from __future__ import annotations

import json
import shlex
from pathlib import Path
from time import perf_counter

from .asset_utils import download_url, download_url_with_error, safe_filename_from_url, split_csv_urls
from .config import settings
from .crawler_adapter import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, MediaCrawlerAdapter
from .job_store import job_store
from .models import AuditSubject
from .prompts import FRAME_PROMPT, FUSION_PROMPT_TEMPLATE, IMAGE_PROMPT
from .qwen_client import QwenClient
from .video_processor import DemoAudioProcessor, DemoFrameExtractor


class AuditPipeline:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.qwen = QwenClient()
        self.crawler = MediaCrawlerAdapter()
        self.audio = DemoAudioProcessor()
        self.frames = DemoFrameExtractor()

    def run(self, request) -> None:
        try:
            job_store.update(self.job_id, status="running")
            job_store.log(self.job_id, "开始任务")

            if request.run_crawler:
                source_root = settings.outputs_dir / self.job_id
                crawl_dir = source_root / "crawler"
                job_store.log(self.job_id, "启动 MediaCrawler 爬取小红书")
                output = self.crawler.run_xhs_search(
                    keyword=request.keyword,
                    start_page=request.start_page,
                    max_notes=request.max_notes,
                    max_comments=request.max_comments,
                    max_concurrency=request.max_concurrency,
                    get_sub_comment=request.get_sub_comment,
                    save_root=crawl_dir,
                )
                if output.command:
                    job_store.log(self.job_id, f"MediaCrawler command: {shlex.join(map(str, output.command))}")
            else:
                source_root = self._resolve_source_root(request.source_output_id)
                crawl_dir = source_root / "crawler"
                job_store.log(self.job_id, f"跳过爬取，读取已有输出：{source_root.name}")
                output = self.crawler.load_latest_xhs_output(crawl_dir)

            job_store.log(
                self.job_id,
                f"crawl limits requested: max_notes={request.max_notes}, max_comments={request.max_comments}, "
                f"max_concurrency={request.max_concurrency}",
            )
            job_store.log(
                self.job_id,
                f"crawler output loaded: contents={len(output.contents)}, comments={len(output.comments)}",
            )
            subjects = self._build_subjects(
                output.contents,
                output.comments,
                source_root,
            )
            total_comments = sum(len(subject.comments) for subject in subjects)
            job_store.log(self.job_id, f"subjects built: subjects={len(subjects)}, comments={total_comments}")
            job_store.log(self.job_id, f"loaded subjects: {len(subjects)}")

            results = []
            total = min(len(subjects), max(0, request.analyze_limit))
            job_store.log(
                self.job_id,
                f"analysis limit applied: analyze_limit={request.analyze_limit}, will_analyze={total}",
            )
            batch_size = max(1, getattr(request, "analysis_batch_size", 5))
            for batch_start in range(0, total, batch_size):
                batch_end = min(batch_start + batch_size, total)
                job_store.log(self.job_id, f"开始分析第 {batch_start + 1}-{batch_end}/{total} 条")
                for idx, subject in enumerate(subjects[batch_start:batch_end], start=batch_start + 1):
                    job_store.log(self.job_id, f"分析第 {idx}/{total} 条：{subject.note_id}")
                    results.append(self._analyze_subject(subject))
                    job_store.update(self.job_id, items=results)
                    job_store.log(self.job_id, f"完成第 {idx}/{total} 条：{subject.note_id}")

            job_store.update(self.job_id, status="completed", items=results)
            job_store.log(self.job_id, "任务完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", error=str(exc))
            job_store.log(self.job_id, f"任务失败：{exc}")

    def run_local_video(self, video_path: Path, title: str = "", desc: str = "") -> None:
        try:
            job_store.update(self.job_id, status="running")
            job_store.log(self.job_id, "开始本地视频审核任务")
            subject = AuditSubject(
                platform="local",
                note_id=video_path.stem,
                url="",
                title=title or video_path.name,
                desc=desc,
                image_urls=[],
                video_urls=[],
                comments=[],
                local_image_paths=[],
                local_video_paths=[str(video_path)],
            )
            result = self._analyze_subject(subject)
            job_store.update(self.job_id, status="completed", items=[result])
            job_store.log(self.job_id, "本地视频审核任务完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", error=str(exc))
            job_store.log(self.job_id, f"本地视频审核任务失败：{exc}")

    def _resolve_source_root(self, source_output_id: str | None) -> Path:
        if not source_output_id:
            raise ValueError("source_output_id is required when run_crawler is false")

        outputs_root = settings.outputs_dir.resolve()
        source_root = (outputs_root / source_output_id).resolve()
        source_root.relative_to(outputs_root)
        if not source_root.exists() or not source_root.is_dir():
            raise ValueError(f"output not found: {source_output_id}")
        return source_root

    def _build_subjects(
        self,
        contents: list[dict],
        comments: list[dict],
        media_root: Path,
    ) -> list[AuditSubject]:
        comments_by_note: dict[str, list[dict]] = {}
        for comment in comments:
            comments_by_note.setdefault(str(comment.get("note_id", "")), []).append(comment)

        subjects = []
        for item in contents:
            note_id = str(item.get("note_id", ""))
            subjects.append(
                AuditSubject(
                    platform="xhs",
                    note_id=note_id,
                    url=item.get("note_url", ""),
                    title=item.get("title", ""),
                    desc=item.get("desc", ""),
                    image_urls=split_csv_urls(item.get("image_list")),
                    video_urls=split_csv_urls(item.get("video_url")),
                    comments=comments_by_note.get(note_id, []),
                    local_image_paths=self._find_local_media(media_root, note_id, "images", IMAGE_EXTENSIONS),
                    local_video_paths=self._find_local_media(media_root, note_id, "videos", VIDEO_EXTENSIONS),
                )
            )
        return subjects

    def _find_local_media(self, media_root: Path, note_id: str, media_type: str, extensions: set[str]) -> list[str]:
        candidates = [
            media_root / "crawler" / "xhs" / media_type / note_id,
            media_root / "assets" / note_id / media_type,
        ]
        files: list[Path] = []
        for folder in candidates:
            if folder.exists():
                files.extend(
                    path
                    for path in folder.iterdir()
                    if path.is_file() and path.suffix.lower() in extensions
                )
        return [str(path) for path in sorted(files)]

    def _analyze_subject(self, subject: AuditSubject) -> dict:
        note_dir = settings.outputs_dir / self.job_id / "assets" / subject.note_id
        started_at = perf_counter()
        job_store.log(
            self.job_id,
            f"笔记 {subject.note_id}：准备分析，本地图片 {len(subject.local_image_paths)}，"
            f"本地视频 {len(subject.local_video_paths)}，远程图片 {len(subject.image_urls)}，远程视频 {len(subject.video_urls)}",
        )

        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始图片分析")
        image_analyses = self._analyze_images(subject, note_dir / "images")
        job_store.log(self.job_id, f"笔记 {subject.note_id}：图片分析完成，共 {len(image_analyses)} 张")

        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始视频分析")
        video_results = self._analyze_videos(subject, note_dir / "videos")
        job_store.log(self.job_id, f"笔记 {subject.note_id}：视频分析完成，共 {len(video_results)} 个")

        comments_text = "\n".join(
            f"- comment:{c.get('comment_id')}: {c.get('content', '')}"
            for c in subject.comments[:30]
        )
        prompt = self._render_fusion_prompt(
            title=subject.title,
            desc=subject.desc,
            comments=comments_text,
            image_analyses=json.dumps(image_analyses, ensure_ascii=False, indent=2),
            video_transcripts=self._format_transcripts_for_prompt(video_results),
            frame_analyses=json.dumps([v.get("frames") for v in video_results], ensure_ascii=False, indent=2),
        )
        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始融合审核")
        audit = self.qwen.audit_text(prompt)
        elapsed = perf_counter() - started_at
        job_store.log(self.job_id, f"笔记 {subject.note_id}：融合审核完成，耗时 {elapsed:.1f}s")

        return {
            "note_id": subject.note_id,
            "url": subject.url,
            "title": subject.title,
            "desc": subject.desc,
            "summary": audit.get("summary", ""),
            "decision": audit.get("decision", "review"),
            "risk_level": audit.get("risk_level", "unknown"),
            "categories": audit.get("categories", []),
            "evidence": audit.get("evidence", []),
            "image_analyses": image_analyses,
            "video_results": video_results,
            "comments_count": len(subject.comments),
            "raw_audit": audit,
        }

    def _format_transcripts_for_prompt(self, video_results: list[dict]) -> str:
        """只把 segment 级文本与时间喂给融合模型，丢弃 word 级时间戳避免上下文爆炸。"""
        lines: list[str] = []
        for video in video_results:
            transcript = video.get("transcript") or {}
            segments = transcript.get("segments") or []
            if segments:
                for seg in segments:
                    text = (seg.get("text") or "").strip()
                    if not text:
                        continue
                    start = seg.get("start") or 0.0
                    end = seg.get("end") or 0.0
                    lines.append(f"[{float(start):.1f}-{float(end):.1f}] {text}")
            else:
                text = (transcript.get("text") or "").strip()
                if text:
                    lines.append(text)

        combined = "\n".join(lines).strip()
        if not combined:
            return "（无语音转写内容）"

        limit = settings.transcript_max_chars
        if limit > 0 and len(combined) > limit:
            original_len = len(combined)
            combined = combined[:limit] + f"\n…（转写过长，已截断，原始长度 {original_len} 字）"
        return combined

    def _render_fusion_prompt(
        self,
        title: str,
        desc: str,
        comments: str,
        image_analyses: str,
        video_transcripts: str,
        frame_analyses: str,
    ) -> str:
        replacements = {
            "{title}": title,
            "{desc}": desc,
            "{comments}": comments,
            "{image_analyses}": image_analyses,
            "{video_transcripts}": video_transcripts,
            "{frame_analyses}": frame_analyses,
        }
        prompt = FUSION_PROMPT_TEMPLATE
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        return prompt

    def _analyze_images(self, subject: AuditSubject, image_dir: Path) -> list[dict]:
        results = []
        local_images = [Path(path) for path in subject.local_image_paths if Path(path).exists()]
        local_limit = min(len(local_images), settings.max_images_per_note)
        for idx, image_path in enumerate(local_images[: settings.max_images_per_note]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：分析本地图片 {idx + 1}/{local_limit}")
            try:
                analysis = self.qwen.analyze_image(image_path, IMAGE_PROMPT)
                results.append({
                    "index": idx,
                    "url": "",
                    "local_path": str(image_path),
                    "source": "local",
                    **analysis,
                })
            except Exception as exc:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：本地图片 {idx + 1} 分析失败：{exc}")
                results.append({
                    "index": idx,
                    "url": "",
                    "local_path": str(image_path),
                    "source": "local",
                    "error": str(exc),
                })

        if results:
            return results

        remote_limit = min(len(subject.image_urls), settings.max_images_per_note)
        for idx, url in enumerate(subject.image_urls[: settings.max_images_per_note]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：下载并分析远程图片 {idx + 1}/{remote_limit}")
            target = image_dir / safe_filename_from_url(url, f"image_{idx:02d}")
            local = download_url(url, target)
            image_source = str(local or url)
            try:
                analysis = self.qwen.analyze_image(image_source, IMAGE_PROMPT)
                results.append({
                    "index": idx,
                    "url": url,
                    "local_path": str(local) if local else "",
                    "source": "download",
                    **analysis,
                })
            except Exception as exc:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：远程图片 {idx + 1} 分析失败：{exc}")
                results.append({
                    "index": idx,
                    "url": url,
                    "local_path": str(local) if local else "",
                    "source": "download",
                    "error": str(exc),
                })
        return results

    def _analyze_videos(self, subject: AuditSubject, video_dir: Path) -> list[dict]:
        results = []
        local_videos = [Path(path) for path in subject.local_video_paths if Path(path).exists()]
        for idx, local in enumerate(local_videos[:1]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：使用本地视频 {local}")
            results.append(self._analyze_video_file(
                index=idx,
                video_path=local,
                video_dir=video_dir,
                url="",
                source="local",
            ))

        if results:
            return results

        if subject.video_urls:
            job_store.log(
                self.job_id,
                f"笔记 {subject.note_id}：本地未找到视频文件，将尝试远程下载。"
                f"期望目录：crawler/xhs/videos/{subject.note_id} 或 assets/{subject.note_id}/videos",
            )

        for idx, url in enumerate(subject.video_urls[:1]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：开始下载远程视频 {idx + 1}/1")
            target = video_dir / f"video_{idx:02d}.mp4"
            download = download_url_with_error(url, target)
            local = download.path
            if not local:
                error = download.error or "video download failed"
                job_store.log(self.job_id, f"笔记 {subject.note_id}：视频下载失败：{error}")
                results.append({
                    "index": idx,
                    "url": url,
                    "error": error,
                    "status_code": download.status_code,
                })
                continue

            results.append(self._analyze_video_file(
                index=idx,
                video_path=local,
                video_dir=video_dir,
                url=url,
                source="download",
            ))
        return results

    def _analyze_video_file(self, index: int, video_path: Path, video_dir: Path, url: str, source: str) -> dict:
        started_at = perf_counter()
        video_label = f"视频 {index + 1}"
        job_store.log(self.job_id, f"{video_label}：开始处理 {video_path}")
        transcript = {"text": "", "segments": []}
        frame_analyses = []
        errors = []

        try:
            job_store.log(self.job_id, f"{video_label}：开始抽取音频")
            audio_path = self.audio.extract_audio(video_path, video_dir / f"audio_{index:02d}")
            if audio_path:
                job_store.log(self.job_id, f"{video_label}：音频抽取完成，开始 Whisper 转写")
                transcript = self.audio.transcribe(audio_path)
                job_store.log(
                    self.job_id,
                    f"{video_label}：Whisper 转写完成，device={transcript.get('device', 'unknown')}，"
                    f"文本长度={len(transcript.get('text', ''))}",
                )
            else:
                errors.append("audio extraction failed")
                job_store.log(self.job_id, f"{video_label}：音频抽取失败，跳过转写")
        except Exception as exc:
            errors.append(f"audio/transcribe failed: {exc}")
            job_store.log(self.job_id, f"{video_label}：音频/转写失败：{exc}")

        try:
            job_store.log(self.job_id, f"{video_label}：开始抽取关键帧，最多 {settings.max_video_frames} 帧")
            frame_infos = self.frames.extract_keyframes(
                video_path=video_path,
                output_dir=video_dir / f"frames_{index:02d}",
                max_frames=settings.max_video_frames,
            )
            job_store.log(self.job_id, f"{video_label}：关键帧抽取完成，共 {len(frame_infos)} 帧")
            for frame_idx, frame in enumerate(frame_infos, start=1):
                job_store.log(self.job_id, f"{video_label}：分析关键帧 {frame_idx}/{len(frame_infos)}")
                analysis = self.qwen.analyze_image(frame["path"], FRAME_PROMPT)
                frame_analyses.append({**frame, **analysis})
        except Exception as exc:
            errors.append(f"frame analysis failed: {exc}")
            job_store.log(self.job_id, f"{video_label}：关键帧分析失败：{exc}")

        job_store.log(self.job_id, f"{video_label}: suspicious flash-frame detection disabled; using keyframes only")

        elapsed = perf_counter() - started_at
        job_store.log(self.job_id, f"{video_label}：处理完成，耗时 {elapsed:.1f}s")

        result = {
            "index": index,
            "url": url,
            "local_path": str(video_path),
            "source": source,
            "transcript": transcript,
            "frames": frame_analyses,
        }
        if errors:
            result["errors"] = errors
        return result
