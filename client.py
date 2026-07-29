import json
import socket
import struct
from pathlib import Path

SERVER_HOST = "192.168.86.63"
SERVER_PORT = 5001

BUFFER_SIZE = 64 * 1024
CLIENT_FILES = Path("client_files")
CLIENT_FILES.mkdir(exist_ok=True)


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


def format_size(size: int) -> str:
    value = float(size)

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{size} B"


def upload_file(connection: socket.socket, filename: str) -> None:
    path = Path(filename).expanduser()

    if not path.is_file():
        print(f"File not found: {path}")
        return

    file_size = path.stat().st_size

    send_header(
        connection,
        {
            "type": "upload",
            "filename": path.name,
            "size": file_size,
        },
    )

    sent = 0

    with path.open("rb") as file:
        while chunk := file.read(BUFFER_SIZE):
            connection.sendall(chunk)
            sent += len(chunk)

            percent = 100 if file_size == 0 else sent / file_size * 100

            print(
                f"\rUploading: {percent:6.2f}%",
                end="",
                flush=True,
            )

    print()

    response = receive_header(connection)
    print(response.get("message", "No server response."))


def download_file(connection: socket.socket, filename: str) -> None:
    send_header(
        connection,
        {
            "type": "download",
            "filename": filename,
        },
    )

    response = receive_header(connection)

    if not response.get("success"):
        print(response.get("message", "Download failed."))
        return

    file_size = int(response["size"])
    safe_filename = Path(response["filename"]).name
    destination = CLIENT_FILES / safe_filename

    received = 0

    with destination.open("wb") as file:
        while received < file_size:
            chunk = connection.recv(
                min(BUFFER_SIZE, file_size - received)
            )

            if not chunk:
                raise ConnectionError(
                    "Connection closed during download."
                )

            file.write(chunk)
            received += len(chunk)

            percent = 100 if file_size == 0 else received / file_size * 100

            print(
                f"\rDownloading: {percent:6.2f}%",
                end="",
                flush=True,
            )

    print()
    print(f"Saved to: {destination.resolve()}")


def list_files(connection: socket.socket) -> None:
    send_header(connection, {"type": "list"})
    response = receive_header(connection)

    files = response.get("files", [])

    if not files:
        print("There are no files on the server.")
        return

    print("\nFiles on server:")

    for file_info in files:
        name = file_info["name"]
        size = format_size(file_info["size"])
        print(f"  {name:<35} {size:>12}")


def delete_file(connection: socket.socket, filename: str) -> None:
    send_header(
        connection,
        {
            "type": "delete",
            "filename": filename,
        },
    )

    response = receive_header(connection)
    print(response.get("message", "No server response."))


def run_system_command(
    connection: socket.socket,
    command: str,
) -> None:
    send_header(
        connection,
        {
            "type": "command",
            "command": command,
        },
    )

    response = receive_header(connection)
    print(response.get("message", "No server response."))


def print_help() -> None:
    print(
        """
Available commands:

  help
      Show this menu.

  list
      List files available on the server.

  upload <path>
      Upload a local file to the server.

  download <filename>
      Download a file from the server.

  delete <filename>
      Delete a file from the server.

  hostname
  ip
  os
  processor
  storage
  time
  current-directory
      Request system information from the server.

  exit
      Disconnect from the server.

Examples:

  upload test.txt
  upload "C:\\Users\\Owner\\Documents\\report.pdf"
  download report.pdf
  delete report.pdf
"""
    )


def main() -> None:
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    print("Starting client...")
    print(f"Client files folder: {CLIENT_FILES.resolve()}")
    print(f"Attempting connection to {SERVER_HOST}:{SERVER_PORT}")

    try:
        client_socket.settimeout(10)

        client_socket.connect((SERVER_HOST, SERVER_PORT))

        client_socket.settimeout(None)

        local_ip, local_port = client_socket.getsockname()
        remote_ip, remote_port = client_socket.getpeername()

        print("Connection successful.")
        print(f"Local address:  {local_ip}:{local_port}")
        print(f"Remote address: {remote_ip}:{remote_port}")
        print("Enter 'help' to see available commands.")

        while True:
            user_input = input("\nCommand: ").strip()

            if not user_input:
                continue

            command, *remaining = user_input.split(maxsplit=1)
            command = command.lower()
            argument = remaining[0].strip() if remaining else ""

            if command == "help":
                print_help()

            elif command == "list":
                list_files(client_socket)

            elif command == "upload":
                if not argument:
                    print("Usage: upload <file path>")
                else:
                    upload_file(client_socket, argument.strip('"'))

            elif command == "download":
                if not argument:
                    print("Usage: download <filename>")
                else:
                    download_file(client_socket, argument.strip('"'))

            elif command == "delete":
                if not argument:
                    print("Usage: delete <filename>")
                else:
                    delete_file(client_socket, argument.strip('"'))

            elif command == "exit":
                run_system_command(client_socket, "exit")
                break

            else:
                run_system_command(client_socket, user_input)

    except ConnectionRefusedError as error:
        print("\nConnection refused.")
        print(f"Target: {SERVER_HOST}:{SERVER_PORT}")
        print(f"Details: {error}")
        print(
            "The computer was reachable, but nothing accepted the "
            "connection on that port."
        )

    except socket.timeout as error:
        print("\nConnection timed out.")
        print(f"Target: {SERVER_HOST}:{SERVER_PORT}")
        print(f"Details: {error}")
        print(
            "The server did not respond within 10 seconds."
        )

    except socket.gaierror as error:
        print("\nServer address error.")
        print(f"Address: {SERVER_HOST}")
        print(f"Details: {error}")

    except (
        ConnectionError,
        ConnectionResetError,
        json.JSONDecodeError,
        struct.error,
    ) as error:
        print(f"\nClient error: {type(error).__name__}: {error}")

    except OSError as error:
        print("\nSocket error.")
        print(f"Error number: {error.errno}")
        print(f"Details: {error}")

    finally:
        client_socket.close()
        print("Client socket closed.")


if __name__ == "__main__":
    main()