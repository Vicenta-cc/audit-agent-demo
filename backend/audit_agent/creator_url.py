from __future__ import annotations

import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import certifi


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


# Only public share/profile hosts may be contacted. No crawler cookies or
# credentials are needed to expand a public share link.
_DOUYIN_HOSTS = frozenset({
    "v.douyin.com", "douyin.com", "www.douyin.com",
    "iesdouyin.com", "www.iesdouyin.com",
})
_PROFILE_HOSTS = _DOUYIN_HOSTS - {"v.douyin.com"}
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_SHARE_URL = re.compile(r"https?://[^\s<>\"'，。！？、；（）【】]+", re.IGNORECASE)
_RESOLUTION_ERROR = "暂时无法解析抖音主页分享链接，请稍后重试，或粘贴浏览器中的完整主页地址。"
_NOT_PROFILE = "该链接不是有效的抖音用户主页，请复制用户主页分享链接或完整主页地址。"
_MAX_REDIRECTS = 5
_RESOLUTION_SECONDS = 12.0


def resolve_creator_url(
    platform: str,
    creator_url: str,
    *,
    allow_legacy_id: bool = False,
) -> str:
    """Resolve share input at the application boundary, before saving a draft.

    Validation of frozen configurations stays pure: this function is never
    needed by a worker or confirmation preview. Existing full profile URLs and
    legacy IDs retain their exact value and do not make a network request.
    """
    value = str(creator_url or "").strip()
    if platform != "dy":
        return validate_creator_url(platform, value, allow_legacy_id=allow_legacy_id)
    # Legacy IDs cannot include whitespace or a URL; do not interpret share
    # prose as a legacy ID merely because it has no scheme.
    if allow_legacy_id and _is_legacy_creator_id(platform, value) and value:
        return value
    if _SHARE_URL.fullmatch(value):
        try:
            return validate_creator_url(platform, value)
        except CreatorUrlValidationError:
            pass

    if len(value) > 4_000:
        raise CreatorUrlValidationError(_NOT_PROFILE)
    candidates = list(dict.fromkeys(
        match.rstrip(".,;:!?)]}、，。；：！？）】")
        for match in _SHARE_URL.findall(value)
    ))
    if len(candidates) != 1:
        raise CreatorUrlValidationError("请一次提供一个抖音用户主页链接。")
    current = _share_hop_url(candidates[0])
    deadline = time.monotonic() + _RESOLUTION_SECONDS
    seen: set[str] = set()
    for hop in range(_MAX_REDIRECTS + 1):
        if current in seen:
            raise CreatorUrlValidationError(_RESOLUTION_ERROR)
        seen.add(current)
        profile = _shared_profile_url(current)
        if profile is not None:
            return profile
        # A video, login page or arbitrary platform URL is never an account.
        if urlsplit(current).hostname != "v.douyin.com":
            raise CreatorUrlValidationError(_NOT_PROFILE)
        if hop == _MAX_REDIRECTS:
            break
        try:
            location = _read_share_redirect(current, deadline=deadline)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise CreatorUrlValidationError(_RESOLUTION_ERROR) from exc
        if not location:
            raise CreatorUrlValidationError(_RESOLUTION_ERROR)
        try:
            destination = urljoin(current, location)
        except ValueError as exc:
            raise CreatorUrlValidationError(_NOT_PROFILE) from exc
        current = _share_hop_url(destination)
    raise CreatorUrlValidationError(_RESOLUTION_ERROR)


def _share_hop_url(value: str) -> str:
    """Validate every destination before any DNS lookup or connection."""
    if len(value) > 4_000 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise CreatorUrlValidationError(_NOT_PROFILE)
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise CreatorUrlValidationError(_NOT_PROFILE) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or hostname not in _DOUYIN_HOSTS
        or parsed.username is not None or parsed.password is not None
        or port not in {None, 443 if parsed.scheme == "https" else 80}
    ):
        raise CreatorUrlValidationError(_NOT_PROFILE)
    # Public HTTP share URLs can safely be upgraded; all actual I/O uses TLS.
    return urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))


def _shared_profile_url(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.hostname not in _PROFILE_HOSTS:
        return None
    parts = parsed.path.strip("/").split("/")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "modal_id" in query:
        raise CreatorUrlValidationError(_NOT_PROFILE)
    if len(parts) == 2 and parts[0] == "user":
        identity = parts[1]
    elif len(parts) == 3 and parts[:2] == ["share", "user"]:
        identity = parts[2]
        # Older mobile shares use a numeric uid in the path. MediaCrawler
        # requires the separate sec_uid, never the numeric uid.
        if identity.isdigit():
            ids = query.get("sec_uid", [])
            if len(ids) != 1:
                raise CreatorUrlValidationError(_NOT_PROFILE)
            identity = ids[0]
    else:
        return None
    if not _ACCOUNT_ID.fullmatch(identity) or identity.isdigit():
        raise CreatorUrlValidationError(_NOT_PROFILE)
    return f"https://www.douyin.com/user/{identity}"


def _remaining_share_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("share link resolution deadline exceeded")
    return min(3.0, remaining)


def _share_tls_context() -> ssl.SSLContext:
    # Keep platform roots and add the CA bundle already used by HTTPX/requests.
    # Some Python installations have no configured system cafile.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


class _ShareHTTPSConnection(http.client.HTTPSConnection):
    """Pin a validated public address while retaining hostname TLS verification."""

    def __init__(self, host: str, address: str, *, timeout: float) -> None:
        self._share_address = address
        self._share_context = _share_tls_context()
        super().__init__(host, timeout=timeout, context=self._share_context)

    def connect(self) -> None:
        sock = socket.create_connection((self._share_address, 443), timeout=self.timeout)
        try:
            self.sock = self._share_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _share_connection(host: str, *, deadline: float):
    # This explicit runtime setting is independent of ambient HTTP(S)_PROXY.
    # A trusted local CONNECT proxy resolves the allowlisted Douyin host outside
    # TUN fake DNS; TLS still authenticates that host end to end.
    proxy_url = os.getenv("CRAWLER_PROXY_URL", "").strip()
    if proxy_url:
        proxy = urlsplit(proxy_url)
        try:
            loopback = ipaddress.ip_address(proxy.hostname or "").is_loopback
            port = proxy.port
        except ValueError as exc:
            raise ValueError("creator URL proxy must be a loopback HTTP endpoint") from exc
        if (
            proxy.scheme != "http" or not loopback or port is None or port < 1
            or proxy.username is not None or proxy.password is not None
            or proxy.path not in {"", "/"} or proxy.query or proxy.fragment
        ):
            raise ValueError("creator URL proxy must be a loopback HTTP endpoint")
        connection = http.client.HTTPSConnection(
            proxy.hostname, port, timeout=_remaining_share_time(deadline),
            context=_share_tls_context(),
        )
        connection.set_tunnel(host, 443)
        return connection

    _remaining_share_time(deadline)
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    }
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("share host must resolve to public addresses")
    # Pin the chosen address to prevent a second DNS lookup/rebinding. Prefer
    # IPv4 where both families are advertised but IPv6 routing is unavailable.
    address = min(addresses, key=lambda item: (":" in item, item))
    return _ShareHTTPSConnection(host, address, timeout=_remaining_share_time(deadline))


def _read_share_redirect(url: str, *, deadline: float) -> str:
    # Keep transport safe even if it is called separately from the resolver.
    parsed = urlsplit(_share_hop_url(url))
    connection = _share_connection(parsed.hostname, deadline=deadline)
    try:
        connection.connect()
        connection.sock.settimeout(_remaining_share_time(deadline))
        connection.request("GET", urlunsplit(("", "", parsed.path or "/", parsed.query, "")), headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html",
        })
        connection.sock.settimeout(_remaining_share_time(deadline))
        response = connection.getresponse()
        try:
            # Read headers only. Never download HTML, execute scripts, send
            # account cookies, or follow a redirect inside the HTTP client.
            if response.status in {301, 302, 303, 307, 308}:
                return response.getheader("Location", "")
            return ""
        finally:
            response.close()
    finally:
        connection.close()
