from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


LOCAL_PRINCIPAL_ID = "local-user"


@dataclass(frozen=True)
class Principal:
    id: str


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
