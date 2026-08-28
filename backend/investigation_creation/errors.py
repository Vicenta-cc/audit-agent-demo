"""Public application errors for M3 investigation creation."""

from __future__ import annotations

from typing import Any


class InvestigationCreationError(Exception):
    code = "INVESTIGATION_CREATION_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or type(self).code
        self.details = dict(details or {})


class DraftNotFoundError(InvestigationCreationError):
    pass


class RunNotFoundError(InvestigationCreationError):
    pass


class DraftRevisionConflictError(InvestigationCreationError):
    pass


class DraftAlreadyConfirmedError(InvestigationCreationError):
    pass


class ConfirmationRequiredError(InvestigationCreationError):
    code = "CONFIRMATION_REQUIRED"


class IdempotencyConflictError(InvestigationCreationError):
    code = "IDEMPOTENCY_CONFLICT"


class InvalidStateTransitionError(InvestigationCreationError):
    pass


class ConfigurationValidationError(InvestigationCreationError):
    code = "CONFIGURATION_INVALID"


class ResourceStaleError(InvestigationCreationError):
    code = "RESOURCE_STALE"


class PrincipalAccessDeniedError(InvestigationCreationError):
    pass


class LeaseLostError(InvestigationCreationError):
    pass
