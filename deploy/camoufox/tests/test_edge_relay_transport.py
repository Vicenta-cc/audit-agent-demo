"""Offline socket contracts; no production sockets or systemd operations."""
import hashlib
import importlib.util
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "edge_under_test", ROOT / "runtime-support/edge_relay.py"
)
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


def receive(sock, length):
    chunks = []
    while length:
        chunk = sock.recv(min(length, 65536))
        if not chunk:
            break
        chunks.append(chunk)
        length -= len(chunk)
    return b"".join(chunks)


class EdgeRelayTransport(unittest.TestCase):
    def setUp(self):
        self.backend = socket.socket()
        self.backend.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.backend.bind(("127.0.0.1", 0))
        self.target = self.backend.getsockname()
        self.backend.listen(40)
        self.backend.settimeout(2)
        self.patches = [patch.object(edge, "TARGET", self.target)]
        self.patches[0].start()
        self.server = edge.RelayServer(("127.0.0.1", 0), edge.RelayHandler)
        self.runner = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.runner.start()
        self.sockets = []

    def tearDown(self):
        for sock in self.sockets:
            sock.close()
        self.backend.close()
        self.server.shutdown()
        self.server.server_close()
        self.runner.join(2)
        for item in reversed(self.patches):
            item.stop()

    def setting(self, name, value):
        item = patch.object(edge, name, value)
        item.start()
        self.patches.append(item)

    def connect(self):
        client = socket.create_connection(self.server.server_address, timeout=2)
        self.sockets.append(client)
        return client

    def accept(self):
        peer, _ = self.backend.accept()
        peer.settimeout(2)
        self.sockets.append(peer)
        return peer

    def test_bidirectional_bytes_preserved(self):
        client = self.connect()
        peer = self.accept()
        payload = bytes(range(256)) * 8192
        failures = []

        def echo():
            try:
                received = receive(peer, len(payload))
                self.assertEqual(hashlib.sha256(received).digest(), hashlib.sha256(payload).digest())
                peer.sendall(received)
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=echo)
        worker.start()
        client.sendall(payload)
        self.assertEqual(receive(client, len(payload)), payload)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(edge.CHUNK_SIZE, 65536)

    def test_cancel_closes_idle_upstream_and_releases_slot(self):
        client = self.connect()
        peer = self.accept()
        client.close()
        self.assertEqual(peer.recv(1), b"")
        # All permits must be returned, not merely the visible socket closed.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            acquired = 0
            while self.server._slots.acquire(blocking=False):
                acquired += 1
            for _ in range(acquired):
                self.server._slots.release()
            if acquired == 32:
                break
            time.sleep(0.01)
        self.assertEqual(acquired, 32)

    def test_upstream_close_closes_client(self):
        client = self.connect()
        peer = self.accept()
        peer.close()
        self.assertEqual(client.recv(1), b"")

    def test_refused_frontend_then_recovery_without_restarting_relay(self):
        self.backend.close()
        client = self.connect()
        self.assertEqual(client.recv(1), b"")
        self.backend = socket.socket()
        self.backend.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.backend.bind(self.target)
        self.backend.listen()
        self.backend.settimeout(2)
        recovered = self.connect()
        peer = self.accept()
        recovered.sendall(b"healthy")
        self.assertEqual(peer.recv(7), b"healthy")
        peer.sendall(b"ready")
        self.assertEqual(recovered.recv(5), b"ready")

    def test_32_active_connections_and_no_overload_handler_thread(self):
        before = {thread.ident for thread in threading.enumerate()}
        for _ in range(32):
            self.connect()
            self.accept()
        excess = self.connect()
        self.assertEqual(excess.recv(1), b"")
        self.backend.settimeout(0.1)
        with self.assertRaises(socket.timeout):
            self.backend.accept()
        handlers = [thread for thread in threading.enumerate()
                    if thread.ident not in before and "process_request_thread" in thread.name]
        self.assertEqual(len(handlers), 32)
        self.assertEqual(self.server.request_queue_size, 16)

    def test_idle_timeout(self):
        self.setting("IDLE_TIMEOUT_SECONDS", 0.12)
        client = self.connect()
        peer = self.accept()
        start = time.monotonic()
        self.assertEqual(client.recv(1), b"")
        self.assertEqual(peer.recv(1), b"")
        self.assertLess(time.monotonic() - start, 0.8)

    def test_heartbeats_keep_stream_open_until_idle(self):
        self.setting("IDLE_TIMEOUT_SECONDS", 0.2)
        client = self.connect()
        peer = self.accept()
        start = time.monotonic()
        exchanged = 0
        while time.monotonic() - start < 0.65:
            client.sendall(b"ping")
            self.assertEqual(receive(peer, 4), b"ping")
            peer.sendall(b"pong")
            self.assertEqual(receive(client, 4), b"pong")
            exchanged += 1
            time.sleep(0.02)
        self.assertGreater(exchanged, 2)
        self.assertGreater(time.monotonic() - start, 3 * edge.IDLE_TIMEOUT_SECONDS)
        stopped = time.monotonic()
        self.assertEqual(client.recv(1), b"")
        self.assertEqual(peer.recv(1), b"")
        self.assertLess(time.monotonic() - stopped, 0.8)

    def test_slow_reader_releases_upstream(self):
        self.setting("IO_TIMEOUT_SECONDS", 0.12)
        client = self.connect()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        peer = self.accept()
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        stopped = threading.Event()

        def send():
            try:
                for _ in range(1024):
                    peer.sendall(b"x" * 65536)
            except OSError:
                stopped.set()

        worker = threading.Thread(target=send, daemon=True)
        worker.start()
        self.assertTrue(stopped.wait(1.5), "stalled client must not retain upstream")
        worker.join(1)
        self.assertFalse(worker.is_alive())

    def test_review_unit_has_weak_dependency_and_original_target(self):
        text = (ROOT / "systemd/xhs-audit-multi-user-edge.service").read_text()
        self.assertIn("Wants=content-audit-production-frontend.service\n", text)
        self.assertNotIn("Requires=", text)
        self.assertIn("After=docker.service content-audit-production-frontend.service\n", text)
        self.assertIn("ExecStart=/usr/bin/python3 /mnt/datadisk0/xhs-audit-agent-multi-user-20260920/edge_relay.py\n", text)
        self.assertEqual(edge.LISTEN, ("172.19.0.1", 13298))
        self.assertEqual(edge.IDLE_TIMEOUT_SECONDS, 180)


if __name__ == "__main__":
    unittest.main()
