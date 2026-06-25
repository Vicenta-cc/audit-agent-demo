from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass
class XhsCrawlOutput:
    contents: list[dict]
    comments: list[dict]
    output_dir: Path
    command: list[str] = field(default_factory=list)


class MediaCrawlerAdapter:
    def __init__(self, media_crawler_dir: Path | None = None):
        self.media_crawler_dir = media_crawler_dir or settings.media_crawler_dir

    def run_xhs_search(
        self,
        keyword: str,
        start_page: int,
        max_notes: int,
        max_comments: int,
        max_concurrency: int,
        get_sub_comment: bool,
        save_root: Path,
    ) -> XhsCrawlOutput:
        save_root.mkdir(parents=True, exist_ok=True)

        # MediaCrawler keeps jsonl file names date-based, so use an isolated save path per job.
        runner = self._build_runner()
        command = [
            *runner,
            "--platform",
            "xhs",
            "--lt",
            "qrcode",
            "--type",
            "search",
            "--keywords",
            keyword,
            "--start",
            str(start_page),
            "--crawler_max_notes_count",
            str(max_notes),
            "--max_comments_count_singlenotes",
            str(max_comments),
            "--max_concurrency_num",
            str(max_concurrency),
            "--get_comment",
            "true",
            "--get_sub_comment",
            "true" if get_sub_comment else "false",
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(save_root),
        ]

        completed = subprocess.run(
            command,
            cwd=self.media_crawler_dir,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "MediaCrawler failed\n"
                f"STDOUT:\n{completed.stdout[-4000:]}\n"
                f"STDERR:\n{completed.stderr[-4000:]}"
            )

        output = self.load_latest_xhs_output(save_root)
        output.command = command
        return output

    def _build_runner(self) -> list[str]:
        uv_path = shutil.which("uv")
        if uv_path:
            return [uv_path, "run", "main.py"]

        venv_python = self.media_crawler_dir / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return [str(venv_python), "main.py"]

        return ["python", "main.py"]

    def load_latest_xhs_output(self, save_root: Path) -> XhsCrawlOutput:
        xhs_dir = save_root / "xhs" / "jsonl"
        if not xhs_dir.exists():
            return XhsCrawlOutput(contents=[], comments=[], output_dir=save_root)

        content_files = sorted(xhs_dir.glob("*_contents_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        comment_files = sorted(xhs_dir.glob("*_comments_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

        contents = self._read_jsonl(content_files[0]) if content_files else []
        comments = self._read_jsonl(comment_files[0]) if comment_files else []
        return XhsCrawlOutput(contents=contents, comments=comments, output_dir=save_root)

    def list_existing_outputs(self) -> list[dict]:
        outputs = []
        root = settings.outputs_dir
        if not root.exists():
            return outputs

        for output_root in root.iterdir():
            if not output_root.is_dir():
                continue
            crawl_root = output_root / "crawler"
            if not crawl_root.exists():
                continue

            loaded = self.load_latest_xhs_output(crawl_root)
            if not loaded.contents and not loaded.comments:
                continue

            image_count = self._count_files(crawl_root / "xhs" / "images", IMAGE_EXTENSIONS)
            video_count = self._count_files(crawl_root / "xhs" / "videos", VIDEO_EXTENSIONS)
            outputs.append({
                "id": output_root.name,
                "path": str(output_root),
                "contents_count": len(loaded.contents),
                "comments_count": len(loaded.comments),
                "image_count": image_count,
                "video_count": video_count,
                "modified_at": self._latest_mtime(output_root),
            })

        return sorted(outputs, key=lambda item: item["modified_at"], reverse=True)

    def _count_files(self, root: Path, extensions: set[str]) -> int:
        if not root.exists():
            return 0
        return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions)

    def _latest_mtime(self, root: Path) -> float:
        latest = root.stat().st_mtime
        for path in root.rglob("*"):
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
        return latest

    def _read_jsonl(self, path: Path) -> list[dict]:
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows
