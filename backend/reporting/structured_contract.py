"""Lightweight public fence for the R3.1 structured report document."""

from __future__ import annotations

import re
from typing import Any

from backend.reporting.errors import ReportGenerationError


STRUCTURED_REPORT_SCHEMA_VERSION = "structured-report-r3.1/v1"
_PRIVATE_KEYS = {
    "database_path",
    "dataset_id",
    "internal_account_ref",
    "snapshot_ref",
    "task_id",
}
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/Users/|/home/|file://|[A-Za-z]:\\)")


def validate_structured_report_document(
    document: dict[str, Any], *, account_model: dict[str, Any] | None
) -> None:
    if document.get("schema_version") != STRUCTURED_REPORT_SCHEMA_VERSION:
        raise ReportGenerationError("unsupported structured Report schema_version")
    if not isinstance(document.get("ordered_sections"), list):
        raise ReportGenerationError("structured Report ordered_sections are missing")
    metadata = document.get("report_metadata")
    if not isinstance(metadata, dict):
        raise ReportGenerationError("structured Report metadata is missing")
    if document.get("template_kind") == "selected_existing_audits":
        if account_model is not None or not isinstance(document.get("source_coverage"), dict):
            raise ReportGenerationError("selected evidence report has invalid coverage or account model")
        _assert_public_value(document)
        return
    required_account_fields = {
        "account_coverage_statistics",
        "default_active_comment_entries",
        "full_account_index",
        "scope_boundary",
        "target_account_entries",
    }
    if not required_account_fields.issubset(account_model):
        raise ReportGenerationError("structured Report account_model is incomplete")
    projected_fields = {
        "account_coverage_statistics": "account_coverage_statistics",
        "default_active_comment_entries": "default_active_comment_entries",
        "full_account_index": "full_account_index",
        "scope_boundary": "account_scope_boundary",
        "target_account_entries": "target_account_entries",
    }
    for model_key, document_key in projected_fields.items():
        if document.get(document_key) != account_model.get(model_key):
            raise ReportGenerationError(
                "structured Report account_model projection mismatch"
            )
    _assert_public_value(document)
    _assert_public_value(account_model)


def _assert_public_value(value: Any) -> None:
    if isinstance(value, dict):
        private = _PRIVATE_KEYS.intersection(value)
        if private:
            raise ReportGenerationError(
                "structured Report contains a private field: " + sorted(private)[0]
            )
        for item in value.values():
            _assert_public_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_public_value(item)
        return
    if isinstance(value, str) and _ABSOLUTE_PATH.search(value):
        raise ReportGenerationError("structured Report contains an absolute path")
