"""Serve the separately pinned frontend hotfix artifact and stream API responses."""

from __future__ import annotations

import json
import os
import re
import select
import socket
import sys
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import check_artifacts, environment, read_json, validate

CONFIG = validate(read_json(os.environ["CAMOUFOX_RELEASE_MANIFEST"]))
check_artifacts(CONFIG)
environment(
    CONFIG
)  # Validate the App-owner contract without exporting secrets to this process.
DIST = Path(CONFIG["artifacts"]["frontend_dist"]["path"])
BACKEND = f"http://{CONFIG['ports']['host']}:{CONFIG['ports']['backend']}"
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


class CandidateFrontendHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != "host"
        }
        request = urllib.request.Request(
            BACKEND + self.path,
            data=body,
            headers=headers,
            method=self.command,
        )
        try:
            response = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            ).open(request, timeout=180)
        except urllib.error.HTTPError as error:
            response = error
        except urllib.error.URLError as error:
            payload = json.dumps(
                {"detail": f"Backend unavailable: {error.reason}"}
            ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if response.headers.get_content_type() == "text/event-stream":
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in HOP_BY_HOP and key.lower() not in {
                    "content-length",
                    "x-accel-buffering",
                }:
                    self.send_header(key, value)
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                if self.command != "HEAD":
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                response.close()
            return
        try:
            payload = response.read()
        finally:
            response.close()
        self.send_response(response.status)
        for key, value in response.headers.items():
            if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

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
