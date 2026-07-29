import json
import os
import platform
import shutil
import socket
import struct
from datetime import datetime
from pathlib import Path

HOST = "0.0.0.0"
PORT = 5001

BUFFER_SIZE = 64 * 1024
SERVER_FILES = Path("server_files")
SERVER_FILES.mkdir(exist_ok=True)


def receive_exact(connection: socket.socket, byte_count: int) -> bytes:
    """Receive exactly byte_count bytes from a socket."""
    data = bytearray()

    while len(data) < byte_count:
        chunk = connection.recv(min(BUFFER_SIZE, byte_count - len(data)))

        if not chunk:
            raise ConnectionError("Connection closed before all data arrived.")

        data.extend(chunk)

    return bytes(data)


def send_header(connection: socket.socket, header: dict) -> None:
    """Send a JSON header prefixed by a 4-byte header length."""
    encoded_header = json.dumps(header).encode("utf-8")

    connection.sendall(struct.pack("!I", len(encoded_header)))
    connection.sendall(encoded_header)


def receive_header(connection: socket.socket) -> dict:
    """Receive a length-prefixed JSON header."""
    header_length_data = receive_exact(connection, 4)
    header_length = struct.unpack("!I", header_length_data)[0]

    encoded_header = receive_exact(connection, header_length)
    return json.loads(encoded_header.decode("utf-8"))


def safe_server_path(filename: str) -> Path:
    """
    Return a safe path inside server_files.
    Path(filename).name removes directory components.
    """
    safe_name = Path(filename).name

    if not safe_name:
        raise ValueError("Invalid filename.")

    return SERVER_FILES / safe_name


def send_response(
    connection: socket.socket,
    success: bool,
    message: str,
    **extra,
) -> None:
    response = {
        "type": "response",
        "success": success,
        "message": message,
        **extra,
    }

    send_header(connection, response)


def handle_system_command(command: str) -> str:
    command = command.strip().lower()

    if command == "hostname":
        return socket.gethostname()

    if command == "ip":
        return socket.gethostbyname(socket.gethostname())

    if command == "os":
        return platform.platform()

    if command == "processor":
        return platform.processor() or "Processor information unavailable"

    if command == "storage":
        total, used, free = shutil.disk_usage(Path.cwd().anchor or "/")
        gb = 1024**3

        return (
            f"Total storage: {total / gb:.2f} GB\n"
            f"Used storage: {used / gb:.2f} GB\n"
            f"Free storage: {free / gb:.2f} GB"
        )

    if command == "time":
        return datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")

    if command == "current-directory":
        return os.getcwd()

    return "Unknown command."


def receive_upload(connection: socket.socket, header: dict) -> None:
    filename = header.get("filename", "")
    file_size = header.get("size", 0)

    try:
        file_size = int(file_size)

        if file_size < 0:
            raise ValueError("Invalid file size.")

        destination = safe_server_path(filename)

        with destination.open("wb") as file:
            remaining = file_size

            while remaining > 0:
                chunk = connection.recv(min(BUFFER_SIZE, remaining))

                if not chunk:
                    raise ConnectionError(
                        "Connection closed during file upload."
                    )

                file.write(chunk)
                remaining -= len(chunk)

        send_response(
            connection,
            True,
            f"Uploaded {destination.name} successfully.",
        )

        print(
            f"Received file: {destination.name} "
            f"({file_size:,} bytes)"
        )

    except (ValueError, OSError, ConnectionError) as error:
        send_response(connection, False, f"Upload failed: {error}")


def send_download(connection: socket.socket, header: dict) -> None:
    filename = header.get("filename", "")

    try:
        source = safe_server_path(filename)

        if not source.is_file():
            send_response(
                connection,
                False,
                f"File '{source.name}' was not found.",
            )
            return

        file_size = source.stat().st_size

        send_header(
            connection,
            {
                "type": "file",
                "success": True,
                "filename": source.name,
                "size": file_size,
            },
        )

        with source.open("rb") as file:
            while chunk := file.read(BUFFER_SIZE):
                connection.sendall(chunk)

        print(f"Sent file: {source.name} ({file_size:,} bytes)")

    except (ValueError, OSError, ConnectionError) as error:
        send_response(connection, False, f"Download failed: {error}")


def list_files(connection: socket.socket) -> None:
    files = []

    for path in sorted(SERVER_FILES.iterdir()):
        if path.is_file():
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                }
            )

    send_header(
        connection,
        {
            "type": "file_list",
            "success": True,
            "files": files,
        },
    )


def delete_file(connection: socket.socket, header: dict) -> None:
    filename = header.get("filename", "")

    try:
        path = safe_server_path(filename)

        if not path.is_file():
            send_response(
                connection,
                False,
                f"File '{path.name}' was not found.",
            )
            return

        path.unlink()

        send_response(
            connection,
            True,
            f"Deleted {path.name}.",
        )

    except (ValueError, OSError) as error:
        send_response(connection, False, f"Delete failed: {error}")


def handle_client(
    connection: socket.socket,
    client_address: tuple,
) -> None:
    print(f"Client connected from {client_address}")

    try:
        while True:
            header = receive_header(connection)
            message_type = header.get("type")

            if message_type == "command":
                command = header.get("command", "").strip()

                if command.lower() == "exit":
                    send_response(
                        connection,
                        True,
                        "Closing connection.",
                    )
                    break

                result = handle_system_command(command)
                send_response(connection, True, result)

            elif message_type == "upload":
                receive_upload(connection, header)

            elif message_type == "download":
                send_download(connection, header)

            elif message_type == "list":
                list_files(connection)

            elif message_type == "delete":
                delete_file(connection, header)

            else:
                send_response(
                    connection,
                    False,
                    "Unknown request type.",
                )

    except (
        ConnectionError,
        ConnectionResetError,
        json.JSONDecodeError,
        struct.error,
    ) as error:
        print(f"Client connection error: {error}")

    finally:
        connection.close()
        print(f"Client disconnected: {client_address}")


def main() -> None:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        server_socket.bind((HOST, PORT))
        server_socket.listen()

        print(f"Server listening on port {PORT}")
        print(f"Shared directory: {SERVER_FILES.resolve()}")

        while True:
            connection, client_address = server_socket.accept()
            handle_client(connection, client_address)

    except KeyboardInterrupt:
        print("\nServer stopped.")

    except OSError as error:
        print(f"Server error: {error}")

    finally:
        server_socket.close()


if __name__ == "__main__":
    main()