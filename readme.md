# Overview

This project demonstrates basic networking concepts using a client-server application. The client can request system information, upload and download files, view files on the server, and delete files.

[Software Demo Video] - https://youtu.be/toa9cgkp4nw

# Network Communication

This application uses **TCP** in a client-server architecture. The client connects to the server and sends requests using JSON headers, and TCP ensures that messages and files are delivered reliably and in the correct order.

# Development Environment

This project was developed using:

* Python 3
* Visual Studio Code
* Oracle VirtualBox for VM

The application uses Python's standard library, including `socket`, `json`, `struct`, `pathlib`, `platform`, `datetime`, and `shutil`.

# Useful Websites

* Python Socket Documentation – https://docs.python.org/3/library/socket.html
* Python Documentation – https://docs.python.org/3/
* Real Python Socket Tutorial – https://realpython.com/python-sockets/

# Future Work

* Support multiple clients at the same time.
* Encrypt communication with SSL/TLS.
