"""Native Hermes ToolResult payload helpers."""

from __future__ import annotations

import json
from typing import Any


CONTRACT_VERSION = "hermes-investigation-tool-result/v1"


def success_result(
    *,
    tool: str,
    result_kind: str,
    content_state: str,
    scope: dict[str, Any],
    data: dict[str, Any],
    not_loaded: list[str],
    limitations: list[str],
    authority_basis: str = "frozen_report_projection",
) -> str:
    return _encode(
        {
            "contract_version": CONTRACT_VERSION,
            "ok": True,
            "tool": tool,
            "result_kind": result_kind,
            "content_state": content_state,
            "scope": scope,
            "authority": {
                "basis": authority_basis,
                "limitations": limitations,
            },
            "data": data,
            "not_loaded": not_loaded,
        }
    )


def error_result(*, tool: str, code: str, message: str) -> str:
    return _encode(
        {
            "contract_version": CONTRACT_VERSION,
            "ok": False,
            "tool": tool,
            "result_kind": "error",
            "content_state": "none",
            "error": {"code": code, "message": message},
            "data": {},
            "not_loaded": [],
        }
    )


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
