"""Public application errors for M3 investigation creation."""


class InvestigationCreationError(Exception):
    pass


class DraftNotFoundError(InvestigationCreationError):
    pass


class RunNotFoundError(InvestigationCreationError):
    pass


class DraftRevisionConflictError(InvestigationCreationError):
    pass


class DraftAlreadyConfirmedError(InvestigationCreationError):
    pass


class ConfirmationRequiredError(InvestigationCreationError):
    pass


class IdempotencyConflictError(InvestigationCreationError):
    pass


class InvalidStateTransitionError(InvestigationCreationError):
    pass


class ConfigurationValidationError(InvestigationCreationError):
    pass


class PrincipalAccessDeniedError(InvestigationCreationError):
    pass


class LeaseLostError(InvestigationCreationError):
    pass
