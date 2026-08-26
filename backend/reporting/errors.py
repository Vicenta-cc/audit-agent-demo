from __future__ import annotations


class ReportGenerationError(RuntimeError):
    pass


class ReportValidationError(ReportGenerationError):
    def __init__(self, stage: str, errors: list[str]):
        super().__init__(f"{stage} failed: {'; '.join(errors)}")
        self.stage = stage
        self.errors = tuple(errors)


class ReportModelError(ReportGenerationError):
    def __init__(self, kind: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class PublishedReportImmutableError(ReportGenerationError):
    pass
