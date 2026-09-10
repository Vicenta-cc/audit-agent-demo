#!/usr/bin/env python3
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class SpaRequestHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        request_path = urlsplit(self.path).path
        local_path = Path(self.translate_path(request_path))
        if (
            not local_path.exists()
            and not request_path.startswith("/api/")
            and not Path(request_path).suffix
        ):
            self.path = "/index.html"
        return super().send_head()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve static files with SPA route fallback.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()

    handler = partial(SpaRequestHandler, directory=str(args.directory.resolve()))
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {args.directory} at http://{args.bind}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
