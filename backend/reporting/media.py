"""Stable archive locations for media referenced by frozen report snapshots."""

import hashlib
import mimetypes
import re
from pathlib import Path

from fastapi.responses import FileResponse, Response, StreamingResponse


def snapshot_asset_relative_path(saved_path: str) -> Path:
    path = Path(saved_path)
    if path.is_absolute():
        # Never serve an old absolute filesystem path directly. Its preserved
        # copy belongs to the report source job inside the managed outputs root.
        digest = hashlib.sha256(saved_path.encode("utf-8")).hexdigest()
        return Path("report-assets") / f"{digest}{path.suffix}"
    return path


def snapshot_media_response(target: Path, range_header: str | None):
    """Support browser playback/seeking with the installed Starlette version."""
    if not range_header or not range_header.startswith("bytes=") or "," in range_header:
        return FileResponse(target, headers={"Accept-Ranges": "bytes"})
    size = target.stat().st_size
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    if not match or not any(match.groups()) or size == 0:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        start, end = max(0, size - int(last)), size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def chunks():
        with target.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(chunks(), status_code=206,
        media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}",
                 "Content-Length": str(end - start + 1)})
