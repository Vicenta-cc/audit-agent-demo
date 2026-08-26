"""Shared fail-closed helpers for frozen source fact projection."""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def normalize_source_id(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text).strip()


def normalize_source_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


def read_verified_raw_item(
    path_value: Any, *, expected_aweme_id: Any
) -> dict[str, Any] | None:
    """Read one linked raw item and reject missing or mismatched identity."""

    raw_path = normalize_source_id(path_value)
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid raw item JSON: {path}") from exc
    item = payload.get("item") if isinstance(payload, dict) else None
    if item is None:
        return None
    if not isinstance(item, dict):
        raise TypeError(f"raw item payload is not an object: {path}")
    expected = normalize_source_id(expected_aweme_id)
    actual = normalize_source_id(item.get("aweme_id"))
    if not expected or not actual or actual != expected:
        raise ValueError(
            f"raw item aweme_id mismatch: expected {expected or 'non-empty'}, "
            f"got {actual or 'missing'} at {path}"
        )
    return item


def unix_timestamp(value: Any) -> tuple[int | None, str]:
    if value in (None, ""):
        return None, ""
    try:
        epoch = int(value)
        rendered = datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid Unix source timestamp: {value}") from exc
    return epoch, rendered


def utc_timestamp(value: Any) -> str:
    """Normalize a source timestamp without inventing a replacement value."""

    raw = normalize_source_id(value)
    if not raw:
        return ""
    try:
        if raw.lstrip("-").isdigit():
            parsed = datetime.fromtimestamp(int(raw), UTC)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            parsed = parsed.astimezone(UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError(f"invalid source timestamp: {raw}") from exc
    return parsed.isoformat().replace("+00:00", "Z")
