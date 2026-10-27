#!/usr/bin/env python3
"""Relay the private Docker network to the loopback-only frontend."""
from __future__ import annotations

import select
import socket
import socketserver
import threading
import time


LISTEN = ("172.19.0.1", 13298)
TARGET = ("127.0.0.1", 3298)
CHUNK_SIZE = 65536
IO_TIMEOUT_SECONDS = 10.0
IDLE_TIMEOUT_SECONDS = 180.0


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        last_activity = time.monotonic()
        try:
            with socket.create_connection(TARGET, timeout=IO_TIMEOUT_SECONDS) as upstream:
                peers = (self.request, upstream)
                while True:
                    # This opaque relay cannot distinguish SSE from ordinary
                    # HTTP. The frontend owns protocol-specific total limits;
                    # active heartbeats keep long-lived streams open here.
                    remaining = last_activity + IDLE_TIMEOUT_SECONDS - time.monotonic()
                    if remaining <= 0:
                        return
                    readable, _, failed = select.select(peers, [], peers, remaining)
                    if failed:
                        return
                    for source in readable:
                        # One chunk in one handler thread; no growing queues or
                        # pump threads. A peer EOF closes both sides, including
                        # a client cancellation while upstream is idle.
                        payload = source.recv(CHUNK_SIZE)
                        if not payload:
                            return
                        destination = upstream if source is self.request else self.request
                        remaining = last_activity + IDLE_TIMEOUT_SECONDS - time.monotonic()
                        if remaining <= 0:
                            return
                        destination.settimeout(min(IO_TIMEOUT_SECONDS, remaining))
                        destination.sendall(payload)
                        last_activity = time.monotonic()
        except OSError:
            # A failed frontend connection is local to this request. Keep the
            # listener available for the frontend's next successful restart.
            pass


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16
    max_connections = 32

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Acquire before ThreadingMixIn creates a thread. The kernel backlog is
        # bounded separately; overload never creates a Python waiting queue.
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


if __name__ == "__main__":
    with RelayServer(LISTEN, RelayHandler) as server:
        server.serve_forever()
