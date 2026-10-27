"""Serve the separately pinned frontend hotfix artifact and stream API responses."""

from __future__ import annotations

import http.client
import json
import os
import re
import select
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer as _ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import check_artifacts, environment, read_json, validate

CONFIG = validate(read_json(os.environ["CAMOUFOX_RELEASE_MANIFEST"]))
check_artifacts(CONFIG)
environment(
    CONFIG
)  # Validate the App-owner contract without exporting secrets to this process.
DIST = Path(CONFIG["artifacts"]["frontend_dist"]["path"])
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


STREAM_IO_TIMEOUT_SECONDS = 10.0
STREAM_TUNNEL_MAX_SECONDS = 1800.0


TRANSFER_CHUNK_BYTES = 65536
HTTP_IO_TIMEOUT_SECONDS = 180.0
HTTP_MAX_SECONDS = 3600.0
MAX_ACTIVE_CONNECTIONS = 32


def shutdown_socket(connection):
    if connection is not None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class ThreadingHTTPServer(_ThreadingHTTPServer):
    """Bound handlers before spawning; the accept loop also reaps cancelled I/O."""

    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_ACTIVE_CONNECTIONS)
        self._active = {}
        self._active_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            # Never block the accept/watchdog loop on a client that does not read.
            request.setblocking(False)
            try:
                request.send(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\nRetry-After: 1\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        request.settimeout(HTTP_IO_TIMEOUT_SECONDS)
        with self._active_lock:
            self._active[request] = {
                "deadline": time.monotonic() + HTTP_MAX_SECONDS,
                "upstream": None,
                "watch_disconnect": False,
            }
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._active_lock:
                self._active.pop(request, None)
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._active_lock:
                self._active.pop(request, None)
            self._slots.release()

    def service_actions(self):
        # One bounded scan in serve_forever's existing thread, no per-transfer
        # polling threads. Production poll_interval is 500 ms. Hold the lock so
        # a completed handler cannot start reading its next request during peek.
        with self._active_lock:
            now = time.monotonic()
            for client, state in self._active.items():
                expired = state["deadline"] is not None and now >= state["deadline"]
                cancelled = False
                if state["watch_disconnect"]:
                    try:
                        ready, _, _ = select.select([client], [], [], 0)
                        cancelled = bool(ready) and client.recv(1, socket.MSG_PEEK) == b""
                    except (OSError, ValueError):
                        cancelled = True
                if expired or cancelled:
                    shutdown_socket(state["upstream"])
                    shutdown_socket(client)

    def server_close(self):
        with self._active_lock:
            active = list(self._active.items())
        for client, state in active:
            shutdown_socket(state["upstream"])
            shutdown_socket(client)
        super().server_close()


class CandidateFrontendHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def handle_one_request(self):
        # Include request headers and body in the total deadline, not just the
        # upstream response. Keepalive requests each receive their own budget.
        self.server._active[self.connection]["deadline"] = (
            time.monotonic() + HTTP_MAX_SECONDS
        )
        super().handle_one_request()

    def _proxy(self) -> None:
        # Reject ambiguous framing rather than forwarding a different body to
        # the backend. The public ingress sends a length, not chunked requests.
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding"):
            self.send_error(501, "Chunked request bodies are not supported")
            return
        if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
            self.send_error(400, "Invalid Content-Length")
            return
        try:
            length = int(lengths[0]) if lengths else 0
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        state = self.server._active[self.connection]
        state["watch_disconnect"] = not length
        connection = http.client.HTTPConnection(
            CONFIG["ports"]["host"],
            CONFIG["ports"]["backend"],
            timeout=HTTP_IO_TIMEOUT_SECONDS,
        )
        response = None
        response_started = False

        def request_chunks():
            remaining = length
            while remaining:
                chunk = self.rfile.read1(min(remaining, TRANSFER_CHUNK_BYTES))
                if not chunk:
                    raise ConnectionError("Incomplete request body")
                remaining -= len(chunk)
                yield chunk
            # For browser/Nginx HTTP traffic, EOF after a complete request means
            # cancellation. Raw clients using SHUT_WR then awaiting a response
            # are deliberately not supported: TCP cannot distinguish their FIN.
            state["watch_disconnect"] = True

        connection_tokens = {
            token.strip().lower() for token in self.headers.get("Connection", "").split(",")
        }
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP | connection_tokens | {"host"}
        }
        try:
            connection.connect()
            state["upstream"] = connection.sock
            # http.client never follows redirects or replays this one-shot body.
            connection.request(
                self.command,
                self.path,
                body=request_chunks() if length else None,
                headers=headers,
            )
            response = connection.getresponse()
            is_sse = response.headers.get_content_type() == "text/event-stream"
            if is_sse:
                state["deadline"] = None  # Existing 15s heartbeat keeps idle I/O alive.
            has_body = self.command != "HEAD" and response.status not in (204, 304)
            expected = response.length if has_body else 0
            response_tokens = {
                token.strip().lower() for token in response.getheader("Connection", "").split(",")
            }
            excluded = HOP_BY_HOP | response_tokens
            if response.chunked or is_sse:
                excluded |= {"content-length"}
            if is_sse:
                excluded |= {"x-accel-buffering"}
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in excluded:
                    self.send_header(key, value)
            if is_sse:
                self.send_header("X-Accel-Buffering", "no")
            # Dechunked/unknown-length responses use EOF framing. Known lengths
            # remain usable by the outer Nginx proxy_force_ranges filter.
            if is_sse or (has_body and expected is None):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            response_started = True
            received = 0
            if has_body:
                while chunk := response.read1(TRANSFER_CHUNK_BYTES):
                    received += len(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                if expected is not None and received != expected:
                    # Never leave a truncated Content-Length response on a
                    # reusable downstream connection or report a second status.
                    self.close_connection = True
        except (OSError, http.client.HTTPException, ValueError):
            self.close_connection = True
            if not response_started:
                try:
                    payload = json.dumps({"detail": "Backend unavailable"}).encode()
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(payload)
                except OSError:
                    pass
        finally:
            with self.server._active_lock:
                state["watch_disconnect"] = False
                state["upstream"] = None
            if response is not None:
                response.close()
            connection.close()

    def _websocket(self) -> None:
        # Authentication and Origin/ticket validation remain in the backend.
        # Transport is bounded to one 64KiB chunk per direction (no growing queues).
        path = self.path.split("?", 1)[0]
        tokens = {
            token.strip().lower()
            for token in self.headers.get("Connection", "").split(",")
        }
        if (
            not re.fullmatch(
                r"/api/crawler-account-login-sessions/[A-Za-z0-9_-]{1,128}/stream", path
            )
            or self.headers.get("Upgrade", "").lower() != "websocket"
            or "upgrade" not in tokens
            or self.headers.get("Content-Length", "0") != "0"
            or self.headers.get("Transfer-Encoding")
        ):
            self.send_error(400, "Invalid stream upgrade")
            return
        upstream = None
        response_started = False
        self.close_connection = True
        try:
            upstream = socket.create_connection(
                (CONFIG["ports"]["host"], CONFIG["ports"]["backend"]), timeout=10
            )
            lines = [
                f"GET {self.path} HTTP/1.1",
                f"Host: {CONFIG['ports']['host']}:{CONFIG['ports']['backend']}",
                "Connection: Upgrade",
                "Upgrade: websocket",
            ]
            for key, value in self.headers.items():
                if key.lower() not in HOP_BY_HOP and key.lower() not in {
                    "host",
                    "content-length",
                }:
                    lines.append(f"{key}: {value}")
            request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
            if len(request) > 65536:
                self.send_error(400, "Invalid stream upgrade")
                return
            upstream.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = upstream.recv(4096)
                if not chunk or len(response) + len(chunk) > 65536:
                    raise OSError("Invalid upstream upgrade")
                response.extend(chunk)
            header, buffered = bytes(response).split(b"\r\n\r\n", 1)
            status_line, *header_lines = header.split(b"\r\n")
            if not re.fullmatch(
                rb"HTTP/1\.[01] [1-5][0-9]{2}(?: [^\r\n]*)?", status_line
            ):
                raise OSError("Invalid upstream status")
            status = int(status_line.split()[1])
            headers = {}
            for line in header_lines:
                key, separator, value = line.partition(b":")
                if not separator:
                    raise OSError("Invalid upstream headers")
                name = key.lower()
                if name in headers:
                    raise OSError("Ambiguous upstream headers")
                headers[name] = value.strip()
            self.connection.settimeout(STREAM_IO_TIMEOUT_SECONDS)
            if status != 101:
                # Preserve bounded auth failures completely; do not truncate a 401 body.
                if b"transfer-encoding" in headers:
                    raise OSError("Unsupported upstream error framing")
                length = int(headers.get(b"content-length", b"0"))
                if not 0 <= length <= 65536 or len(buffered) > length:
                    raise OSError("Invalid upstream error size")
                body = bytearray(buffered)
                while len(body) < length:
                    chunk = upstream.recv(min(4096, length - len(body)))
                    if not chunk:
                        raise OSError("Truncated upstream error")
                    body.extend(chunk)
                response_started = True
                self.connection.sendall(header + b"\r\n\r\n" + body)
                return
            if headers.get(
                b"upgrade", b""
            ).lower() != b"websocket" or b"upgrade" not in {
                v.strip().lower() for v in headers.get(b"connection", b"").split(b",")
            }:
                raise OSError("Invalid upstream upgrade headers")
            response_started = True
            self.connection.sendall(header + b"\r\n\r\n" + buffered)
            upstream.settimeout(STREAM_IO_TIMEOUT_SECONDS)
            peers = (self.connection, upstream)
            deadline = time.monotonic() + STREAM_TUNNEL_MAX_SECONDS
            while time.monotonic() < deadline:
                readable, _, failed = select.select(
                    peers, [], peers, min(30, max(0, deadline - time.monotonic()))
                )
                if failed:
                    break
                for source in readable:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    destination = (
                        upstream if source is self.connection else self.connection
                    )
                    destination.sendall(chunk)
        except (OSError, ValueError):
            if not response_started:
                self.send_error(502, "Login stream unavailable")
        finally:
            if upstream is not None:
                upstream.close()

    def log_message(self, format, *args):
        # Render original types first (%d must stay numeric), then redact capabilities.
        message = re.sub(r"\?[^\s\"]*", "", format % args)
        super().log_message("%s", message)

    def _serve_spa(self) -> None:
        request_path = self.path.split("?", 1)[0]
        candidate = (DIST / request_path.lstrip("/")).resolve()
        if (
            request_path != "/"
            and DIST.resolve() in candidate.parents
            and candidate.is_file()
        ):
            super().do_GET()
            return
        self.path = "/index.html"
        super().do_GET()

    def do_GET(self) -> None:
        if self.headers.get("Upgrade"):
            self._websocket()
        elif self.path.startswith("/api/"):
            self._proxy()
        else:
            self._serve_spa()

    def do_HEAD(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self._serve_spa()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        (CONFIG["ports"]["host"], CONFIG["ports"]["frontend"]),
        CandidateFrontendHandler,
    )
    server.serve_forever()
