from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException


LOCAL_PRINCIPAL_ID = "local-user"


@dataclass(frozen=True)
class Principal:
    id: str
    role: str = "user"
    session_id: str = ""
    expires_at: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class PrincipalProvider(Protocol):
    def __call__(self) -> Principal: ...


class LocalPrincipalProvider:
    """Explicit single-user identity boundary for the local product mode."""

    def __init__(self, principal_id: str = LOCAL_PRINCIPAL_ID) -> None:
        normalized = str(principal_id or "").strip()
        if not normalized:
            raise ValueError("local principal id is required")
        self.principal = Principal(normalized)

    def __call__(self) -> Principal:
        return self.principal


_request_principal: ContextVar[Principal | None] = ContextVar(
    "application_request_principal", default=None
)


def bind_request_principal(principal: Principal) -> Token:
    return _request_principal.set(principal)


def reset_request_principal(token: Token) -> None:
    _request_principal.reset(token)


class RequestPrincipalProvider:
    """Return the authenticated Principal bound by the HTTP auth middleware."""

    def __call__(self) -> Principal:
        principal = _request_principal.get()
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return principal
