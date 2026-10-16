from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit


PROFILE_ID_PATTERNS = {
    "xhs": re.compile(r"/user/profile/([A-Za-z0-9_-]{4,128})(?:[/?#]|$)"),
    "dy": re.compile(r"/user/([A-Za-z0-9_-]{8,256})(?:[/?#]|$)"),
    "ks": re.compile(r"/profile/([A-Za-z0-9_-]{4,128})(?:[/?#]|$)"),
}

PERSONAL_LINK_MARKERS = ("我", "个人", "主页", "用户中心", "profile", "account")


def douyin_self_response_identity(url: str, payload: object) -> str:
    """Accept only the first-party current-user endpoint, never an author's profile.

    The real login bundle calls profile/self without a sec_user_id target. The
    response must carry a successful user object with a valid public sec_uid;
    anonymous telemetry IDs, nicknames and caller-supplied targets are not proof.
    """
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or not (host == "douyin.com" or host.endswith(".douyin.com"))
            or parsed.path.rstrip("/") != "/aweme/v1/web/user/profile/self"
            or any(key in parse_qs(parsed.query) for key in ("sec_user_id", "user_id", "uid"))
            or not isinstance(payload, dict) or type(payload.get("status_code")) is not int
            or payload.get("status_code") != 0):
        return ""
    user = payload.get("user")
    if not isinstance(user, dict):
        return ""
    value = str(user.get("sec_uid") or "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{8,256}", value) else ""


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
