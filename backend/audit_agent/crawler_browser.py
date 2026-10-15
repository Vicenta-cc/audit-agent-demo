"""Share MediaCrawler's account browser implementation with account login."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

from .config import settings


def account_profile_directory(account_id: str, profile_root: Path | None = None) -> Path:
    """Return the persistent Douyin profile directory without creating it."""
    if not account_id.strip():
        raise ValueError("抖音采集必须选择已保存的账号")
    key = hashlib.sha256(("dy:" + account_id).encode()).hexdigest()
    return (profile_root or (settings.crawler_camoufox_state_root / "profiles")).expanduser().resolve() / key


def account_profile_owns_auth(account_id: str, profile_root: Path | None = None) -> bool:
    """Whether the persistent profile has taken ownership of login state."""
    return (account_profile_directory(account_id, profile_root) / "auth-imported").is_file()


def account_browser_env(account_id: str, profile_root: Path | None = None) -> dict[str, str]:
    if not account_id.strip():
        raise ValueError("抖音采集必须选择已保存的账号")
    return {
        "MEDIACRAWLER_ACCOUNT_ID": account_id,
        "MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT": str(
            (profile_root or (settings.crawler_camoufox_state_root / "profiles")).expanduser().resolve()
        ),
        "MEDIACRAWLER_DY_BROWSER_ENGINE": "camoufox",
    }


def load_account_browser(media_crawler_dir: Path | None = None):
    path = (media_crawler_dir or settings.media_crawler_dir).resolve() / "tools" / "camoufox_browser.py"
    if not path.is_file():
        raise RuntimeError("MediaCrawler 缺少账号浏览器适配器，请安装配套的 Camoufox 接入版本")
    crawler_root = str(path.parent.parent)
    if crawler_root not in sys.path:
        sys.path.insert(0, crawler_root)
    spec = importlib.util.spec_from_file_location("audit_account_camoufox_browser", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not all(callable(getattr(module, name, None))
               for name in ("launch_account_context", "prepare_account_login")):
        raise RuntimeError("MediaCrawler 账号浏览器适配器版本不匹配，请更新配套版本")
    return module


def scheduler_env() -> dict[str, str]:
    return {
        "MEDIACRAWLER_PROXY_ECHO_URL": getattr(settings, "crawler_proxy_echo_url", ""),
        "MEDIACRAWLER_PROXY_PROBE_MAX_CALLS": str(getattr(settings, "crawler_proxy_probe_budget", 96)),
        "MEDIACRAWLER_PROXY_PROBE_BUDGET_ID": "",
        "MEDIACRAWLER_PROXY_PROBE_TTL": str(getattr(settings, "crawler_proxy_probe_ttl", 300)),
        "MEDIACRAWLER_CONTENT_PACING_ENABLED": str(settings.crawler_content_pacing_enabled).lower(),
        "MEDIACRAWLER_REQUEST_SCHEDULER_DB": str(settings.request_scheduler_db),
        "MEDIACRAWLER_REQUEST_MIN_INTERVAL": str(settings.request_min_interval),
        "MEDIACRAWLER_REQUESTS_PER_MINUTE": str(settings.requests_per_minute),
        "MEDIACRAWLER_REQUEST_CONCURRENCY": str(settings.request_concurrency),
        "MEDIACRAWLER_MEDIA_REQUEST_INTERVAL": str(settings.media_request_interval),
        "MEDIACRAWLER_REQUEST_COOLDOWN_SECONDS": str(settings.request_cooldown_seconds),
    }


def load_request_scheduler():
    crawler_root = str(settings.media_crawler_dir.resolve())
    if crawler_root not in sys.path:
        sys.path.insert(0, crawler_root)
    path = settings.media_crawler_dir.resolve() / "tools" / "persistent_request_gate.py"
    if not path.is_file():
        raise RuntimeError("缺少配套请求调度器，请安装统一限速版 MediaCrawler")
    spec = importlib.util.spec_from_file_location("audit_shared_request_scheduler", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, 'RequestScheduler') or not hasattr(module, 'configured_gate'):
        raise RuntimeError("请求调度器版本不匹配，请安装统一限速版 MediaCrawler")
    return module
