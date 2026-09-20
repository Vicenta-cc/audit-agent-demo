"""Serve the built R2-ABC frontend and proxy /api to its backend."""
from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("DOUYIN_CANDIDATE_CONFIG", ROOT / "runtime.json")).resolve()
CONFIG = json.loads(CONFIG_PATH.read_text())
DIST = Path(CONFIG["frontend_root"]) / "dist"
BACKEND = f"http://{CONFIG['host']}:{int(CONFIG['backend_port'])}"
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


class CandidateFrontendHandler(SimpleHTTPRequestHandler):
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
            response = urllib.request.urlopen(request, timeout=180)
        except urllib.error.HTTPError as error:
            response = error
        except urllib.error.URLError as error:
            payload = json.dumps({"detail": f"R2-ABC backend unavailable: {error.reason}"}).encode()
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
                    "content-length", "x-accel-buffering"
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

    def _serve_spa(self) -> None:
        request_path = self.path.split("?", 1)[0]
        candidate = (DIST / request_path.lstrip("/")).resolve()
        if request_path != "/" and DIST.resolve() in candidate.parents and candidate.is_file():
            super().do_GET()
            return
        self.path = "/index.html"
        super().do_GET()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
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
        (CONFIG["host"], int(CONFIG["frontend_port"])),
        CandidateFrontendHandler,
    )
    server.serve_forever()
