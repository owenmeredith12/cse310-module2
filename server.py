
import json
import os
import platform
import shutil
import socket
import struct
from datetime import datetime
from pathlib import Path


#the server listens on every network adapter on this computer.
HOST = "0.0.0.0"

#client has to use this same port number when connecting.
PORT = 5001


# iles transferred in chunks instead of loading an entire file into memory.
# 64 KB is large enough to be efficient 
BUFFER_SIZE = 64 * 1024


# This is where uploaded files will be stored on the server.
SERVER_FILES = Path("server_files")

# Create the folder automatically if it does not already exist.
SERVER_FILES.mkdir(exist_ok=True)


def receive_exact(connection: socket.socket, byte_count: int) -> bytes:
    """
    Receive an exact number of bytes from the socket.

    TCP does not guarantee that one recv() call will return everything we ask
    for, so this keeps receiving data until the full amount has arrived.
    """
    data = bytearray()

    while len(data) < byte_count:
        # only ask for the number of bytes we still need.
        chunk = connection.recv(
            min(BUFFER_SIZE, byte_count - len(data))
        )

        # an empty chunk means the other side closed the connection.
        if not chunk:
            raise ConnectionError(
                "Connection closed before all data arrived."
            )

        data.extend(chunk)

    return bytes(data)


def send_header(connection: socket.socket, header: dict) -> None:
    """
    Send a JSON header to the client.

    The first four bytes tell the client how long the JSON header is.
    After that, the actual JSON data is sent.
    """
    encoded_header = json.dumps(header).encode("utf-8")

    # pack the header length as a four-byte unsigned integer.
    connection.sendall(
        struct.pack("!I", len(encoded_header))
    )

    # send the actual JSON header after its length.
    connection.sendall(encoded_header)


def receive_header(connection: socket.socket) -> dict:
    """
    Receive a length-prefixed JSON header from the client.
    """
    # she first four bytes contain the size of the JSON header.
    header_length_data = receive_exact(connection, 4)

    # convert those four bytes back into an integer.
    header_length = struct.unpack(
        "!I",
        header_length_data,
    )[0]

    # now that we know the size, receive the full JSON header.
    encoded_header = receive_exact(
        connection,
        header_length,
    )

    # decode the bytes into text, then convert the JSON into a dictionary.
    return json.loads(
        encoded_header.decode("utf-8")
    )


def safe_server_path(filename: str) -> Path:
    """
    Create a safe file path inside the server_files folder.

    Path(filename).name removes any folder information from the filename.
    This keeps a client from trying to access files outside server_files.
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
    """
    Send a normal response back to the client.

    The extra values let other functions include additional information
    without needing a completely separate response format.
    """
    response = {
        "type": "response",
        "success": success,
        "message": message,
        **extra,
    }

    send_header(connection, response)


def handle_system_command(command: str) -> str:
    """
    Run one of the supported system-information commands.

    Everything is returned as text so the client can display it directly.
    """
    command = command.strip().lower()

    if command == "hostname":
        return socket.gethostname()

    if command == "ip":
        # this gets the IP address associated with the computer's hostname.
        return socket.gethostbyname(
            socket.gethostname()
        )

    if command == "os":
        # platform.platform() gives a fairly detailed operating system string.
        return platform.platform()

    if command == "processor":
        # some systems may not return processor information, so theres a fall back message
        return (
            platform.processor()
            or "Processor information unavailable"
        )

    if command == "storage":
        # path.cwd().anchor normally gives the drive root on Windows
        total, used, free = shutil.disk_usage(
            Path.cwd().anchor or "/"
        )

        gb = 1024**3

        return (
            f"Total storage: {total / gb:.2f} GB\n"
            f"Used storage: {used / gb:.2f} GB\n"
            f"Free storage: {free / gb:.2f} GB"
        )

    if command == "time":
        return datetime.now().strftime(
            "%Y-%m-%d %I:%M:%S %p"
        )

    if command == "current-directory":
        return os.getcwd()

    return "Unknown command."


def receive_upload(
    connection: socket.socket,
    header: dict,
) -> None:
    """
    Receive a file uploaded by the client and save it in server_files.
    """
    filename = header.get("filename", "")
    file_size = header.get("size", 0)

    try:
        # json values are not guaranteed to already be integers convert file before using
        file_size = int(file_size)

        if file_size < 0:
            raise ValueError("Invalid file size.")

        destination = safe_server_path(filename)

        # open the destination in binary write mode, this works for most file types
        with destination.open("wb") as file:
            remaining = file_size

            while remaining > 0:
                # only receive as much data as is still needed.
                chunk = connection.recv(
                    min(BUFFER_SIZE, remaining)
                )

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

    except (
        ValueError,
        OSError,
        ConnectionError,
    ) as error:
        # let the client know that the upload failed instead of silently closing connection
        send_response(
            connection,
            False,
            f"Upload failed: {error}",
        )


def send_download(
    connection: socket.socket,
    header: dict,
) -> None:
    """
    Send a requested file from server_files to the client.
    """
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

        # send the file information first so the client knows what is coming.
        send_header(
            connection,
            {
                "type": "file",
                "success": True,
                "filename": source.name,
                "size": file_size,
            },
        )

        # streaming the file in chunks so large files to avoid excessive memory usage
        with source.open("rb") as file:
            while chunk := file.read(BUFFER_SIZE):
                connection.sendall(chunk)

        print(
            f"Sent file: {source.name} "
            f"({file_size:,} bytes)"
        )

    except (
        ValueError,
        OSError,
        ConnectionError,
    ) as error:
        send_response(
            connection,
            False,
            f"Download failed: {error}",
        )


def list_files(connection: socket.socket) -> None:
    """
    Build and send a list of files currently stored on the server.
    """
    files = []

    # sorting makes the file list easier to read on the client side.
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


def delete_file(
    connection: socket.socket,
    header: dict,
) -> None:
    """
    Delete a requested file from the server_files folder.
    """
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

        print(f"Deleted file: {path.name}")

    except (
        ValueError,
        OSError,
    ) as error:
        send_response(
            connection,
            False,
            f"Delete failed: {error}",
        )


def handle_client(
    connection: socket.socket,
    client_address: tuple,
) -> None:
    """
    Handle requests from one connected client.

    This client stays connected and can send multiple commands until it
    sends the exit command or disconnects unexpectedly.
    """
    print(
        f"Client connected from "
        f"{client_address[0]}:{client_address[1]}"
    )

    try:
        while True:
            # every client request starts with a JSON header.
            header = receive_header(connection)

            # the type tells the server which action the client wants.
            message_type = header.get("type")

            if message_type == "command":
                command = header.get(
                    "command",
                    "",
                ).strip()

                if command.lower() == "exit":
                    send_response(
                        connection,
                        True,
                        "Closing connection.",
                    )
                    break

                result = handle_system_command(command)

                send_response(
                    connection,
                    True,
                    result,
                )

            elif message_type == "upload":
                receive_upload(
                    connection,
                    header,
                )

            elif message_type == "download":
                send_download(
                    connection,
                    header,
                )

            elif message_type == "list":
                list_files(connection)

            elif message_type == "delete":
                delete_file(
                    connection,
                    header,
                )

            else:
                # This catches anything the server does not recognize.
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
        print(
            f"Client connection error: {error}"
        )

    finally:
        # always close the client socket, even if something goes wrong.
        connection.close()

        print(
            f"Client disconnected: "
            f"{client_address[0]}:{client_address[1]}"
        )


def main() -> None:
    """
    Create the server socket and keep accepting client connections.
    """
    server_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )

    try:
        # this makes it easier to restart the server
        server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        # bind the server to the selected address and port.
        server_socket.bind(
            (
                HOST,
                PORT,
            )
        )

        # start listening for incoming TCP connections.
        server_socket.listen()

        print(
            f"Server listening on "
            f"{HOST}:{PORT}"
        )

        print(
            f"Shared directory: "
            f"{SERVER_FILES.resolve()}"
        )

        print(
            "Waiting for a client to connect..."
        )

        while True:
            # accept() pauses here until a client connects.
            connection, client_address = (
                server_socket.accept()
            )

            # handle this client until it exits or disconnects.
            handle_client(
                connection,
                client_address,
            )

    except KeyboardInterrupt:
        print("\nServer stopped.")

    except OSError as error:
        print(f"Server error: {error}")

    finally:
        server_socket.close()
        print("Server socket closed.")


if __name__ == "__main__":
    main()

