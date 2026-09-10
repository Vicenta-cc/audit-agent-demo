from __future__ import annotations

import mimetypes
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


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
) -> DownloadResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers=media_headers(platform, referer),
            allow_redirects=True,
        )
        response.raise_for_status()
        output_path.write_bytes(response.content)
        return DownloadResult(path=output_path, status_code=response.status_code)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        return DownloadResult(path=None, error=f"HTTP {status_code}: {exc}", status_code=status_code)
    except requests.RequestException as exc:
        return DownloadResult(path=None, error=f"request failed: {exc}")
    except Exception as exc:
        return DownloadResult(path=None, error=f"download failed: {exc}")
