from __future__ import annotations

import re


PROFILE_ID_PATTERNS = {
    "xhs": re.compile(r"/user/profile/([A-Za-z0-9_-]{4,128})(?:[/?#]|$)"),
    "dy": re.compile(r"/user/([A-Za-z0-9_-]{8,256})(?:[/?#]|$)"),
    "ks": re.compile(r"/profile/([A-Za-z0-9_-]{4,128})(?:[/?#]|$)"),
}

PERSONAL_LINK_MARKERS = ("我", "个人", "主页", "用户中心", "profile", "account")


def extract_platform_account_id(platform: str, value: str) -> str:
    pattern = PROFILE_ID_PATTERNS.get(platform)
    if not pattern:
        return ""
    match = pattern.search(str(value or ""))
    return match.group(1) if match else ""


def candidate_is_personal_link(platform: str, candidate: dict, require_marker: bool) -> bool:
    if not candidate.get("visible"):
        return False
    if platform in {"dy", "ks"} and candidate.get("inContent"):
        return False
    if platform in {"dy", "ks"} and not (-20 <= float(candidate.get("top") or 0) <= 220):
        return False
    text = f"{candidate.get('text') or ''} {candidate.get('label') or ''}".lower()
    has_marker = any(marker in text for marker in PERSONAL_LINK_MARKERS)
    if require_marker:
        return has_marker
    return bool(candidate.get("hasImage") or has_marker)
