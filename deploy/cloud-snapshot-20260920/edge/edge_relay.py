#!/usr/bin/env python3
"""Relay the edge proxy's private Docker network to the loopback-only frontend."""
from __future__ import annotations

import socket
import socketserver
import threading


LISTEN = ("172.19.0.1", 13198)
TARGET = ("127.0.0.1", 3198)


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        upstream = socket.create_connection(TARGET, timeout=10)
        upstream.settimeout(None)

        def pump(source: socket.socket, destination: socket.socket) -> None:
            try:
                while True:
                    payload = source.recv(1024 * 1024)
                    if not payload:
                        break
                    destination.sendall(payload)
            finally:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        request_pump = threading.Thread(
            target=pump,
            args=(self.request, upstream),
            daemon=True,
        )
        request_pump.start()
        try:
            pump(upstream, self.request)
        finally:
            request_pump.join(timeout=5)
            upstream.close()


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with RelayServer(LISTEN, RelayHandler) as server:
        server.serve_forever()
