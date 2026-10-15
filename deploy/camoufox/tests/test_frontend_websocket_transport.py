"""Real loopback sockets prove HTTP upgrade and bidirectional bounded transport."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control
from test_control import manifest


def read_headers(sock):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def frame(payload, masked=False):
    mask = b"abcd"
    prefix = bytes([0x82, (0x80 if masked else 0) | len(payload)])
    return (
        prefix + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if masked
        else prefix + payload
    )


class FrontendWebsocketTransport(unittest.TestCase):
    def setUp(self):
        self.backend = socket.socket()
        self.backend.bind(("127.0.0.1", 0))
        self.backend.listen()
        self.backend.settimeout(3)
        self.backend_port = self.backend.getsockname()[1]
        self.handshake = None
        self.backend_errors = []
        self.done = threading.Event()
        config = manifest(Path("/fixture"))
        config["ports"].update(
            backend=self.backend_port,
            frontend=18301,
            allowed=[self.backend_port, 18301],
        )
        source = Path(__file__).resolve().parents[1] / "runtime-support/frontend.py"
        spec = importlib.util.spec_from_file_location(
            "frontend_transport_under_test", source
        )
        self.module = importlib.util.module_from_spec(spec)
        # Startup manifest/artifact behavior has separate tests; this tests transport
        # using an explicit structurally valid fixture, not a production receipt.
        with (
            patch.dict(
                os.environ, {"CAMOUFOX_RELEASE_MANIFEST": "/fixture/manifest.json"}
            ),
            patch.object(control, "read_json", return_value=config),
            patch.object(control, "check_artifacts"),
        ):
            spec.loader.exec_module(self.module)
        self.server = self.module.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.module.CandidateFrontendHandler
        )
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.backend.close()

    def backend_run(self, callback):
        def worker():
            try:
                connection, _ = self.backend.accept()
                with connection:
                    connection.settimeout(3)
                    self.handshake = read_headers(connection)
                    callback(connection)
            except BaseException as error:
                self.backend_errors.append(error)
            finally:
                self.done.set()

        threading.Thread(target=worker, daemon=True).start()

    def connect(
        self,
        path="/api/crawler-account-login-sessions/session_1/stream?ticket=SECRET_CAPABILITY",
        extra="",
    ):
        self.client = socket.create_connection(self.server.server_address, timeout=3)
        self.client.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: app.example\r\nConnection: keep-alive, Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: YWJjZGVmZ2hpamtsbW5vcA==\r\nOrigin: https://app.example\r\nCookie: session=PRIVATE_COOKIE\r\n{extra}\r\n"
            ).encode()
        )
        return self.client

    def upgrade(self, connection):
        connection.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: fixture-accept\r\n\r\n"
        )

    def test_101_binary_frames_both_directions_and_origin_cookie_forwarding(self):
        received = []

        def backend(connection):
            self.upgrade(connection)
            connection.sendall(frame(b"from-server"))
            received.append(connection.recv(1024))

        self.backend_run(backend)
        client = self.connect()
        response = read_headers(client)
        self.assertIn(b"101 Switching Protocols", response)
        expected = frame(b"from-server")
        if expected not in response:
            self.assertEqual(client.recv(1024), expected)
        outbound = frame(b"from-client", masked=True)
        client.sendall(outbound)
        self.assertTrue(self.done.wait(3))
        self.assertEqual(received, [outbound])
        self.assertIn(b"Origin: https://app.example", self.handshake)
        self.assertIn(b"Cookie: session=PRIVATE_COOKIE", self.handshake)
        self.assertIn(b"?ticket=SECRET_CAPABILITY", self.handshake)
        self.assertEqual(self.backend_errors, [])

    def test_401_auth_body_not_truncated(self):
        body = b"unauthorized-" + b"x" * 24000

        def backend(connection):
            connection.sendall(
                b"HTTP/1.1 401 Unauthorized\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
            )
            connection.sendall(body)

        self.backend_run(backend)
        client = self.connect()
        response = bytearray()
        while chunk := client.recv(4096):
            response.extend(chunk)
        self.assertIn(b"401 Unauthorized", response)
        self.assertEqual(bytes(response).split(b"\r\n\r\n")[1], body)

    def test_wrong_path_and_body_are_rejected_without_backend_connection(self):
        client = self.connect(path="/api/arbitrary/stream")
        response = read_headers(client)
        self.assertIn(b"400", response.split(b"\r\n")[0])
        with self.assertRaises(socket.timeout):
            self.backend.settimeout(0.05)
            self.backend.accept()

    def test_origin_denial_is_forwarded_without_rewriting_origin(self):
        def backend(connection):
            self.assertIn(b"Origin: https://app.example", self.handshake)
            connection.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")

        self.backend_run(backend)
        client = self.connect()
        self.assertIn(b"403 Forbidden", read_headers(client))
        self.assertTrue(self.done.wait(2))
        self.assertEqual(self.backend_errors, [])

    def test_client_disconnect_closes_backend_socket(self):
        closed = []

        def backend(connection):
            self.upgrade(connection)
            closed.append(connection.recv(1))

        self.backend_run(backend)
        client = self.connect()
        self.assertIn(b"101", read_headers(client))
        client.close()
        self.client = None
        self.assertTrue(self.done.wait(2))
        self.assertEqual(closed, [b""])

    def test_upgrade_with_body_is_rejected_without_backend_connection(self):
        client = self.connect(extra="Content-Length: 1\r\n")
        self.assertIn(b"400", read_headers(client).split(b"\r\n")[0])
        with self.assertRaises(socket.timeout):
            self.backend.settimeout(0.05)
            self.backend.accept()

    def test_upstream_disconnect_terminates_client(self):
        self.backend_run(lambda connection: self.upgrade(connection))
        client = self.connect()
        self.assertIn(b"101", read_headers(client))
        self.assertEqual(client.recv(1), b"")

    def test_ticket_never_appears_in_error_or_access_logs(self):
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs):
            client = self.connect(path="/bad?ticket=SECRET_CAPABILITY")
            read_headers(client)
            self.done.wait(0.05)
        self.assertNotIn("SECRET_CAPABILITY", logs.getvalue())

    def test_slow_client_is_bounded_by_send_timeout(self):
        self.assertEqual(self.module.STREAM_IO_TIMEOUT_SECONDS, 10)
        self.module.STREAM_IO_TIMEOUT_SECONDS = 0.25

        def backend(connection):
            self.upgrade(connection)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            try:
                for _ in range(256):
                    connection.sendall(b"x" * 65536)
            except (OSError, TimeoutError):
                pass

        self.backend_run(backend)
        client = self.connect()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        start = time.monotonic()
        self.assertIn(b"101", read_headers(client))
        self.assertTrue(
            self.done.wait(1.5),
            "relay must close stalled peer instead of buffering without bound",
        )
        self.assertLess(time.monotonic() - start, 1.5)


if __name__ == "__main__":
    unittest.main()
