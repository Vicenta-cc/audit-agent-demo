"""Opt-in isolated model gateway; importing this module never starts a server.

Every forwarded HTTP attempt consumes a durable credit before transport. All
model roles and their subprocesses must use this one gateway for a global cap.
This module does not configure clients, authorize R6, or prove route coverage.
No request body, Authorization value, or upstream exception is logged/persisted.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import sqlite3
import ssl
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

ALLOWED_PATH = '/v1/chat/completions'
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class GatewayResponse:
    status: int
    body: bytes
    content_type: str = 'application/json'


def _error(status, code):
    return GatewayResponse(status, json.dumps({'error': {'code': code, 'message': code}}).encode())


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_transport(*, url, body, headers, timeout):
    """One TLS-verified, non-retrying, non-redirecting transport attempt."""
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    request = Request(url, data=body, headers=headers, method='POST')
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as response_error:
        response = response_error
    with response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            raise ValueError('upstream_response_too_large')
        return response.code, content, response.headers.get('Content-Type', 'application/json')


class ModelGateway:
    def __init__(self, *, isolation_root, database, upstream_base,
                 upstream_authorization, gateway_token, limit=30, timeout=120,
                 transport=https_transport):
        root = Path(isolation_root).resolve()
        temporary = Path(tempfile.gettempdir()).resolve()
        if root == temporary or not root.is_relative_to(temporary):
            raise ValueError('dedicated temporary isolation root required')
        self.database = Path(database).resolve()
        if not self.database.is_relative_to(root) or self.database.name != 'model_gateway_budget.sqlite3':
            raise ValueError('isolated model gateway database required')
        parsed = urlsplit(upstream_base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or '\\' in upstream_base
                or parsed.path.rstrip('/') not in ('', '/v1') and not parsed.path.rstrip('/').endswith('/v1')
                or any(part in ('.', '..') for part in parsed.path.split('/'))):
            raise ValueError('fixed HTTPS model base ending in /v1 required')
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError('budget limit must be an integer between 1 and 30')
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValueError('bounded transport timeout required')
        if not gateway_token or not upstream_authorization or any(c in upstream_authorization for c in '\r\n'):
            raise ValueError('server-owned authorization and local token required')
        self.url = upstream_base.rstrip('/') + ('/chat/completions' if parsed.path.rstrip('/') else ALLOWED_PATH)
        self._upstream_authorization = upstream_authorization
        self._gateway_token = gateway_token
        self.timeout, self.transport = timeout, transport
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'model_gateway_budget'}:
                raise ValueError('database contains unrelated tables')
            db.execute('CREATE TABLE IF NOT EXISTS model_gateway_budget '
                       '(id INTEGER PRIMARY KEY CHECK(id=1), upstream TEXT NOT NULL, '
                       'budget_limit INTEGER NOT NULL CHECK(budget_limit>0), '
                       'used INTEGER NOT NULL CHECK(used>=0 AND used<=budget_limit))')
            saved = db.execute('SELECT upstream,budget_limit FROM model_gateway_budget WHERE id=1').fetchone()
            if saved is None:
                db.execute('INSERT INTO model_gateway_budget VALUES (1,?,?,0)', (self.url, limit))
            elif saved != (self.url, limit):
                raise ValueError('persisted gateway configuration differs; budget cannot be reset')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def snapshot(self):
        with self._connect() as db:
            limit, used = db.execute('SELECT budget_limit,used FROM model_gateway_budget WHERE id=1').fetchone()
        return {'limit': limit, 'used': used, 'remaining': limit-used}

    def _reserve(self):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE model_gateway_budget SET used=used+1 WHERE id=1 AND used<budget_limit').rowcount
            # The context commits before returning to the transport caller.
        return changed == 1

    def forward(self, method, path, body, *, authorization=''):
        if not isinstance(authorization, str) or not hmac.compare_digest(authorization.encode(), ('Bearer '+self._gateway_token).encode()):
            return _error(401, 'gateway_unauthorized')
        if method != 'POST':
            return _error(405, 'gateway_method_not_allowed')
        if path != ALLOWED_PATH:
            return _error(404, 'gateway_path_not_allowed')
        if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
            return _error(413, 'gateway_body_too_large')
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError('object required')
        except (ValueError, UnicodeDecodeError):
            return _error(400, 'gateway_json_required')
        if not self._reserve():
            return _error(429, 'model_budget_exhausted')
        try:
            status, content, content_type = self.transport(url=self.url, body=body,
                headers={'Authorization': self._upstream_authorization, 'Content-Type': 'application/json'},
                timeout=self.timeout)
            if not isinstance(content, bytes) or len(content) > MAX_RESPONSE_BYTES:
                raise ValueError('invalid upstream response')
            # Never relay redirect locations or hop-by-hop upstream headers.
            if not 200 <= status < 300:
                return _error(status if 400 <= status <= 599 else 502, 'model_upstream_error')
            return GatewayResponse(status, content, 'text/event-stream' if content_type.split(';')[0].strip() == 'text/event-stream' else 'application/json')
        except Exception:
            return _error(502, 'model_transport_failed')


def serve(gateway, *, port):
    """Explicit opt-in only. Binds loopback; no CLI or import-time auto-start.

    Responses (including SSE) are buffered. This is not a streaming-latency
    implementation. Direct clients/proxy routing still require an R6 audit.
    """
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('explicit unprivileged loopback port required')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_BODY_BYTES or self.headers.get('Transfer-Encoding'):
                    result = _error(413, 'gateway_content_length_required')
                else:
                    self.connection.settimeout(gateway.timeout)
                    result = gateway.forward('POST', self.path, self.rfile.read(length),
                        authorization=self.headers.get('Authorization', ''))
            except Exception:
                result = _error(400, 'gateway_request_invalid')
            self.send_response(result.status)
            self.send_header('Content-Type', result.content_type)
            self.send_header('Content-Length', str(len(result.body)))
            self.end_headers()
            self.wfile.write(result.body)

    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        server.serve_forever()
