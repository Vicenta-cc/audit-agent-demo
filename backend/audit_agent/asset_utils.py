from __future__ import annotations

import importlib.util
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin
from contextlib import nullcontext

import requests


@lru_cache(maxsize=8)
def _load_trial_limits(path: str):
    spec = importlib.util.spec_from_file_location("audit_asset_trial_limits", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trial_media_limits(snapshot):
    from .config import settings
    from .crawler_route import CollectionRouteError
    trial_id = str(getattr(settings, "crawler_proxy_trial_id", "") or os.environ.get("MEDIACRAWLER_PROXY_TRIAL_ID", "")).strip()
    if not trial_id:
        return None
    if snapshot is None:
        raise CollectionRouteError("PROXY_CONFIG_INVALID")
    path = str(settings.media_crawler_dir.resolve() / "tools" / "trial_limits.py")
    try:
        return _load_trial_limits(path).from_env(snapshot.scheduler_db_path, env={"MEDIACRAWLER_PROXY_TRIAL_ID": trial_id})
    except RuntimeError as exc:
        code = "PROBE_BUDGET_EXHAUSTED" if str(exc) == "PROBE_BUDGET_EXHAUSTED" else "PROXY_CONFIG_INVALID"
        raise CollectionRouteError(code) from None
    except (ValueError, OSError, ImportError, AttributeError):
        raise CollectionRouteError("PROXY_CONFIG_INVALID") from None


@dataclass
class DownloadResult:
    path: Path | None
    error: str = ""
    status_code: int | None = None


def split_csv_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def safe_filename_from_url(url: str, fallback: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix
    if not suffix:
        suffix = ".jpg"
    return f"{fallback}{suffix}"


def download_url(url: str, output_path: Path, timeout: int = 30) -> Path | None:
    return download_url_with_error(url, output_path, timeout).path


def media_headers(platform: str = "xhs", referer: str = "") -> dict[str, str]:
    platform_referers = {
        "xhs": "https://www.xiaohongshu.com/",
        "dy": "https://www.douyin.com/",
        "ks": "https://www.kuaishou.com/",
    }
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer or platform_referers.get(platform, platform_referers["xhs"]),
    }


def download_url_with_error(
    url: str,
    output_path: Path,
    timeout: int = 30,
    platform: str = "xhs",
    referer: str = "",
    *,
    collection_route_provenance: dict | None = None,
    owner_id: str | None = None,
    execution_id: str | None = None,
    route_context=None,
    verify: bool | str = True,
    required_route: bool = False,
) -> DownloadResult:
    from .config import settings
    from . import crawler_route
    trial_id = str(getattr(settings, "crawler_proxy_trial_id", "") or os.environ.get("MEDIACRAWLER_PROXY_TRIAL_ID", "")).strip()
    strict = (bool(trial_id) or bool(getattr(settings, "crawler_proxy_enabled", False)) or required_route or
              getattr(route_context, "snapshot", None) is not None or collection_route_provenance is not None)
    if strict:
        if not collection_route_provenance or not owner_id or not execution_id:
            raise crawler_route.CollectionRouteError("SUPPLEMENT_DEFERRED")
        try:
            source = crawler_route.route_contract().validate_collection_route_provenance(collection_route_provenance)
        except Exception as exc:
            if getattr(exc, "code", None):
                raise crawler_route.CollectionRouteError(exc.code) from None
            raise
        route = crawler_route.authorize_source_route(source, owner_id, execution_id, route_context=route_context)
    else:
        route = nullcontext(None)
    if verify is not True and not (isinstance(verify, str) and Path(verify).is_file()):
        raise crawler_route.CollectionRouteError("PROXY_CONFIG_INVALID")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    limits = None
    media_token = None
    temporary_path = None
    try:
        with route as bundle:
            snapshot = bundle.snapshot if bundle is not None else None
            limits = _trial_media_limits(snapshot)
            if limits is not None:
                media_token = limits.begin_media()
            if snapshot is not None:
                # The runtime owns probe, reservation and streaming in one async task.
                # The private file remains unpublished until the complete hop succeeds.
                with tempfile.NamedTemporaryFile(mode="wb", dir=output_path.parent,
                                                 prefix="." + output_path.name + ".", suffix=".partial",
                                                 delete=False) as stream:
                    temporary_path = Path(stream.name)
                    def accept_chunk(chunk):
                        if limits is not None:
                            limits.reserve_media_bytes(media_token, len(chunk))
                        stream.write(chunk)
                    current_url = url
                    for hop in range(11):
                        parsed = urlparse(current_url)
                        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                            raise crawler_route.CollectionRouteError("PROXY_CONFIG_INVALID")
                        hop_timeout = min(timeout, limits.media_timeout(media_token)) if limits is not None else timeout
                        try:
                            status, response_headers = crawler_route.download_asset_hop(
                                bundle, current_url, media_headers(platform, referer), hop_timeout, verify, accept_chunk,
                                media_limits=limits, media_token=media_token)
                        except crawler_route.CollectionRouteError:
                            if limits is not None:
                                try:
                                    limits.media_timeout(media_token)
                                except RuntimeError:
                                    raise crawler_route.CollectionRouteError("PROBE_BUDGET_EXHAUSTED") from None
                            raise
                        if status in {301, 302, 303, 307, 308}:
                            location = response_headers.get("location")
                            if not location or hop == 10:
                                return DownloadResult(None, "media redirect limit exceeded")
                            current_url = urljoin(current_url, location)
                            continue
                        if status >= 400:
                            return DownloadResult(None, f"HTTP {status}", status)
                        if limits is not None:
                            limits.media_timeout(media_token)
                            limits.finish_media(media_token)
                            media_token = None
                        stream.flush()
                        temporary_path.replace(output_path)
                        temporary_path = None
                        return DownloadResult(output_path, status_code=status)
                return DownloadResult(None, "media redirect limit exceeded")
            # Legacy, never-required routes retain the requests transport.
            with requests.Session() as session:
                current_url = url
                for hop in range(11):
                    parsed = urlparse(current_url)
                    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                        raise crawler_route.CollectionRouteError("PROXY_CONFIG_INVALID")
                    response = session.get(current_url, timeout=timeout,
                                           headers=media_headers(platform, referer),
                                           allow_redirects=False, verify=verify)
                    try:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location or hop == 10:
                                return DownloadResult(None, "media redirect limit exceeded")
                            current_url = urljoin(current_url, location)
                            continue
                        response.raise_for_status()
                        output_path.write_bytes(response.content)
                        return DownloadResult(output_path, status_code=response.status_code)
                    finally:
                        response.close()
    except crawler_route.CollectionRouteError:
        raise
    except RuntimeError as exc:
        if str(exc) == "PROBE_BUDGET_EXHAUSTED":
            raise crawler_route.CollectionRouteError("PROBE_BUDGET_EXHAUSTED") from None
        raise
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        return DownloadResult(path=None, error=f"HTTP {status_code}", status_code=status_code)
    except requests.RequestException:
        return DownloadResult(path=None, error="media request failed")
    except OSError:
        return DownloadResult(path=None, error="media file write failed")
    finally:
        try:
            if limits is not None and media_token is not None:
                try:
                    limits.finish_media(media_token)
                except RuntimeError as exc:
                    if str(exc) == "PROBE_BUDGET_EXHAUSTED":
                        raise crawler_route.CollectionRouteError("PROBE_BUDGET_EXHAUSTED") from None
                    raise
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return DownloadResult(path=None, error="media redirect limit exceeded")
