from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote, unquote


FINDING_PREFIX = "finding:audit_result:"
EVIDENCE_PREFIX = "evidence:audit_result:"


def make_finding_id(audit_result_id: int) -> str:
    return f"{FINDING_PREFIX}{int(audit_result_id)}"


def parse_finding_id(value: str) -> int:
    if not value.startswith(FINDING_PREFIX):
        raise ValueError("invalid finding id")
    raw_id = value[len(FINDING_PREFIX) :]
    if not raw_id.isdigit():
        raise ValueError("invalid finding id")
    return int(raw_id)


def make_evidence_id(audit_result_id: int, local_evidence_id: str) -> str:
    local_id = str(local_evidence_id or "").strip()
    if not local_id:
        raise ValueError("local evidence id is required")
    encoded = quote(local_id, safe=":-._~")
    return f"{EVIDENCE_PREFIX}{int(audit_result_id)}:{encoded}"


def parse_evidence_id(value: str) -> tuple[int, str]:
    if not value.startswith(EVIDENCE_PREFIX):
        raise ValueError("invalid evidence id")
    raw = value[len(EVIDENCE_PREFIX) :]
    raw_result_id, separator, raw_local_id = raw.partition(":")
    if not separator or not raw_result_id.isdigit() or not raw_local_id:
        raise ValueError("invalid evidence id")
    local_id = unquote(raw_local_id)
    if not local_id:
        raise ValueError("invalid evidence id")
    return int(raw_result_id), local_id


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generated_local_evidence_id(prefix: str, value: Any) -> str:
    normalized_prefix = "".join(
        character if character.isalnum() or character in ("-", "_") else "_"
        for character in str(prefix or "other").lower()
    ).strip("_") or "other"
    return f"{normalized_prefix}:{stable_hash(value)[:20]}"
