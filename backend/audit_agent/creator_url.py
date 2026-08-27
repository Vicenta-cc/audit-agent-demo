from __future__ import annotations

from urllib.parse import urlsplit


class CreatorUrlValidationError(ValueError):
    pass


def validate_creator_url(
    platform: str,
    creator_url: str,
    *,
    allow_legacy_id: bool = False,
) -> str:
    """Validate a platform creator profile URL without an HTTP dependency."""

    value = str(creator_url or "").strip()
    if not value:
        raise CreatorUrlValidationError(
            "creator_url is required when crawl_mode is creator"
        )

    if allow_legacy_id and _is_legacy_creator_id(platform, value):
        return value

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise CreatorUrlValidationError(_profile_url_message(platform)) from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise CreatorUrlValidationError(_profile_url_message(platform))

    hostname = parsed.hostname.lower().rstrip(".")
    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if platform == "xhs":
        valid = (
            _host_matches(hostname, "xiaohongshu.com")
            and len(path_parts) == 3
            and path_parts[:2] == ("user", "profile")
            and bool(path_parts[2])
        )
        if not valid:
            raise CreatorUrlValidationError(_profile_url_message(platform))
        query_keys = {part.split("=", 1)[0] for part in parsed.query.split("&")}
        if not {"xsec_token", "xsec_source"}.issubset(query_keys):
            raise CreatorUrlValidationError(
                "小红书主页 URL 建议包含 xsec_token 和 xsec_source；"
                "请从网页端打开主页后复制完整地址。"
            )
    elif platform == "dy":
        valid = (
            _host_matches(hostname, "douyin.com")
            and len(path_parts) == 2
            and path_parts[0] == "user"
            and bool(path_parts[1])
        )
        if not valid:
            raise CreatorUrlValidationError(_profile_url_message(platform))
    elif platform == "ks":
        valid = (
            _host_matches(hostname, "kuaishou.com")
            and len(path_parts) == 2
            and path_parts[0] == "profile"
            and bool(path_parts[1])
        )
        if not valid:
            raise CreatorUrlValidationError(_profile_url_message(platform))
    else:
        raise CreatorUrlValidationError(f"Unsupported platform: {platform}")
    return value


def _host_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _is_legacy_creator_id(platform: str, value: str) -> bool:
    if "://" in value or "/" in value or any(character.isspace() for character in value):
        return False
    if platform == "xhs":
        return len(value) == 24 and all(
            character in "0123456789abcdef" for character in value
        )
    return platform in {"dy", "ks"}


def _profile_url_message(platform: str) -> str:
    return {
        "xhs": "小红书创作者抓取请填写完整主页 URL。",
        "dy": "抖音创作者抓取请填写完整主页 URL。",
        "ks": "快手创作者抓取请填写完整主页 URL。",
    }.get(platform, f"Unsupported platform: {platform}")
