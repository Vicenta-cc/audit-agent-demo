"""Strict Provider configuration shared by preflight and frozen diagnostics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


_ALLOWED_ENDPOINTS = {
    "dashscope.aliyuncs.com": "/compatible-mode/v1",
    "dashscope-intl.aliyuncs.com": "/compatible-mode/v1",
}


@dataclass(frozen=True)
class DashScopeEndpoint:
    base_url: str
    host: str


def require_dashscope_endpoint(
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> DashScopeEndpoint:
    values = os.environ if environ is None else environ
    raw = values.get("DASHSCOPE_BASE_URL", "")
    if not raw.strip():
        raise RuntimeError("DASHSCOPE_BASE_URL is required")
    return validate_dashscope_endpoint(raw)


def validate_dashscope_endpoint(raw: str) -> DashScopeEndpoint:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("DashScope endpoint must be a non-empty URL")
    parsed = urlsplit(raw.strip())
    host = (parsed.hostname or "").lower()
    expected_path = _ALLOWED_ENDPOINTS.get(host)
    if parsed.scheme.lower() != "https":
        raise ValueError("DashScope endpoint must use HTTPS")
    if expected_path is None:
        raise ValueError("DashScope endpoint host is not allowed")
    if parsed.username or parsed.password or parsed.port is not None:
        raise ValueError("DashScope endpoint must not include credentials or a port")
    if parsed.query or parsed.fragment:
        raise ValueError("DashScope endpoint must not include query or fragment data")
    normalized_path = parsed.path.rstrip("/")
    if normalized_path != expected_path:
        raise ValueError(
            f"DashScope endpoint path must be {expected_path}"
        )
    normalized = urlunsplit(("https", host, expected_path, "", ""))
    return DashScopeEndpoint(base_url=normalized, host=host)
