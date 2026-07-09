from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
from typing import Callable

from .config import settings

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
SUPPORTED_PLATFORMS = ("xhs", "dy", "ks")
PLATFORM_DATA_DIRS = {
    "xhs": "xhs",
    "dy": "douyin",
    "ks": "kuaishou",
}


@dataclass
class CrawlOutput:
    platform: str
    contents: list[dict]
    comments: list[dict]
    output_dir: Path
    creators: list[dict] = field(default_factory=list)
    command: list[str] = field(default_factory=list)


ProgressCallback = Callable[[int, int], None]
ContentCallback = Callable[[list[dict], list[dict]], None]
StopChecker = Callable[[], bool]


class MediaCrawlerAdapter:
    def __init__(self, media_crawler_dir: Path | None = None):
        self.media_crawler_dir = media_crawler_dir or settings.media_crawler_dir

    def run_search(
        self,
        platform: str,
        keyword: str,
        start_page: int,
        max_notes: int,
        max_comments: int,
        max_concurrency: int,
        get_sub_comment: bool,
        save_root: Path,
        progress_callback: ProgressCallback | None = None,
        content_callback: ContentCallback | None = None,
        stream_items: bool = False,
        stop_checker: StopChecker | None = None,
    ) -> CrawlOutput:
        self._validate_platform(platform)
        command = [
            *self._base_command(platform),
            "--platform",
            platform,
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
            "--get_media",
            "true",
            "--stream_items",
            "true" if stream_items else "false",
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(save_root),
        ]
        return self._run_command(
            command=command,
            save_root=save_root,
            platform=platform,
            max_notes=max_notes,
            progress_callback=progress_callback,
            content_callback=content_callback,
            stop_checker=stop_checker,
        )

    def run_creator(
        self,
        platform: str,
        creator_id: str,
        max_notes: int,
        max_comments: int,
        max_concurrency: int,
        get_sub_comment: bool,
        save_root: Path,
        progress_callback: ProgressCallback | None = None,
        content_callback: ContentCallback | None = None,
        stream_items: bool = False,
        stop_checker: StopChecker | None = None,
    ) -> CrawlOutput:
        self._validate_platform(platform)
        command = [
            *self._base_command(platform),
            "--platform",
            platform,
            "--lt",
            "qrcode",
            "--type",
            "creator",
            "--creator_id",
            creator_id,
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
            "--get_media",
            "true",
            "--stream_items",
            "true" if stream_items else "false",
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(save_root),
        ]
        return self._run_command(
            command=command,
            save_root=save_root,
            platform=platform,
            max_notes=max_notes,
            progress_callback=progress_callback,
            content_callback=content_callback,
            stop_checker=stop_checker,
        )

    def _run_command(
        self,
        command: list[str],
        save_root: Path,
        platform: str,
        max_notes: int,
        progress_callback: ProgressCallback | None,
        content_callback: ContentCallback | None,
        stop_checker: StopChecker | None = None,
    ) -> CrawlOutput:
        save_root.mkdir(parents=True, exist_ok=True)

        stdout_path = save_root / "mediacrawler_stdout.log"
        stderr_path = save_root / "mediacrawler_stderr.log"
        with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as stderr_file:
            completed = subprocess.Popen(
                command,
                cwd=self.media_crawler_dir,
                env=self._subprocess_env(),
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            last_count = -1
            seen_content_ids: set[str] = set()
            while completed.poll() is None:
                if stop_checker and stop_checker():
                    completed.terminate()
                    try:
                        completed.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        completed.kill()
                        completed.wait(timeout=5)
                    break
                current_count = self._latest_content_count(save_root, platform)
                if current_count != last_count:
                    last_count = current_count
                    if progress_callback:
                        progress_callback(min(current_count, max_notes), max_notes)
                if content_callback:
                    self._emit_new_content(save_root, platform, seen_content_ids, content_callback)
                sleep(1)

        final_count = self._latest_content_count(save_root, platform)
        if final_count != last_count and progress_callback:
            progress_callback(min(final_count, max_notes), max_notes)
        if content_callback:
            self._emit_new_content(save_root, platform, seen_content_ids, content_callback)

        if completed.returncode != 0 and not (stop_checker and stop_checker()):
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
            raise RuntimeError(
                "MediaCrawler failed\n"
                f"STDOUT:\n{stdout[-4000:]}\n"
                f"STDERR:\n{stderr[-4000:]}"
            )

        output = self.load_latest_output(save_root, platform)
        output.command = command
        return output

    def run_xhs_search(self, **kwargs) -> CrawlOutput:
        return self.run_search(platform="xhs", **kwargs)

    def _base_command(self, platform: str) -> list[str]:
        return self._build_runner()

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        path_parts = [
            str(settings.root_dir / "tools" / "node" / "bin"),
            env.get("PATH", ""),
        ]
        env["PATH"] = os.pathsep.join(part for part in path_parts if part)
        env.setdefault("EXECJS_RUNTIME", "Node")
        return env

    def _build_runner(self) -> list[str]:
        uv_path = shutil.which("uv")
        if uv_path:
            return [uv_path, "run", "main.py"]

        for venv_python in (
            self.media_crawler_dir / ".venv" / "bin" / "python",
            self.media_crawler_dir / ".venv" / "Scripts" / "python.exe",
        ):
            if venv_python.exists():
                return [str(venv_python), "main.py"]

        return ["python", "main.py"]

    def load_latest_output(self, save_root: Path, platform: str) -> CrawlOutput:
        self._validate_platform(platform)
        platform_dir = save_root / PLATFORM_DATA_DIRS[platform] / "jsonl"
        if not platform_dir.exists():
            return CrawlOutput(platform=platform, contents=[], comments=[], output_dir=save_root)

        content_files = sorted(platform_dir.glob("*_contents_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        comment_files = sorted(platform_dir.glob("*_comments_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        creator_files = sorted(platform_dir.glob("*_creators_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

        contents = self._read_jsonl(content_files[0]) if content_files else []
        comments = self._read_jsonl(comment_files[0]) if comment_files else []
        creators = self._read_jsonl(creator_files[0]) if creator_files else []
        return CrawlOutput(platform=platform, contents=contents, comments=comments, creators=creators, output_dir=save_root)

    def load_latest_xhs_output(self, save_root: Path) -> CrawlOutput:
        return self.load_latest_output(save_root, "xhs")

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

            for platform in SUPPORTED_PLATFORMS:
                loaded = self.load_latest_output(crawl_root, platform)
                if not loaded.contents and not loaded.comments:
                    continue

                platform_dir = PLATFORM_DATA_DIRS[platform]
                image_count = self._count_files(crawl_root / platform_dir / "images", IMAGE_EXTENSIONS)
                video_count = self._count_files(crawl_root / platform_dir / "videos", VIDEO_EXTENSIONS)
                outputs.append({
                    "id": output_root.name,
                    "platform": platform,
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

    def _latest_content_count(self, save_root: Path, platform: str) -> int:
        platform_dir = save_root / PLATFORM_DATA_DIRS[platform] / "jsonl"
        if not platform_dir.exists():
            return 0
        content_files = sorted(platform_dir.glob("*_contents_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not content_files:
            return 0
        return len(self._read_jsonl(content_files[0]))

    def _emit_new_content(
        self,
        save_root: Path,
        platform: str,
        seen_content_ids: set[str],
        callback: ContentCallback,
    ) -> None:
        output = self.load_latest_output(save_root, platform)
        new_contents = []
        for item in output.contents:
            content_id = self._content_identity(item, platform)
            if not content_id or content_id in seen_content_ids:
                continue
            seen_content_ids.add(content_id)
            new_contents.append(item)
        if new_contents:
            for item in new_contents:
                callback([item], output.comments)
        else:
            callback([], output.comments)

    def _content_identity(self, item: dict, platform: str) -> str:
        fields = {
            "xhs": ("note_id", "note_url"),
            "dy": ("aweme_id", "note_id", "aweme_url"),
            "ks": ("video_id", "note_id", "video_url"),
        }.get(platform, ("note_id",))
        for field in fields:
            value = item.get(field)
            if value:
                return str(value)
        return ""

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

    def _validate_platform(self, platform: str) -> None:
        if platform not in SUPPORTED_PLATFORMS:
            supported = ", ".join(sorted(SUPPORTED_PLATFORMS))
            raise ValueError(f"Unsupported platform: {platform}. Supported: {supported}")
