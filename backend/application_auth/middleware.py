from __future__ import annotations

import json
from http.cookies import SimpleCookie
from typing import Any

from backend.investigation_creation.principal import (
    bind_request_principal,
    reset_request_principal,
)

from .store import (
    AccountExpiredError,
    AuthenticationError,
    AuthStore,
    AuthorizationError,
)


class ApplicationAuthMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        store: AuthStore,
        cookie_name: str,
        public_api_paths: tuple[str, ...] = (
            "/api/auth/login",
            "/api/auth/config",
            "/api/acceptance-runtime",
        ),
        public_api_prefixes: tuple[str, ...] = ("/api/health/",),
    ) -> None:
        self.app = app
        self.store = store
        self.cookie_name = cookie_name
        self.public_api_paths = frozenset(public_api_paths)
        self.public_api_prefixes = public_api_prefixes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if (
            not path.startswith("/api/")
            or method == "OPTIONS"
            or path in self.public_api_paths
            or any(path.startswith(prefix) for prefix in self.public_api_prefixes)
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or ()
        }
        raw_token = _cookie_value(headers.get("cookie", ""), self.cookie_name)
        csrf_token = (
            headers.get("x-csrf-token", "")
            if method in {"POST", "PUT", "PATCH", "DELETE"}
            else None
        )
        try:
            principal = self.store.authenticate_session(
                raw_token, csrf_token=csrf_token
            )
        except AuthorizationError as exc:
            await _json_error(
                scope,
                receive,
                send,
                403,
                "CSRF_VALIDATION_FAILED",
                str(exc),
            )
            return
        except AccountExpiredError as exc:
            await _json_error(
                scope, receive, send, 401, "ACCOUNT_EXPIRED", str(exc)
            )
            return
        except AuthenticationError as exc:
            await _json_error(
                scope, receive, send, 401, "AUTHENTICATION_REQUIRED", str(exc)
            )
            return

        expected_user = headers.get("x-application-user", "")
        if expected_user and expected_user != principal.id:
            await _json_error(scope, receive, send, 409, "AUTH_IDENTITY_CHANGED", "登录身份已变化，请重新登录。")
            return

        scope.setdefault("state", {})["principal"] = principal
        context_token = bind_request_principal(principal)
        is_event_stream = False
        stream_ended = False

        async def authenticated_receive() -> dict:
            if stream_ended:
                return {"type": "http.disconnect"}
            return await receive()

        async def authenticated_send(message: dict) -> None:
            nonlocal is_event_stream, stream_ended
            if stream_ended:
                return
            if message["type"] == "http.response.start":
                response_start_headers = list(message.get("headers") or ())
                response_headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in response_start_headers
                }
                is_event_stream = response_headers.get(
                    "content-type", ""
                ).startswith("text/event-stream")
                # Authenticated API responses can contain user-owned data. A
                # shared browser/proxy cache must never replay A's response to B.
                response_start_headers = [
                    (key, value)
                    for key, value in response_start_headers
                    if key.lower() != b"cache-control"
                ]
                response_start_headers.append((b"x-application-user", principal.id.encode("ascii")))
                response_start_headers.append(
                    (b"cache-control", b"private, no-store")
                )
                message = {**message, "headers": response_start_headers}
            elif message["type"] == "http.response.body" and is_event_stream:
                try:
                    self.store.authenticate_session(raw_token)
                except AuthenticationError as exc:
                    payload = (
                        "event: auth_expired\n"
                        + "data: "
                        + json.dumps(
                            {
                                "code": "AUTHENTICATION_EXPIRED",
                                "message": str(exc),
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    ).encode("utf-8")
                    await send(
                        {
                            "type": "http.response.body",
                            "body": payload,
                            "more_body": False,
                        }
                    )
                    stream_ended = True
                    return
            await send(message)

        try:
            await self.app(scope, authenticated_receive, authenticated_send)
        finally:
            reset_request_principal(context_token)


def _cookie_value(header: str, name: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(str(header or ""))
    except Exception:
        return ""
    morsel = cookie.get(name)
    return morsel.value if morsel is not None else ""


async def _json_error(
    scope: dict,
    receive: Any,
    send: Any,
    status: int,
    code: str,
    message: str,
) -> None:
    body = json.dumps(
        {"detail": {"code": code, "message": message}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
