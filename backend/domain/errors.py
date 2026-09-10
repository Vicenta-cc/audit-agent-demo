from __future__ import annotations

from enum import Enum


class DomainQueryErrorCode(str, Enum):
    OBJECT_NOT_FOUND = "object_not_found"
    INVALID_PARAMETER = "invalid_parameter"
    ACCESS_DENIED = "access_denied"
    FINDING_HAS_NO_EVIDENCE = "finding_has_no_evidence"
    EVIDENCE_TYPE_NOT_FOUND = "evidence_type_not_found"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    BROKEN_ASSOCIATION = "broken_association"
    UNSUPPORTED_FORMAT = "unsupported_format"


class DomainQueryError(Exception):
    code: DomainQueryErrorCode

    def __init__(self, message: str, *, object_type: str = "", object_id: str = ""):
        super().__init__(message)
        self.message = message
        self.object_type = object_type
        self.object_id = object_id

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "message": self.message,
            "object_type": self.object_type,
            "object_id": self.object_id,
        }


class DomainObjectNotFoundError(DomainQueryError):
    code = DomainQueryErrorCode.OBJECT_NOT_FOUND


class DomainQueryParameterError(DomainQueryError, ValueError):
    code = DomainQueryErrorCode.INVALID_PARAMETER


class DomainAccessDeniedError(DomainQueryError, PermissionError):
    code = DomainQueryErrorCode.ACCESS_DENIED


class FindingHasNoEvidenceError(DomainQueryError):
    code = DomainQueryErrorCode.FINDING_HAS_NO_EVIDENCE


class EvidenceTypeNotFoundError(DomainQueryError):
    code = DomainQueryErrorCode.EVIDENCE_TYPE_NOT_FOUND


class EvidenceResourceUnavailableError(DomainQueryError):
    code = DomainQueryErrorCode.RESOURCE_UNAVAILABLE


class DomainAssociationError(DomainQueryError):
    code = DomainQueryErrorCode.BROKEN_ASSOCIATION


class DomainUnsupportedFormatError(DomainQueryError):
    code = DomainQueryErrorCode.UNSUPPORTED_FORMAT
