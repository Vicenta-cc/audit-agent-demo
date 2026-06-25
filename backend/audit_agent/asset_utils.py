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


def download_url_with_error(url: str, output_path: Path, timeout: int = 30) -> DownloadResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "*/*",
                "Referer": "https://www.xiaohongshu.com/",
            },
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
