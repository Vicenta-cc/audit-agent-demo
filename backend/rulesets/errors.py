class RuleSetError(RuntimeError):
    pass


class RuleSetNotFoundError(RuleSetError):
    pass


class RuleSetRevisionNotFoundError(RuleSetError):
    pass


class RuleSetForbiddenError(RuleSetError):
    pass


class RuleSetRevisionConflictError(RuleSetError):
    pass


class RuleSetIdempotencyConflictError(RuleSetError):
    pass


class RuleSetValidationError(RuleSetError):
    pass
