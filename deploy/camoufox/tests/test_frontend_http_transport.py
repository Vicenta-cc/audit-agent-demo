"""Synthetic loopback HTTP transfers; no application data or external requests."""

import http.client
import importlib.util
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


class FrontendHTTPTransport(unittest.TestCase):
    def setUp(self):
        self.backend = socket.socket()
        self.backend.bind(("127.0.0.1", 0))
        self.backend.listen()
        self.backend.settimeout(2)
        config = manifest(Path("/fixture"))
        config["ports"].update(backend=self.backend.getsockname()[1], frontend=18301, allowed=[self.backend.getsockname()[1], 18301])
        source = Path(__file__).resolve().parents[1] / "runtime-support/frontend.py"
        spec = importlib.util.spec_from_file_location("frontend_http_under_test", source)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"CAMOUFOX_RELEASE_MANIFEST": "/fixture/manifest.json"}), patch.object(control, "read_json", return_value=config), patch.object(control, "check_artifacts"):
            spec.loader.exec_module(self.module)
        self.module.HTTP_IO_TIMEOUT_SECONDS = 1.0
        self.server = self.module.ThreadingHTTPServer(("127.0.0.1", 0), self.module.CandidateFrontendHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.clients = []
        self.workers = []
        self.errors = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.backend.close()
        self.thread.join(2)
        for worker in self.workers:
            worker.join(2)
        self.assertEqual(self.errors, [], "synthetic upstream failed")

    def backend_run(self, callback):
        def run():
            try:
                conn, _ = self.backend.accept()
                with conn:
                    conn.settimeout(2)
                    stream = conn.makefile("rb")
                    request = stream.readline()
                    headers = http.client.parse_headers(stream)
                    callback(conn, stream, request, headers)
                    stream.close()
            except BaseException as exc:
                self.errors.append(exc)
        worker = threading.Thread(target=run, daemon=True)
        self.workers.append(worker)
        worker.start()

    def connect(self, request=b"GET /api/media HTTP/1.1\r\nHost: fixture\r\n\r\n"):
        client = socket.create_connection(self.server.server_address, timeout=0.5)
        self.clients.append(client)
        client.sendall(request)
        return client

    def response(self, client, method="GET"):
        response = http.client.HTTPResponse(client, method=method)
        response.begin()
        return response

    def test_response_first_chunk_arrives_before_upstream_finishes(self):
        release = threading.Event()
        def upstream(conn, *_):
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\npart")
            release.wait(1)
            conn.sendall(b"last")
        self.backend_run(upstream)
        try:
            response = self.response(self.connect())
            self.assertEqual(response.read(4), b"part")
        finally:
            release.set()
        self.assertEqual(response.read(), b"last")

    def test_head_preserves_representation_length(self):
        self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 268435456\r\n\r\n"))
        response = self.response(self.connect(b"HEAD /api/media HTTP/1.1\r\nHost: fixture\r\n\r\n"), "HEAD")
        self.assertEqual(response.getheader("Content-Length"), "268435456")
        self.assertEqual(response.read(), b"")

    def test_request_first_chunk_reaches_upstream_before_body_finishes(self):
        first = threading.Event()
        def upstream(conn, stream, request, headers):
            self.assertEqual(headers["Content-Length"], "8")
            self.assertEqual(stream.read(4), b"part")
            first.set()
            self.assertEqual(stream.read(4), b"last")
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        self.backend_run(upstream)
        client = self.connect(b"POST /api/media HTTP/1.1\r\nHost: fixture\r\nContent-Length: 8\r\n\r\npart")
        try:
            self.assertTrue(first.wait(0.5), "proxy buffered the complete request body")
        finally:
            client.sendall(b"last")
        self.assertEqual(self.response(client).read(), b"ok")

    def test_cancel_while_upstream_is_silent_closes_upstream(self):
        connected = threading.Event()
        closed = threading.Event()
        def upstream(conn, *_):
            connected.set()
            if conn.recv(1) == b"":
                closed.set()
        self.backend_run(upstream)
        client = self.connect()
        self.assertTrue(connected.wait(0.5))
        client.close()
        self.assertTrue(closed.wait(0.5), "cancel must interrupt a pending upstream response")

    def test_range_and_unsatisfiable_range_headers_are_preserved(self):
        for status, content_range, body in [(206, "bytes 4-7/16", b"4567"), (416, "bytes */16", b"")]:
            with self.subTest(status=status):
                def upstream(conn, stream, request, headers):
                    self.assertEqual(headers["Range"], "bytes=4-7")
                    conn.sendall(f"HTTP/1.1 {status} Result\r\nContent-Length: {len(body)}\r\nContent-Range: {content_range}\r\nAccept-Ranges: bytes\r\n\r\n".encode() + body)
                self.backend_run(upstream)
                response = self.response(self.connect(b"GET /api/media HTTP/1.1\r\nHost: fixture\r\nRange: bytes=4-7\r\n\r\n"))
                self.assertEqual(response.status, status)
                self.assertEqual(response.getheader("Content-Range"), content_range)
                self.assertEqual(response.read(), body)
        self.assertEqual(self.errors, [])

    def test_chunked_and_unknown_length_responses_are_eof_framed(self):
        for wire in [b"Transfer-Encoding: chunked\r\n\r\n4\r\ndata\r\n0\r\n\r\n", b"Connection: close\r\n\r\ndata"]:
            with self.subTest(wire=wire):
                self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 200 OK\r\n" + wire))
                response = self.response(self.connect())
                self.assertEqual(response.getheader("Connection"), "close")
                self.assertIsNone(response.getheader("Transfer-Encoding"))
                self.assertIsNone(response.getheader("Content-Length"))
                self.assertEqual(response.read(), b"data")

    def test_redirect_is_passed_through_without_replaying_request(self):
        self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 307 Temporary Redirect\r\nLocation: /other\r\nContent-Length: 0\r\n\r\n"))
        response = self.response(self.connect())
        self.assertEqual(response.status, 307)
        self.assertEqual(response.getheader("Location"), "/other")

    def test_known_length_keepalive_supports_a_second_request(self):
        request = b"GET /api/media HTTP/1.1\r\nHost: fixture\r\n\r\n"
        self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 404 Missing\r\nContent-Length: 4\r\n\r\ngone"))
        client = self.connect(request)
        first = self.response(client)
        self.assertEqual(first.status, 404)
        self.assertEqual(first.read(), b"gone")
        self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"))
        client.sendall(request)
        self.assertEqual(self.response(client).read(), b"ok")

    def test_truncated_response_is_not_reused_or_reframed_as_success(self):
        self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nshort"))
        response = self.response(self.connect())
        self.assertEqual(response.getheader("Content-Length"), "8")
        with self.assertRaises(http.client.IncompleteRead):
            response.read()

    def test_ambiguous_or_chunked_request_is_rejected(self):
        for headers, status in [(b"Transfer-Encoding: chunked\r\n", 501), (b"Content-Length: 1\r\nContent-Length: 2\r\n", 400), (b"Content-Length: -1\r\n", 400)]:
            with self.subTest(headers=headers):
                response = self.response(self.connect(b"POST /api/media HTTP/1.1\r\nHost: fixture\r\n" + headers + b"\r\n"))
                self.assertEqual(response.status, status)
                response.read()
        self.backend.settimeout(0.05)
        with self.assertRaises(socket.timeout):
            self.backend.accept()

    def test_full_connection_limit_rejects_without_spawning_and_recovers(self):
        # The cap includes clients that have not sent their first HTTP byte.
        held = []
        for expected_active in range(1, self.module.MAX_ACTIVE_CONNECTIONS + 1):
            client = socket.create_connection(self.server.server_address, timeout=1)
            self.clients.append(client)
            held.append(client)
            # A connected socket may still be in the listen backlog. Wait for
            # accept before adding another, so this tests active capacity.
            deadline = time.monotonic() + 0.5
            while len(self.server._active) < expected_active and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(len(self.server._active), expected_active)
        deadline = time.monotonic() + 0.5
        while len(self.server._active) < self.module.MAX_ACTIVE_CONNECTIONS and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.server._active), self.module.MAX_ACTIVE_CONNECTIONS)
        response = self.response(self.connect())
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.server._active), self.module.MAX_ACTIVE_CONNECTIONS)
        for client in held:
            client.close()
        self.wait_for_no_active()
        self.backend_run(lambda conn, *_: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"))
        self.assertEqual(self.response(self.connect()).read(), b"ok")

    def wait_for_no_active(self, timeout=1):
        deadline = time.monotonic() + timeout
        while self.server._active and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.server._active), 0)

    def test_slow_request_body_idle_timeout_releases_slot(self):
        self.module.HTTP_IO_TIMEOUT_SECONDS = 0.1
        closed = threading.Event()
        def upstream(conn, stream, *_):
            if stream.read(8) == b"part":
                closed.set()
        self.backend_run(upstream)
        client = self.connect(b"POST /api/media HTTP/1.1\r\nHost: fixture\r\nContent-Length: 8\r\n\r\npart")
        self.assertEqual(self.response(client).status, 502)
        self.assertTrue(closed.wait(0.5))
        self.wait_for_no_active()

    def test_idle_client_without_headers_releases_slot(self):
        self.module.HTTP_IO_TIMEOUT_SECONDS = 0.1
        client = socket.create_connection(self.server.server_address, timeout=0.5)
        self.clients.append(client)
        self.assertEqual(client.recv(1), b"")
        self.wait_for_no_active()

    def test_slow_downstream_releases_upstream_and_slot(self):
        self.module.HTTP_IO_TIMEOUT_SECONDS = 0.1
        closed = threading.Event()
        def upstream(conn, *_):
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 33554432\r\n\r\n")
                for _ in range(512):
                    conn.sendall(b"x" * 65536)
            except OSError:
                closed.set()
        self.backend_run(upstream)
        client = socket.socket()
        self.clients.append(client)
        client.settimeout(0.5)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        client.connect(self.server.server_address)
        client.sendall(b"GET /api/media HTTP/1.1\r\nHost: fixture\r\n\r\n")
        response = self.response(client)
        self.assertEqual(response.status, 200)
        self.wait_for_no_active()
        self.assertTrue(closed.wait(0.5))

    def test_total_deadline_closes_even_an_active_trickle(self):
        self.module.HTTP_MAX_SECONDS = 0.12
        stopped = threading.Event()
        def upstream(conn, *_):
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n")
                for _ in range(100):
                    conn.sendall(b"x")
                    time.sleep(0.02)
            except OSError:
                stopped.set()
        self.backend_run(upstream)
        response = self.response(self.connect())
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        self.assertTrue(stopped.wait(0.5))
        self.wait_for_no_active()

    def test_sse_heartbeats_are_not_cut_by_normal_http_total_deadline(self):
        self.module.HTTP_MAX_SECONDS = 0.1
        def upstream(conn, *_):
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            for _ in range(4):
                conn.sendall(b": ping\n\n")
                time.sleep(0.06)
        self.backend_run(upstream)
        response = self.response(self.connect())
        self.assertEqual(response.getheader("X-Accel-Buffering"), "no")
        self.assertEqual(response.read(), b": ping\n\n" * 4)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
