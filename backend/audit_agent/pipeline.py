from __future__ import annotations

import json
import queue
import shutil
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

from .asset_utils import download_url_with_error, safe_filename_from_url, split_csv_urls
from .config import settings
from .crawler_adapter import IMAGE_EXTENSIONS, PLATFORM_DATA_DIRS, VIDEO_EXTENSIONS, MediaCrawlerAdapter
from .evidence_groups import build_evidence_groups
from .ingestion import AuditResultStore, BatchWriter, IngestionStore, content_identity
from .job_store import job_store
from .models import AuditSubject
from .ocr_processor import VideoOCRTracker
from .prompts import get_prompt_set
from .qwen_client import QwenClient
from .translation import TranslationProcessor
from .video_processor import DemoAudioProcessor, DemoFrameExtractor, MMSAudioProcessor


_crawler_lock = threading.Lock()


def _severity_rank(severity) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(severity).lower(), 0)


class AuditPipeline:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.qwen = QwenClient()
        self.crawler = MediaCrawlerAdapter()
        self.audio = DemoAudioProcessor()
        self.mms_audio = MMSAudioProcessor()
        self.translator = TranslationProcessor()
        self.frames = DemoFrameExtractor()
        self.ocr = VideoOCRTracker(self.translator)
        self.ingestion = IngestionStore()
        self.audit_results = AuditResultStore()
        self.prompt_set = get_prompt_set("soft")
        self.audit_config_revision_id = ""

    def run(self, request) -> None:
        try:
            job_store.update(self.job_id, status="running")
            job_store.log(self.job_id, "开始任务")
            job_snapshot = job_store.get(self.job_id) or {}
            self.audit_config_revision_id = str(job_snapshot.get("current_audit_config_revision_id") or "")
            self._set_prompt_context(
                self._prompt_category_from_source(request),
                self._prompt_profile_from_source(request),
            )

            def control() -> dict:
                return job_store.control(self.job_id)

            def analysis_stop_requested() -> bool:
                current = control()
                return bool(current.get("stop_all_requested") or current.get("analysis_stop_requested"))

            def wait_if_analysis_paused() -> bool:
                return not analysis_stop_requested()

            if request.run_crawler:
                source_root = settings.outputs_dir / self.job_id
                crawl_dir = source_root / "crawler"
                job_store.log(self.job_id, f"等待 MediaCrawler 爬取锁：{request.platform}")
                last_progress = {"done": -1}
                results: list[dict] = []
                analyzed_ids: set[str] = set()
                analyze_limit = max(0, request.analyze_limit)
                stream_analysis = settings.stream_crawl_analysis
                use_batch_ingestion = stream_analysis and settings.batch_ingestion_enabled
                skip_final_supplement = use_batch_ingestion
                stream_queue: queue.Queue[dict | tuple[list[dict], list[dict]] | None] = queue.Queue()
                analysis_errors: list[BaseException] = []
                batch_writer = BatchWriter(
                    job_id=self.job_id,
                    platform=request.platform,
                    keyword=request.keyword if request.crawl_mode == "search" else (request.creator_url or request.creator_id),
                    category=request.lexicon_category if getattr(request, "keyword_source", "keyword") == "lexicon" else "",
                    root=source_root,
                )
                raw_items_dir = source_root / "raw_items"
                stop_flusher = threading.Event()

                def crawl_stop_requested() -> bool:
                    current = control()
                    return bool(current.get("crawl_stop_requested") or current.get("stop_all_requested"))

                def log_crawl_progress(done: int, total: int) -> None:
                    if done <= 0 or done == last_progress["done"]:
                        return
                    last_progress["done"] = done
                    job_store.log(self.job_id, f"已爬取 {done}/{total} 条")

                def enqueue_stream_batch(contents: list[dict], comments: list[dict]) -> None:
                    if not contents:
                        return
                    if not use_batch_ingestion:
                        stream_queue.put((contents, comments))
                        return
                    for batch_path in batch_writer.add(contents, comments):
                        ingest_completed_batch(batch_path)

                def ingest_completed_batch(batch_path: Path) -> None:
                    queued = self.ingestion.ingest_batch(batch_path, raw_items_dir)
                    job_store.log(
                        self.job_id,
                        f"ingestion 完成 batch：{batch_path.name}，新内容 {len(queued)} 条",
                    )
                    for ref in queued:
                        stream_queue.put(ref)

                def flush_batches_periodically() -> None:
                    while not stop_flusher.is_set():
                        for batch_path in batch_writer.flush_due():
                            ingest_completed_batch(batch_path)
                        stop_flusher.wait(1)

                def analyze_stream_ref(ref: dict) -> None:
                    if analyze_limit <= 0:
                        return
                    if analyze_limit and len(results) >= analyze_limit:
                        return
                    if not wait_if_analysis_paused():
                        return
                    content_key = str(ref.get("content_key") or "")
                    subjects = self._build_subjects(
                        request.platform,
                        [ref["item"]],
                        ref.get("comments", []),
                        source_root,
                    )
                    for subject in subjects:
                        subject_key = content_key or content_identity(ref["item"], request.platform) or subject.note_id
                        if not subject_key or subject_key in analyzed_ids:
                            continue
                        if analyze_limit and len(results) >= analyze_limit:
                            return
                        if not wait_if_analysis_paused():
                            return
                        analyzed_ids.add(subject_key)
                        job_store.log(self.job_id, f"边抓边分析：{subject.note_id}")
                        try:
                            self.ingestion.mark_content_status(
                                request.platform,
                                subject_key,
                                "analyzing",
                                task_id=self.job_id,
                            )
                            result = self._analyze_subject(subject)
                            result_path = self._write_result_json(subject.note_id, result)
                            persisted = self._persist_audit_result(
                                platform=request.platform,
                                content_key=subject_key,
                                result=result,
                                result_path=result_path,
                                content_id=ref.get("content_id"),
                            )
                            self.ingestion.mark_content_status(
                                request.platform,
                                subject_key,
                                "completed",
                                str(result_path),
                                task_id=self.job_id,
                                audit_result_id=int((persisted or {}).get("audit_result_id") or (persisted or {}).get("id") or 0) or None,
                            )
                            results.append(persisted or result)
                        except Exception:
                            self.ingestion.mark_content_status(
                                request.platform,
                                subject_key,
                                "failed",
                                task_id=self.job_id,
                            )
                            raise
                        job_store.update(self.job_id, items=results)
                        job_store.log(self.job_id, f"边抓边分析完成：{subject.note_id}")

                def analyze_stream_batch(contents: list[dict], comments: list[dict]) -> None:
                    if analyze_limit <= 0:
                        return
                    if analyze_limit and len(results) >= analyze_limit:
                        return
                    if not wait_if_analysis_paused():
                        return
                    subjects = self._build_subjects(
                        request.platform,
                        contents,
                        comments,
                        source_root,
                    )
                    for subject in subjects:
                        if not subject.note_id or subject.note_id in analyzed_ids:
                            continue
                        if analyze_limit and len(results) >= analyze_limit:
                            return
                        if not wait_if_analysis_paused():
                            return
                        analyzed_ids.add(subject.note_id)
                        subject_key = subject.note_id
                        job_store.log(self.job_id, f"边抓边分析：{subject.note_id}")
                        try:
                            self.ingestion.mark_content_status(
                                request.platform,
                                subject_key,
                                "analyzing",
                                task_id=self.job_id,
                            )
                            result = self._analyze_subject(subject)
                            result_path = self._write_result_json(subject.note_id, result)
                            persisted = self._persist_audit_result(
                                platform=request.platform,
                                content_key=subject_key,
                                result=result,
                                result_path=result_path,
                            )
                            self.ingestion.mark_content_status(
                                request.platform,
                                subject_key,
                                "completed",
                                str(result_path),
                                task_id=self.job_id,
                                audit_result_id=int((persisted or {}).get("audit_result_id") or (persisted or {}).get("id") or 0) or None,
                            )
                        except Exception:
                            self.ingestion.mark_content_status(
                                request.platform,
                                subject_key,
                                "failed",
                                task_id=self.job_id,
                            )
                            raise
                        results.append(persisted or result)
                        job_store.update(self.job_id, items=results)
                        job_store.log(self.job_id, f"边抓边分析完成：{subject.note_id}")

                def consume_stream_queue() -> None:
                    try:
                        while True:
                            batch = stream_queue.get()
                            if batch is None:
                                return
                            if isinstance(batch, tuple):
                                contents, comments = batch
                                analyze_stream_batch(contents, comments)
                            else:
                                analyze_stream_ref(batch)
                    except BaseException as exc:
                        analysis_errors.append(exc)

                stream_analyzer = None
                batch_flusher = None
                if stream_analysis:
                    stream_analyzer = threading.Thread(
                        target=consume_stream_queue,
                        name=f"audit-stream-analyzer-{self.job_id}",
                        daemon=True,
                    )
                    stream_analyzer.start()
                    batch_flusher = threading.Thread(
                        target=flush_batches_periodically,
                        name=f"audit-batch-flusher-{self.job_id}",
                        daemon=True,
                    )
                    batch_flusher.start()

                try:
                    with _crawler_lock:
                        if request.crawl_mode == "creator":
                            creator_ref = request.creator_url or request.creator_id
                            job_store.log(self.job_id, f"启动 MediaCrawler 博主主页爬取 {request.platform}: {creator_ref}")
                            output = self.crawler.run_creator(
                                platform=request.platform,
                                creator_id=creator_ref,
                                max_notes=request.max_notes,
                                max_comments=request.max_comments,
                                max_concurrency=request.max_concurrency,
                                get_sub_comment=request.get_sub_comment,
                                save_root=crawl_dir,
                                progress_callback=log_crawl_progress,
                                content_callback=enqueue_stream_batch if stream_analysis else None,
                                stream_items=stream_analysis,
                                stop_checker=crawl_stop_requested,
                            )
                        else:
                            if getattr(request, "keyword_source", "keyword") == "lexicon":
                                job_store.log(
                                    self.job_id,
                                    f"词库展开 {len(getattr(request, 'lexicon_keywords', []) or [])} 个关键词：{request.lexicon_category}",
                                )
                            job_store.log(self.job_id, f"启动 MediaCrawler 关键词爬取 {request.platform}: {request.keyword}")
                            output = self.crawler.run_search(
                                platform=request.platform,
                                keyword=request.keyword,
                                start_page=request.start_page,
                                max_notes=request.max_notes,
                                max_comments=request.max_comments,
                                max_concurrency=request.max_concurrency,
                                get_sub_comment=request.get_sub_comment,
                                save_root=crawl_dir,
                                progress_callback=log_crawl_progress,
                                content_callback=enqueue_stream_batch if stream_analysis else None,
                                stream_items=stream_analysis,
                                stop_checker=crawl_stop_requested,
                            )
                finally:
                    if batch_flusher:
                        stop_flusher.set()
                        batch_flusher.join()
                    if use_batch_ingestion:
                        for batch_path in batch_writer.flush_due(force=True):
                            ingest_completed_batch(batch_path)
                    if stream_analyzer:
                        stream_queue.put(None)
                        stream_analyzer.join()
                if analysis_errors:
                    raise analysis_errors[0]
                if output.command:
                    job_store.log(self.job_id, f"MediaCrawler command: {shlex.join(map(str, output.command))}")
                if crawl_stop_requested():
                    job_store.log(self.job_id, "采集已暂停，不再抓取新内容")
            else:
                source_root = self._resolve_source_root(request.source_output_id)
                crawl_dir = source_root / "crawler"
                job_store.log(self.job_id, f"跳过爬取，读取已有输出：{source_root.name}")
                output = self.crawler.load_latest_output(crawl_dir, request.platform)
                results = []
                analyzed_ids = set()
                analyze_limit = max(0, request.analyze_limit)
                skip_final_supplement = False

            job_store.log(
                self.job_id,
                f"crawl limits requested: max_notes={request.max_notes}, max_comments={request.max_comments}, "
                f"max_concurrency={request.max_concurrency}",
            )
            job_store.log(
                self.job_id,
                f"crawler output loaded: contents={len(output.contents)}, comments={len(output.comments)}",
            )
            if request.run_crawler and not output.contents and not output.comments:
                job_store.log(self.job_id, self._empty_crawl_hint(crawl_dir, request.platform))
            ingested_refs_by_key = {}
            if not skip_final_supplement and output.contents:
                ingested_refs_by_key = self._ingest_loaded_output(
                    platform=output.platform,
                    contents=output.contents,
                    comments=output.comments,
                    keyword=request.keyword if request.crawl_mode == "search" else (request.creator_url or request.creator_id),
                    category=request.lexicon_category if getattr(request, "keyword_source", "keyword") == "lexicon" else "",
                )
                for item in results:
                    analyzed_key = str(item.get("content_key") or item.get("note_id") or item.get("url") or "")
                    if analyzed_key:
                        self.ingestion.mark_content_status(
                            output.platform,
                            analyzed_key,
                            "completed",
                            task_id=self.job_id,
                        )
            subjects = self._build_subjects(
                output.platform,
                output.contents,
                output.comments,
                source_root,
                output.creators,
            )
            total_comments = sum(len(subject.comments) for subject in subjects)
            job_store.log(self.job_id, f"subjects built: subjects={len(subjects)}, comments={total_comments}")
            job_store.log(self.job_id, f"loaded subjects: {len(subjects)}")

            remaining_subjects = [] if skip_final_supplement else [
                subject for subject in subjects if subject.note_id not in analyzed_ids
            ]
            remaining_limit = max(0, max(0, request.analyze_limit) - len(results))
            total = min(len(remaining_subjects), remaining_limit)
            job_store.log(
                self.job_id,
                f"analysis limit applied: analyze_limit={request.analyze_limit}, already_analyzed={len(results)}, will_analyze_remaining={total}",
            )
            batch_size = max(1, getattr(request, "analysis_batch_size", 5))
            analysis_stopped = False
            for batch_start in range(0, total, batch_size):
                if not wait_if_analysis_paused():
                    break
                batch_end = min(batch_start + batch_size, total)
                job_store.log(self.job_id, f"开始补充分析第 {batch_start + 1}-{batch_end}/{total} 条")
                for idx, subject in enumerate(remaining_subjects[batch_start:batch_end], start=batch_start + 1):
                    if not wait_if_analysis_paused():
                        analysis_stopped = True
                        break
                    subject_key = self._subject_content_key(subject, ingested_refs_by_key)
                    subject_ref = ingested_refs_by_key.get(subject_key, {})
                    job_store.log(self.job_id, f"分析第 {idx}/{total} 条：{subject.note_id}")
                    try:
                        if subject_key:
                            self.ingestion.mark_content_status(
                                output.platform,
                                subject_key,
                                "analyzing",
                                task_id=self.job_id,
                            )
                        result = self._analyze_subject(subject)
                        result_path = self._write_result_json(subject.note_id, result)
                        persisted = self._persist_audit_result(
                            platform=output.platform,
                            content_key=subject_key or subject.note_id,
                            result=result,
                            result_path=result_path,
                            content_id=subject_ref.get("content_id"),
                        )
                        if subject_key:
                            self.ingestion.mark_content_status(
                                output.platform,
                                subject_key,
                                "completed",
                                str(result_path),
                                task_id=self.job_id,
                                audit_result_id=int((persisted or {}).get("audit_result_id") or (persisted or {}).get("id") or 0) or None,
                            )
                    except Exception:
                        if subject_key:
                            self.ingestion.mark_content_status(
                                output.platform,
                                subject_key,
                                "failed",
                                task_id=self.job_id,
                            )
                        raise
                    results.append(persisted or result)
                    analyzed_ids.add(subject.note_id)
                    job_store.update(self.job_id, items=results)
                    job_store.log(self.job_id, f"完成第 {idx}/{total} 条：{subject.note_id}")
                if analysis_stopped:
                    break

            final_control = job_store.control(self.job_id)
            if final_control.get("stop_all_requested"):
                job_store.update(self.job_id, status="stopped", items=results)
                job_store.log(self.job_id, "任务已停止")
            elif final_control.get("analysis_stop_requested"):
                reset_count = self.ingestion.reset_analyzing_for_task(self.job_id)
                job_store.update(self.job_id, status="analysis_stopped", items=results)
                job_store.update_control(self.job_id, analysis_stop_requested=False)
                suffix = f"，{reset_count} 条处理中内容已回到待分析" if reset_count else ""
                job_store.log(self.job_id, f"分析已停止，可点击继续分析{suffix}")
            elif final_control.get("analysis_paused"):
                job_store.update(self.job_id, status="analysis_paused", items=results)
                job_store.log(self.job_id, "任务采集结束，分析保持暂停")
            elif final_control.get("crawl_stop_requested"):
                job_store.update(self.job_id, status="crawl_paused", items=results)
                job_store.log(self.job_id, "任务采集已暂停，已处理当前可分析内容")
            else:
                job_store.update(self.job_id, status="completed", items=results)
                job_store.log(self.job_id, "任务完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", error=str(exc))
            job_store.log(self.job_id, f"任务失败：{exc}")

    def resume_pending_analysis(self, analyze_limit: int = 0, analysis_batch_size: int = 5) -> None:
        try:
            job = job_store.get(self.job_id)
            if not job:
                return
            platform = str(job.get("platform") or "xhs")
            source_root = self._resume_source_root(job)
            refs = self.ingestion.pending_for_task(self.job_id, limit=max(0, analyze_limit))
            existing_items = job.get("items") or []
            results = list(existing_items)
            analyzed_ids = {str(item.get("note_id") or "") for item in results if item.get("note_id")}
            job_store.update(self.job_id, status="analysis_running")
            self.audit_config_revision_id = str(job.get("current_audit_config_revision_id") or "")
            self._set_prompt_context(
                self._prompt_category_from_source(job),
                self._prompt_profile_from_source(job),
            )
            job_store.update_control(
                self.job_id,
                analysis_paused=False,
                analysis_stop_requested=False,
                stop_all_requested=False,
            )
            if source_root.resolve() != (settings.outputs_dir / self.job_id).resolve():
                job_store.log(self.job_id, f"继续分析复用已有输出媒体目录：{source_root.name}")
            job_store.log(self.job_id, f"继续分析开始：待处理 {len(refs)} 条")
            batch_size = max(1, analysis_batch_size)
            for batch_start in range(0, len(refs), batch_size):
                current_control = job_store.control(self.job_id)
                if current_control.get("stop_all_requested") or current_control.get("analysis_stop_requested"):
                    break
                batch = refs[batch_start:batch_start + batch_size]
                for ref in batch:
                    current_control = job_store.control(self.job_id)
                    if current_control.get("stop_all_requested") or current_control.get("analysis_stop_requested"):
                        break
                    content_key = str(ref.get("content_key") or "")
                    subjects = self._build_subjects(platform, [ref["item"]], ref.get("comments", []), source_root)
                    for subject in subjects:
                        subject_key = content_key or content_identity(ref["item"], platform) or subject.note_id
                        if subject.note_id in analyzed_ids:
                            continue
                        analyzed_ids.add(subject.note_id)
                        job_store.log(self.job_id, f"继续分析：{subject.note_id}")
                        try:
                            self.ingestion.mark_content_status(
                                platform,
                                subject_key,
                                "analyzing",
                                task_id=self.job_id,
                            )
                            result = self._analyze_subject(subject)
                            result_path = self._write_result_json(subject.note_id, result)
                            persisted = self._persist_audit_result(
                                platform=platform,
                                content_key=subject_key,
                                result=result,
                                result_path=result_path,
                                content_id=ref.get("content_id"),
                            )
                            self.ingestion.mark_content_status(
                                platform,
                                subject_key,
                                "completed",
                                str(result_path),
                                task_id=self.job_id,
                                audit_result_id=int((persisted or {}).get("audit_result_id") or (persisted or {}).get("id") or 0) or None,
                            )
                            results.append(persisted or result)
                            job_store.update(self.job_id, items=results)
                        except Exception:
                            self.ingestion.mark_content_status(
                                platform,
                                subject_key,
                                "failed",
                                task_id=self.job_id,
                            )
                            raise
            final_control = job_store.control(self.job_id)
            if final_control.get("stop_all_requested"):
                job_store.update(self.job_id, status="stopped", items=results)
                job_store.log(self.job_id, "继续分析已停止")
            elif final_control.get("analysis_stop_requested"):
                reset_count = self.ingestion.reset_analyzing_for_task(self.job_id)
                job_store.update(self.job_id, status="analysis_stopped", items=results)
                job_store.update_control(self.job_id, analysis_stop_requested=False)
                suffix = f"，{reset_count} 条处理中内容已回到待分析" if reset_count else ""
                job_store.log(self.job_id, f"继续分析已停止，可再次继续分析{suffix}")
            else:
                job_store.update(self.job_id, status="completed", items=results)
                job_store.log(self.job_id, "继续分析完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", error=str(exc))
            job_store.log(self.job_id, f"继续分析失败：{exc}")

    def run_local_video(self, video_path: Path, title: str = "", desc: str = "") -> None:
        try:
            job_store.update(self.job_id, status="running")
            job_store.log(self.job_id, "开始本地视频审核任务")
            job = job_store.get(self.job_id) or {}
            self.audit_config_revision_id = str(job.get("current_audit_config_revision_id") or "")
            self._set_prompt_context(self._prompt_category_from_source(job), self._prompt_profile_from_source(job))
            subject = AuditSubject(
                platform="local",
                note_id=video_path.stem,
                url="",
                title=title or video_path.name,
                desc=desc,
                author={},
                image_urls=[],
                video_urls=[],
                comments=[],
                local_image_paths=[],
                local_video_paths=[str(video_path)],
            )
            result = self._analyze_subject(subject)
            result_path = self._write_result_json(subject.note_id, result)
            persisted = self._persist_audit_result(
                platform="local",
                content_key=subject.note_id,
                result=result,
                result_path=result_path,
            )
            job_store.update(self.job_id, status="completed", items=[persisted or result])
            job_store.log(self.job_id, "本地视频审核任务完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", error=str(exc))
            job_store.log(self.job_id, f"本地视频审核任务失败：{exc}")

    def _persist_audit_result(
        self,
        *,
        platform: str,
        content_key: str,
        result: dict,
        result_path: Path,
        content_id: int | None = None,
    ) -> dict:
        result = dict(result)
        if self.audit_config_revision_id:
            result["audit_config_revision_id"] = self.audit_config_revision_id
        result["evidence_groups"] = build_evidence_groups(result)
        return self.audit_results.upsert_result(
            job_id=self.job_id,
            platform=platform,
            content_key=content_key or str(result.get("note_id") or ""),
            result=result,
            result_path=str(result_path),
            content_id=content_id,
            prompt_version=str(result.get("prompt_version") or ""),
            audit_config_revision_id=self.audit_config_revision_id,
        )

    @staticmethod
    def _prompt_category_from_source(source) -> str:
        lexicon_category = ""
        if isinstance(source, dict):
            lexicon_category = str(source.get("lexicon_category") or "")
        else:
            lexicon_category = str(getattr(source, "lexicon_category", "") or "")
        if lexicon_category:
            return lexicon_category
        return "soft"

    @staticmethod
    def _prompt_profile_from_source(source) -> dict:
        profile = source.get("prompt_profile_snapshot") if isinstance(source, dict) else getattr(source, "prompt_profile_snapshot", None)
        return profile if isinstance(profile, dict) else {}

    def _set_prompt_context(self, category: str | None, prompt_profile: dict | None = None) -> None:
        self.prompt_set = get_prompt_set(category, prompt_profile=prompt_profile)
        job_store.log(
            self.job_id,
            f"使用审核 Prompt：{self.prompt_set.category} · {self.prompt_set.prompt_version}",
        )

    def _ingest_loaded_output(
        self,
        *,
        platform: str,
        contents: list[dict],
        comments: list[dict],
        keyword: str,
        category: str = "",
    ) -> dict[str, dict]:
        batch_writer = BatchWriter(
            job_id=self.job_id,
            platform=platform,
            keyword=keyword,
            category=category,
            root=settings.outputs_dir / self.job_id,
            batch_size=max(1, len(contents)),
        )
        batch_paths = batch_writer.add(contents, comments)
        batch_paths.extend(batch_writer.flush_due(force=True))
        raw_items_dir = settings.outputs_dir / self.job_id / "raw_items"
        refs_by_key: dict[str, dict] = {}
        ingested_count = 0
        for batch_path in batch_paths:
            refs = self.ingestion.ingest_batch(batch_path, raw_items_dir)
            ingested_count += len(refs)
            for ref in refs:
                keys = {
                    str(ref.get("content_key") or ""),
                    self._content_id(ref.get("item") or {}, platform),
                    self._content_url(ref.get("item") or {}, platform),
                    content_identity(ref.get("item") or {}, platform),
                }
                for key in keys:
                    if key:
                        refs_by_key[key] = ref
        job_store.log(self.job_id, f"已有输出入库完成：{len(contents)} 条，待分析 {ingested_count} 条")
        return refs_by_key

    def _subject_content_key(self, subject: AuditSubject, refs_by_key: dict[str, dict]) -> str:
        ref = refs_by_key.get(subject.note_id) or refs_by_key.get(subject.url)
        if ref:
            return str(ref.get("content_key") or subject.note_id)
        return subject.note_id or subject.url

    def _write_result_json(self, note_id: str, result: dict) -> Path:
        result_dir = settings.outputs_dir / self.job_id / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        safe_note_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in note_id) or "unknown"
        result_path = result_dir / f"{safe_note_id}.result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result_path

    def _resolve_source_root(self, source_output_id: str | None) -> Path:
        if not source_output_id:
            raise ValueError("source_output_id is required when run_crawler is false")

        outputs_root = settings.outputs_dir.resolve()
        source_root = (outputs_root / source_output_id).resolve()
        source_root.relative_to(outputs_root)
        if not source_root.exists() or not source_root.is_dir():
            raise ValueError(f"output not found: {source_output_id}")
        return source_root

    def _empty_crawl_hint(self, crawl_dir: Path, platform: str) -> str:
        stderr_path = crawl_dir / "mediacrawler_stderr.log"
        base = "MediaCrawler 本次没有产出内容，分析阶段未触发。"
        if not stderr_path.exists():
            return base

        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-6000:]
        if platform == "dy" and "page: 1 is empty,[]" in stderr_tail:
            return (
                f"{base}抖音搜索第一页返回空列表，常见原因是登录态失效、请求过频或平台风控；"
                f"建议稍后重试，或重新打开可见浏览器完成登录/验证。日志：{stderr_path}"
            )
        if "账号也许被风控" in stderr_tail:
            return f"{base}MediaCrawler 日志提示账号可能被风控；建议稍后重试或重新登录。日志：{stderr_path}"
        if stderr_tail.strip():
            return f"{base}请检查 MediaCrawler 日志：{stderr_path}"
        return base

    def _resume_source_root(self, job: dict) -> Path:
        current_root = settings.outputs_dir / self.job_id
        if job.get("run_crawler"):
            return current_root

        source_output_id = str(job.get("source_output_id") or "").strip()
        if not source_output_id:
            return current_root

        try:
            return self._resolve_source_root(source_output_id)
        except Exception as exc:
            job_store.log(self.job_id, f"继续分析：已有输出媒体目录不可用，回退当前任务目录：{exc}")
            return current_root

    def _build_subjects(
        self,
        platform: str,
        contents: list[dict],
        comments: list[dict],
        media_root: Path,
        creators: list[dict] | None = None,
    ) -> list[AuditSubject]:
        comments_by_note: dict[str, list[dict]] = {}
        for comment in comments:
            note_id = self._content_id(comment, platform)
            comments_by_note.setdefault(note_id, []).append(comment)
        creators_by_key = self._creator_lookup(creators or [])

        subjects = []
        for item in contents:
            note_id = self._content_id(item, platform)
            local_video_paths = self._find_local_media(media_root, platform, note_id, "videos", VIDEO_EXTENSIONS)
            local_image_paths = self._find_local_media(media_root, platform, note_id, "images", IMAGE_EXTENSIONS)
            subjects.append(
                AuditSubject(
                    platform=platform,
                    note_id=note_id,
                    url=self._content_url(item, platform),
                    title=str(item.get("title", "") or ""),
                    desc=str(item.get("desc", "") or ""),
                    author=self._author_info(item, platform, creators_by_key),
                    image_urls=[] if local_video_paths else self._image_urls(item, platform),
                    video_urls=[] if local_video_paths else self._video_urls(item, platform),
                    comments=comments_by_note.get(note_id, []),
                    local_image_paths=local_image_paths,
                    local_video_paths=local_video_paths,
                )
            )
        return subjects

    def _content_id(self, item: dict, platform: str) -> str:
        id_fields = {
            "xhs": ("note_id",),
            "dy": ("aweme_id", "note_id"),
            "ks": ("video_id", "note_id"),
        }
        for field in id_fields.get(platform, ("note_id",)):
            value = item.get(field)
            if value:
                return str(value)
        return ""

    def _creator_lookup(self, creators: list[dict]) -> dict[str, dict]:
        lookup: dict[str, dict] = {}
        for creator in creators:
            for key in self._author_keys(creator):
                lookup.setdefault(key, creator)
        return lookup

    def _author_keys(self, item: dict) -> set[str]:
        keys = set()
        for field in ("user_id", "author_id", "sec_uid", "short_user_id", "user_unique_id"):
            value = item.get(field)
            if value:
                keys.add(str(value))
        return keys

    def _author_info(self, item: dict, platform: str, creators_by_key: dict[str, dict] | None = None) -> dict:
        creator = {}
        for key in self._author_keys(item):
            creator = (creators_by_key or {}).get(key) or {}
            if creator:
                break

        return {
            "platform": platform,
            "user_id": str(item.get("user_id") or item.get("author_id") or ""),
            "sec_uid": str(item.get("sec_uid") or ""),
            "short_user_id": str(item.get("short_user_id") or ""),
            "user_unique_id": str(item.get("user_unique_id") or ""),
            "nickname": str(item.get("nickname") or item.get("user_nickname") or creator.get("nickname") or ""),
            "avatar": str(item.get("avatar") or creator.get("avatar") or ""),
            "signature": str(item.get("user_signature") or creator.get("desc") or ""),
            "ip_location": str(item.get("ip_location") or creator.get("ip_location") or ""),
        }

    def _content_url(self, item: dict, platform: str) -> str:
        url_fields = {
            "xhs": ("note_url",),
            "dy": ("aweme_url",),
            "ks": ("video_url",),
        }
        for field in url_fields.get(platform, ("note_url",)):
            value = item.get(field)
            if value:
                return str(value)
        return ""

    def _image_urls(self, item: dict, platform: str) -> list[str]:
        if platform == "dy":
            return split_csv_urls(item.get("note_download_url") or item.get("cover_url"))
        if platform == "ks":
            return split_csv_urls(item.get("video_cover_url"))
        return split_csv_urls(item.get("image_list"))

    def _video_urls(self, item: dict, platform: str) -> list[str]:
        if platform == "dy":
            return split_csv_urls(item.get("video_download_url"))
        if platform == "ks":
            return split_csv_urls(item.get("video_play_url"))
        return split_csv_urls(item.get("video_url"))

    def _find_local_media(self, media_root: Path, platform: str, note_id: str, media_type: str, extensions: set[str]) -> list[str]:
        platform_dir = PLATFORM_DATA_DIRS.get(platform, platform)
        candidates = [
            media_root / "crawler" / platform_dir / media_type / note_id,
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

        evidence_index = self._build_evidence_index(subject, image_analyses, video_results)
        evidence_index_path = self._write_evidence_index(subject.note_id, evidence_index)
        prompt = self._render_fusion_prompt(
            title=subject.title,
            desc=subject.desc,
            comments=self._format_comments_for_prompt(subject.comments),
            image_analyses=json.dumps(image_analyses, ensure_ascii=False, indent=2),
            video_transcripts=self._format_relevant_transcripts_for_prompt(video_results),
            video_ocr_tracks="（视频 OCR 已绑定到 Evidence Index 的原始 timeline frame；如需引用画面文字，请使用对应 video_frame:<timestamp> 或 video:<v>/moment:<m>）",
            frame_analyses=self._format_evidence_index_for_prompt(evidence_index),
        )
        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始融合审核")
        audit = self.qwen.audit_text(prompt)
        elapsed = perf_counter() - started_at
        job_store.log(self.job_id, f"笔记 {subject.note_id}：融合审核完成，耗时 {elapsed:.1f}s")

        job_root = settings.outputs_dir / self.job_id
        risk_evidence = self._normalize_fusion_evidence(audit)
        decision = audit.get("decision", "review")
        risk_level = audit.get("risk_level", "unknown")
        risk_score = self._normalize_risk_score(audit.get("risk_score"), risk_level)
        category_scores = self._normalize_category_scores(
            audit.get("category_scores"),
            audit.get("categories", []),
            risk_score,
            risk_level,
        )
        score_breakdown = audit.get("score_breakdown") if isinstance(audit.get("score_breakdown"), list) else []
        primary_risk = str(audit.get("primary_risk") or "")
        if not primary_risk and category_scores:
            primary_risk = str(category_scores[0].get("category") or "")
        risk_frames = self._collect_risk_frames(
            video_results,
            job_root,
            decision,
            risk_level,
            risk_evidence=risk_evidence,
            audit_summary=audit.get("summary", ""),
        )
        risk_images = self._collect_risk_images(image_analyses, job_root)
        has_risk = bool(risk_evidence) or bool(risk_frames) or bool(risk_images) or decision in ("review", "reject")

        return {
            "note_id": subject.note_id,
            "url": subject.url,
            "title": subject.title,
            "desc": subject.desc,
            "author": subject.author,
            "prompt_category": self.prompt_set.category,
            "prompt_version": self.prompt_set.prompt_version,
            "content_title": audit.get("content_title", ""),
            "summary": audit.get("summary", ""),
            "decision": decision,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "primary_risk": primary_risk,
            "categories": audit.get("categories", []),
            "category_scores": category_scores,
            "score_breakdown": score_breakdown,
            "evidence": audit.get("evidence", []),
            "risk_evidence": risk_evidence,
            "risk_frames": risk_frames,
            "risk_images": risk_images,
            "has_risk": has_risk,
            "evidence_index": evidence_index,
            "evidence_index_path": str(evidence_index_path),
            "evidence_index_rel": self._to_job_rel(str(evidence_index_path), job_root),
            "image_analyses": image_analyses,
            "video_results": video_results,
            "comments": subject.comments,
            "comments_count": len(subject.comments),
            "raw_audit": audit,
        }

    def _to_job_rel(self, path_str: str | None, job_root: Path) -> str | None:
        if not path_str:
            return None
        try:
            return Path(path_str).resolve().relative_to(job_root.resolve()).as_posix()
        except (ValueError, OSError):
            return None

    def _normalize_fusion_evidence(self, audit: dict) -> list[dict]:
        out: list[dict] = []
        for ev in audit.get("evidence", []) or []:
            source = str(ev.get("source", ""))
            if source.startswith("comment"):
                kind = "comment"
            elif source.startswith("video_audio"):
                kind = "audio"
            elif source.startswith("video_ocr"):
                kind = "ocr"
            elif source.startswith("video_frame") or source.startswith("video:"):
                kind = "frame_ref"
            elif source.startswith("image"):
                kind = "image_ref"
            else:
                kind = "text"
            out.append({
                "kind": kind,
                "source": source,
                "severity": ev.get("severity", ""),
                "text": ev.get("text", ""),
                "reason": ev.get("reason", ""),
                "start": ev.get("start"),
                "end": ev.get("end"),
            })
        return out

    @staticmethod
    def _normalize_risk_score(score, risk_level: str = "unknown") -> int:
        try:
            return max(0, min(100, int(float(score))))
        except (TypeError, ValueError):
            fallback = {
                "high": 85,
                "medium": 65,
                "low": 40,
                "none": 0,
                "pass": 0,
            }
            return fallback.get(str(risk_level or "").lower(), 0)

    def _normalize_category_scores(
        self,
        category_scores,
        categories,
        risk_score: int,
        risk_level: str,
    ) -> list[dict]:
        if isinstance(category_scores, list):
            out = []
            for item in category_scores:
                if not isinstance(item, dict):
                    continue
                category = str(item.get("category") or "").strip()
                if not category:
                    continue
                out.append({
                    "category": category,
                    "score": self._normalize_risk_score(item.get("score"), item.get("level") or risk_level),
                    "level": str(item.get("level") or risk_level or "unknown"),
                })
            if out:
                return sorted(out, key=lambda item: item["score"], reverse=True)
        if not isinstance(categories, list):
            categories = [str(categories)] if categories else []
        clean_categories = [str(category).strip() for category in categories if str(category).strip()]
        if clean_categories:
            return [{
                "category": clean_categories[0],
                "score": risk_score,
                "level": str(risk_level or "unknown"),
            }]
        return []

    def _collect_risk_frames(
        self,
        video_results: list[dict],
        job_root: Path,
        decision: str = "review",
        risk_level: str = "unknown",
        risk_evidence: list[dict] | None = None,
        audit_summary: str = "",
    ) -> list[dict]:
        if str(decision).lower() == "pass" or str(risk_level).lower() == "none":
            return []
        frame_reasons = self._frame_evidence_reasons(risk_evidence or [])
        frames_out: list[dict] = []
        for video_idx, video in enumerate(video_results):
            for precise in video.get("precise_sheets", []) or []:
                analysis = precise.get("analysis") or {}
                precise_frames = precise.get("frames") or []
                for item in analysis.get("risk_items") or []:
                    if not isinstance(item, dict):
                        continue
                    precise_frame_id = str(item.get("precise_frame_id") or "")
                    matched_frame = next((frame for frame in precise_frames if str(frame.get("frame_id")) == precise_frame_id), {})
                    timestamp = item.get("timestamp", matched_frame.get("timestamp", precise.get("center_timestamp")))
                    reason = (
                        self._matching_frame_reason({"timestamp": timestamp}, frame_reasons)
                        or item.get("reason", "")
                        or item.get("evidence", "")
                    )
                    frames_out.append({
                        "video_index": video.get("index", video_idx),
                        "frame_index": matched_frame.get("index"),
                        "timestamp": timestamp,
                        "frame_number": matched_frame.get("frame_number"),
                        "severity": item.get("severity", ""),
                        "risk_type": item.get("risk_type", ""),
                        "evidence": item.get("evidence", ""),
                        "reason": reason,
                        "analysis_result": reason,
                        "visual_summary": analysis.get("visual_summary", ""),
                        "ocr_text": "",
                        "ocr_text_zh": "",
                        "asset_rel": precise.get("asset_rel"),
                        "frame_asset_rel": matched_frame.get("asset_rel"),
                        "precise_sheet_id": precise.get("precise_sheet_id"),
                        "moment_id": precise.get("moment_id"),
                        "precise_frame_id": precise_frame_id,
                    })
            for frame_idx, frame in enumerate(video.get("frames", []) or [], start=1):
                risk_items = frame.get("risk_items") or []
                if not risk_items:
                    continue
                top = max(risk_items, key=lambda r: _severity_rank(r.get("severity")))
                analysis_result = (
                    self._video_frame_summary(audit_summary)
                    or self._matching_frame_reason(frame, frame_reasons)
                    or top.get("reason", "")
                    or top.get("evidence", "")
                )
                frames_out.append({
                    "video_index": video.get("index", video_idx),
                    "frame_index": frame.get("frame_index", frame_idx),
                    "timestamp": frame.get("timestamp"),
                    "frame_number": frame.get("frame_number"),
                    "severity": top.get("severity", ""),
                    "risk_type": top.get("risk_type", ""),
                    "evidence": top.get("evidence", ""),
                    "reason": analysis_result,
                    "analysis_result": analysis_result,
                    "visual_summary": frame.get("visual_summary", ""),
                    "ocr_text": frame.get("ocr_text", "") or self._first_external_ocr_value(frame, "text"),
                    "ocr_text_zh": frame.get("ocr_text_zh", "") or self._first_external_ocr_value(frame, "text_zh"),
                    "ocr_engine": frame.get("ocr_engine", "") or self._first_external_ocr_value(frame, "engine"),
                    "ocr_error": frame.get("ocr_error", ""),
                    "asset_rel": self._to_job_rel(frame.get("path"), job_root),
                })
        frames_out.sort(key=lambda f: _severity_rank(f.get("severity")), reverse=True)
        return frames_out

    @staticmethod
    def _frame_evidence_reasons(risk_evidence: list[dict]) -> list[dict]:
        out: list[dict] = []
        for ev in risk_evidence:
            if ev.get("kind") != "frame_ref":
                continue
            reason = str(ev.get("reason") or ev.get("text") or "").strip()
            if not reason:
                continue
            source = str(ev.get("source") or "")
            timestamp = None
            if source.startswith("video_frame:"):
                try:
                    timestamp = float(source.split(":", 1)[1])
                except ValueError:
                    timestamp = None
            if timestamp is None and ev.get("start") not in (None, ""):
                try:
                    timestamp = float(ev.get("start"))
                except (TypeError, ValueError):
                    timestamp = None
            out.append({"timestamp": timestamp, "reason": reason})
        return out

    @staticmethod
    def _matching_frame_reason(frame: dict, frame_reasons: list[dict]) -> str:
        if not frame_reasons:
            return ""
        timestamp = frame.get("timestamp")
        try:
            timestamp_value = float(timestamp)
        except (TypeError, ValueError):
            timestamp_value = None
        if timestamp_value is None:
            return str(frame_reasons[0].get("reason") or "")
        nearest: tuple[float, str] | None = None
        for item in frame_reasons:
            reason = str(item.get("reason") or "")
            item_ts = item.get("timestamp")
            if item_ts is None:
                if nearest is None:
                    nearest = (0.75, reason)
                continue
            distance = abs(timestamp_value - float(item_ts))
            if nearest is None or distance < nearest[0]:
                nearest = (distance, reason)
        if nearest and nearest[0] <= 0.75:
            return nearest[1]
        return ""

    @staticmethod
    def _video_frame_summary(summary: str) -> str:
        text = str(summary or "").strip()
        if not text:
            return ""
        markers = ("视频关键帧", "关键帧", "OCR", "字幕", "画面文字")
        return text if any(marker in text for marker in markers) else ""

    @staticmethod
    def _first_external_ocr_value(frame: dict, key: str) -> str:
        for item in frame.get("external_ocr") or []:
            value = item.get(key)
            if value:
                return str(value)
        return ""

    def _collect_risk_images(self, image_analyses: list[dict], job_root: Path) -> list[dict]:
        images_out: list[dict] = []
        for img in image_analyses:
            risk_items = img.get("risk_items") or []
            if not risk_items:
                continue
            top = max(risk_items, key=lambda r: _severity_rank(r.get("severity")))
            images_out.append({
                "severity": top.get("severity", ""),
                "risk_type": top.get("risk_type", ""),
                "evidence": top.get("evidence", ""),
                "reason": top.get("reason", ""),
                "ocr_text": img.get("ocr_text", ""),
                "ocr_text_zh": img.get("ocr_text_zh", ""),
                "ocr_engine": img.get("ocr_engine", ""),
                "asset_rel": self._to_job_rel(img.get("local_path"), job_root),
            })
        images_out.sort(key=lambda i: _severity_rank(i.get("severity")), reverse=True)
        return images_out

    def _build_evidence_index(self, subject: AuditSubject, image_analyses: list[dict], video_results: list[dict]) -> dict:
        image_units = []
        for image in image_analyses:
            image_units.append({
                "evidence_id": image.get("evidence_id") or f"image:{image.get('index', len(image_units))}",
                "index": image.get("index"),
                "asset_rel": image.get("asset_rel") or self._to_job_rel(image.get("local_path"), settings.outputs_dir / self.job_id),
                "url": image.get("url", ""),
                "ocr_text": image.get("ocr_text", ""),
                "ocr_text_zh": image.get("ocr_text_zh", ""),
                "ocr_engine": image.get("ocr_engine", ""),
                "ocr_language": image.get("ocr_language", ""),
                "ocr_confidence": image.get("ocr_confidence", 0.0),
                "visual_summary": image.get("visual_summary", ""),
                "benign_context": image.get("benign_context", ""),
                "risk_items": self._compact_risk_items(image.get("risk_items") or []),
                "error": image.get("error", ""),
            })

        video_units = []
        timeline_frames = []
        moment_sheets = []
        moments = []
        precise_sheets = []
        asr_segments = []
        ocr_items = []
        for video in video_results:
            video_ref = f"video:{int(video.get('index', len(video_units)) or 0) + 1}"
            transcript = video.get("transcript") or {}
            for seg in transcript.get("segments") or []:
                text = self._truncate_text(seg.get("text", ""), 220)
                text_zh = self._truncate_text(seg.get("translation_zh", ""), 260)
                if not text and not text_zh:
                    continue
                asr_segments.append({
                    "source": f"{video_ref}/audio",
                    "audio_source": f"video_audio:{self._time_range(seg.get('start'), seg.get('end')).replace('s', '')}",
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "text": text,
                    "source_text_dolphin": self._truncate_text(seg.get("source_text_dolphin") or text, 220),
                    "source_text_mms": self._truncate_text(seg.get("source_text_mms", ""), 220),
                    "translation_zh": text_zh,
                    "language": transcript.get("language", ""),
                    "confidence": seg.get("translation_confidence", ""),
                    "engines": [
                        value
                        for value in [
                            transcript.get("asr_engine") or transcript.get("provider"),
                            "mms" if (transcript.get("mms") or {}).get("text") else "",
                            (transcript.get("translation") or {}).get("provider"),
                        ]
                        if value
                    ],
                })
            compact_frames = []
            for frame in video.get("timeline_frames") or video.get("frames") or []:
                frame_ref = f"{video_ref}/frame:{frame.get('frame_id')}"
                item = {
                    "source": frame_ref,
                    "video_frame_source": f"video_frame:{round(float(frame.get('timestamp') or 0.0), 2)}",
                    "frame_id": frame.get("frame_id"),
                    "timestamp": frame.get("timestamp"),
                    "frame_number": frame.get("frame_number"),
                    "asset_rel": frame.get("asset_rel") or self._to_job_rel(frame.get("path"), settings.outputs_dir / self.job_id),
                    "ocr_text": frame.get("ocr_text", ""),
                    "ocr_text_zh": frame.get("ocr_text_zh", ""),
                    "ocr_engine": frame.get("ocr_engine", ""),
                    "ocr_language": frame.get("ocr_language", ""),
                    "ocr_confidence": frame.get("ocr_confidence", 0.0),
                }
                compact_frames.append(item)
                timeline_frames.append(item)
                if item["ocr_text"] or item["ocr_text_zh"]:
                    ocr_items.append({
                        "source": frame_ref,
                        "timestamp": frame.get("timestamp"),
                        "text": item["ocr_text"],
                        "text_zh": item["ocr_text_zh"],
                        "language": item["ocr_language"],
                        "confidence": item["ocr_confidence"],
                        "engine": item["ocr_engine"],
                    })
            for sheet in video.get("moment_sheets") or []:
                moment_sheets.append({
                    "source": sheet.get("moment_id"),
                    "asset_rel": sheet.get("asset_rel"),
                    "start": sheet.get("start"),
                    "end": sheet.get("end"),
                    "frame_ids": sheet.get("frame_ids") or [],
                })
            for moment in video.get("moments") or []:
                analysis = moment.get("analysis") or {}
                moments.append({
                    "source": moment.get("moment_id"),
                    "asset_rel": moment.get("asset_rel"),
                    "start": moment.get("start"),
                    "end": moment.get("end"),
                    "frame_ids": moment.get("frame_ids") or [],
                    "status": analysis.get("status", "safe"),
                    "candidate_frame_ids": analysis.get("candidate_frame_ids") or [],
                    "risk_types": analysis.get("risk_types") or [],
                    "reason": analysis.get("reason", ""),
                    "safe_context": analysis.get("safe_context", ""),
                })
            for precise in video.get("precise_sheets") or []:
                analysis = precise.get("analysis") or {}
                precise_sheets.append({
                    "source": precise.get("precise_sheet_id"),
                    "moment_id": precise.get("moment_id"),
                    "candidate_frame_id": precise.get("candidate_frame_id"),
                    "asset_rel": precise.get("asset_rel"),
                    "center_timestamp": precise.get("center_timestamp"),
                    "visual_summary": analysis.get("visual_summary", ""),
                    "safe_context": analysis.get("safe_context", ""),
                    "risk_items": analysis.get("risk_items") or [],
                })
            video_units.append({
                "source": video_ref,
                "asset_rel": self._to_job_rel(video.get("local_path"), settings.outputs_dir / self.job_id),
                "timeline_frame_count": len(video.get("timeline_frames") or video.get("frames") or []),
                "moment_count": len(video.get("moments") or []),
                "precise_sheet_count": len(video.get("precise_sheets") or []),
                "transcript_summary": self._truncate_text(transcript.get("text_zh") or transcript.get("text") or "", 420),
                "asr_raw_rel": transcript.get("asr_raw_rel"),
                "moments": [moment for moment in moments if str(moment.get("source", "")).startswith(video_ref)],
            })

        return {
            "text_context": {
                "title": subject.title,
                "desc": subject.desc,
                "comments_count": len(subject.comments),
            },
            "image_units": image_units,
            "video_units": video_units,
            "timeline_frames": timeline_frames,
            "moment_sheets": moment_sheets,
            "moments": moments,
            "precise_sheets": precise_sheets,
            "asr_segments": asr_segments,
            "asr_raw": [
                {"source": video.get("source"), "asr_raw_rel": (video.get("transcript") or {}).get("asr_raw_rel")}
                for video in video_results
                if (video.get("transcript") or {}).get("asr_raw_rel")
            ],
            "ocr_items": ocr_items,
            "final_evidence_refs": [],
        }

    def _write_evidence_index(self, note_id: str, evidence_index: dict) -> Path:
        target = settings.outputs_dir / self.job_id / "assets" / note_id / "evidence_index.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence_index, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def _format_evidence_index_for_prompt(self, evidence_index: dict) -> str:
        compact = {
            "allowed_sources": [
                "image:<index>",
                "video:<v>/moment:<m>",
                "video_frame:<timestamp>",
                "video_audio:<start-end>",
                "comment:<id>",
            ],
            "image_units": evidence_index.get("image_units", []),
            "moments": [
                moment
                for moment in evidence_index.get("moments", [])
                if moment.get("status") == "suspicious" or moment.get("safe_context")
            ],
            "precise_sheets": evidence_index.get("precise_sheets", []),
            "relevant_asr_segments": self._relevant_asr_segments_for_index(evidence_index),
            "relevant_ocr_items": self._relevant_ocr_items_for_index(evidence_index),
        }
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        limit = settings.fusion_frame_evidence_max_chars
        if limit > 0 and len(text) > limit:
            compact["image_units"] = compact["image_units"][: settings.max_images_per_note]
            compact["moments"] = compact["moments"][:12]
            compact["precise_sheets"] = compact["precise_sheets"][: settings.max_precise_sheets_per_video]
            compact["relevant_asr_segments"] = compact["relevant_asr_segments"][:12]
            compact["relevant_ocr_items"] = compact["relevant_ocr_items"][:12]
            text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        return text

    def _relevant_asr_segments_for_index(self, evidence_index: dict) -> list[dict]:
        ranges = [
            (float(moment.get("start") or 0.0), float(moment.get("end") or 0.0), moment.get("source"))
            for moment in evidence_index.get("moments", [])
            if moment.get("status") == "suspicious"
        ]
        if not ranges:
            return []
        out = []
        for seg in evidence_index.get("asr_segments", []):
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") if seg.get("end") is not None else start)
            except (TypeError, ValueError):
                continue
            for range_start, range_end, source in ranges:
                if end >= range_start and start <= range_end:
                    out.append({**seg, "moment_source": source})
                    break
        return out

    def _relevant_ocr_items_for_index(self, evidence_index: dict) -> list[dict]:
        candidate_frame_ids = {
            str(frame_id)
            for moment in evidence_index.get("moments", [])
            if moment.get("status") == "suspicious"
            for frame_id in moment.get("candidate_frame_ids", [])
        }
        if not candidate_frame_ids:
            return []
        out = []
        for item in evidence_index.get("ocr_items", []):
            source = str(item.get("source") or "")
            if any(source.endswith(f"/frame:{frame_id}") for frame_id in candidate_frame_ids):
                out.append(item)
        return out

    def _format_relevant_transcripts_for_prompt(self, video_results: list[dict]) -> str:
        lines: list[str] = []
        for video in video_results:
            transcript = video.get("transcript") or {}
            summary = self._truncate_text(transcript.get("text_zh") or transcript.get("text") or "", 520)
            if summary:
                lines.append(f"[ASR全局摘要] {summary}")
            ranges = [
                (float(moment.get("start") or 0.0), float(moment.get("end") or 0.0))
                for moment in video.get("moments", []) or []
                if (moment.get("analysis") or {}).get("status") == "suspicious"
            ]
            if not ranges:
                continue
            for seg in transcript.get("segments") or []:
                try:
                    start = float(seg.get("start") or 0.0)
                    end = float(seg.get("end") if seg.get("end") is not None else start)
                except (TypeError, ValueError):
                    continue
                if not any(end >= range_start and start <= range_end for range_start, range_end in ranges):
                    continue
                text = self._truncate_text(seg.get("text", ""), 220)
                text_zh = self._truncate_text(seg.get("translation_zh", ""), 260)
                if text_zh and text:
                    lines.append(f"[ASR相关片段 {start:.1f}-{end:.1f}] {text_zh} / 原文:{text}")
                elif text_zh or text:
                    lines.append(f"[ASR相关片段 {start:.1f}-{end:.1f}] {text_zh or text}")
        return "\n".join(lines).strip() or "（无相关语音转写内容）"

    def _format_comments_for_prompt(self, comments: list[dict]) -> str:
        lines: list[str] = []
        limit = max(0, settings.fusion_max_comments)
        for comment in comments[:limit]:
            text = self._truncate_text(comment.get("content", ""), settings.fusion_comment_max_chars)
            if not text:
                continue
            comment_id = comment.get("comment_id") or comment.get("id") or ""
            lines.append(f"- comment:{comment_id}: {text}")
        if not lines:
            return "（无评论）"
        if len(comments) > limit:
            lines.append(f"…（评论较多，已仅保留前 {limit} 条）")
        return "\n".join(lines)

    def _format_frame_evidence_for_prompt(self, video_results: list[dict]) -> str:
        videos_out: list[dict] = []
        total_frames = 0
        for video_idx, video in enumerate(video_results, start=1):
            frames_out: list[dict] = []
            for frame_idx, frame in enumerate(video.get("frames") or [], start=1):
                total_frames += 1
                timestamp = float(frame.get("timestamp") or 0.0)
                item = {
                    "video": video_idx,
                    "frame": frame_idx,
                    "timestamp": round(timestamp, 2),
                    "visual_summary": self._truncate_text(
                        frame.get("visual_summary", ""),
                        settings.fusion_frame_summary_max_chars,
                    ),
                }
                risk_items = self._compact_risk_items(frame.get("risk_items") or [])
                if risk_items:
                    item["risk_items"] = risk_items
                ocr_text = self._truncate_text(
                    frame.get("ocr_text", "") or self._first_external_ocr_value(frame, "text"),
                    settings.fusion_ocr_text_max_chars,
                )
                ocr_text_zh = self._truncate_text(
                    frame.get("ocr_text_zh", "") or self._first_external_ocr_value(frame, "text_zh"),
                    settings.fusion_ocr_text_max_chars,
                )
                if ocr_text or ocr_text_zh:
                    item["ocr_text"] = ocr_text
                    item["ocr_text_zh"] = ocr_text_zh
                    item["ocr_engine"] = frame.get("ocr_engine", "") or self._first_external_ocr_value(frame, "engine")
                external_ocr = self._compact_external_ocr(frame.get("external_ocr") or [])
                if external_ocr:
                    item["ocr"] = external_ocr
                    item["ocr_alignment_status"] = frame.get("ocr_alignment_status") or ""
                benign_context = self._truncate_text(frame.get("benign_context", ""), 120)
                if benign_context:
                    item["benign_context"] = benign_context
                frames_out.append(item)
            if frames_out:
                videos_out.append({"video": video_idx, "frames": frames_out})

        if not videos_out:
            return "（无视频关键帧分析）"

        text = json.dumps(videos_out, ensure_ascii=False, separators=(",", ":"))
        limit = settings.fusion_frame_evidence_max_chars
        if limit > 0 and len(text) > limit:
            text = self._trim_frame_evidence_to_limit(videos_out, limit, total_frames)
        return text

    def _compact_risk_items(self, risk_items: list[dict]) -> list[dict]:
        compact: list[dict] = []
        ordered = sorted(risk_items, key=lambda item: _severity_rank(item.get("severity")), reverse=True)
        for item in ordered[: max(0, settings.fusion_frame_max_risk_items)]:
            compact.append({
                "severity": item.get("severity", ""),
                "risk_type": self._truncate_text(item.get("risk_type", ""), 80),
                "evidence": self._truncate_text(item.get("evidence", ""), 140),
                "reason": self._truncate_text(item.get("reason", ""), 160),
            })
        return compact

    def _compact_external_ocr(self, external_ocr: list[dict]) -> list[dict]:
        compact: list[dict] = []
        seen: set[str] = set()
        for item in external_ocr:
            text = self._truncate_text(item.get("text", ""), settings.fusion_ocr_text_max_chars)
            text_zh = self._truncate_text(item.get("text_zh", ""), settings.fusion_ocr_text_max_chars)
            key = "".join((text_zh or text).split()).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            compact.append({
                "text_zh": text_zh,
                "text": text,
                "time_range": self._time_range(item.get("start_ts"), item.get("end_ts") or item.get("sample_ts")),
                "alignment_confidence": item.get("alignment_confidence"),
            })
            if len(compact) >= max(0, settings.fusion_frame_max_ocr_items):
                break
        return compact

    def _trim_frame_evidence_to_limit(self, videos_out: list[dict], limit: int, total_frames: int) -> str:
        trimmed: list[dict] = []
        kept_frames = 0
        for video in videos_out:
            video_copy = {"video": video.get("video"), "frames": []}
            for frame in video.get("frames") or []:
                candidate = [*trimmed]
                candidate_video = {"video": video_copy["video"], "frames": [*video_copy["frames"], frame]}
                candidate.append(candidate_video)
                candidate_text = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                if len(candidate_text) > max(100, limit - 120):
                    break
                video_copy["frames"].append(frame)
                kept_frames += 1
            if video_copy["frames"]:
                trimmed.append(video_copy)
        omitted = max(0, total_frames - kept_frames)
        if omitted:
            trimmed.append({"omitted_frames": omitted, "reason": "融合输入长度限制，仅保留前部关键帧摘要"})
        return json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _truncate_text(value, max_chars: int) -> str:
        text = str(value or "").strip()
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[:max_chars] + "…"

    @staticmethod
    def _time_range(start, end) -> str:
        try:
            start_f = float(start or 0.0)
            end_f = float(end if end is not None else start_f)
            return f"{start_f:.1f}-{end_f:.1f}s"
        except (TypeError, ValueError):
            return ""

    def _format_transcripts_for_prompt(self, video_results: list[dict]) -> str:
        """只把 segment 级文本与时间喂给融合模型，丢弃 word 级时间戳避免上下文爆炸。"""
        lines: list[str] = []
        for video in video_results:
            transcript = video.get("transcript") or {}
            translation_text = (transcript.get("text_zh") or "").strip()
            if translation_text:
                provider = (transcript.get("translation") or {}).get("provider", "translation")
                lines.append(f"[ASR中文译文/{provider}] {translation_text}")
            segments = transcript.get("segments") or []
            if segments:
                for seg in segments:
                    text = (seg.get("text") or "").strip()
                    text_zh = (seg.get("translation_zh") or "").strip()
                    if not text and not text_zh:
                        continue
                    start = seg.get("start") or 0.0
                    end = seg.get("end") or 0.0
                    lang = transcript.get("language") or "asr"
                    if text_zh and text:
                        lines.append(f"[ASR片段/{lang} {float(start):.1f}-{float(end):.1f}] {text_zh} / 原文:{text}")
                    else:
                        lines.append(f"[ASR原文/{lang} {float(start):.1f}-{float(end):.1f}] {text_zh or text}")
            else:
                text = (transcript.get("text") or "").strip()
                if text:
                    lang = transcript.get("language") or "asr"
                    lines.append(f"[ASR原文/{lang}] {text}")

        combined = "\n".join(lines).strip()
        if not combined:
            return "（无语音转写内容）"

        limit = settings.transcript_max_chars
        if limit > 0 and len(combined) > limit:
            original_len = len(combined)
            combined = combined[:limit] + f"\n…（转写过长，已截断，原始长度 {original_len} 字）"
        return combined

    def _format_video_ocr_tracks_for_prompt(self, video_results: list[dict]) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        omitted = 0
        max_states = max(0, settings.fusion_max_ocr_states)
        for video in video_results:
            track = video.get("ocr_track") or {}
            for state in track.get("states", []) or []:
                text = self._truncate_text(state.get("text", ""), settings.fusion_ocr_text_max_chars)
                text_zh = self._truncate_text(state.get("text_zh", ""), settings.fusion_ocr_text_max_chars)
                if not text and not text_zh:
                    continue
                key = "".join((text_zh or text).split()).lower()
                if key in seen:
                    omitted += 1
                    continue
                seen.add(key)
                if len(lines) >= max_states:
                    omitted += 1
                    continue
                start = float(state.get("start_ts") or state.get("sample_ts") or 0.0)
                end = float(state.get("end_ts") or start)
                engine = state.get("engine") or "paddleocr_vl"
                if text_zh and text:
                    lines.append(f"[OCR/{engine} {start:.1f}-{end:.1f}] {text_zh} / 原文:{text}")
                elif text_zh:
                    lines.append(f"[OCR/{engine} {start:.1f}-{end:.1f}] {text_zh}")
                else:
                    lines.append(f"[OCR/{state.get('language_hint') or 'ug'} {start:.1f}-{end:.1f}] {text}")

        combined = "\n".join(lines).strip()
        if not combined:
            return "（无视频 OCR 文字）"
        if omitted:
            combined = f"{combined}\n…（OCR 文字已去重/截断，省略 {omitted} 条）"

        limit = settings.ocr_prompt_max_chars
        if limit > 0 and len(combined) > limit:
            original_len = len(combined)
            combined = combined[:limit] + f"\n…（视频 OCR 过长，已截断，原始长度 {original_len} 字）"
        return combined

    def _render_fusion_prompt(
        self,
        title: str,
        desc: str,
        comments: str,
        image_analyses: str,
        video_transcripts: str,
        video_ocr_tracks: str,
        frame_analyses: str,
    ) -> str:
        replacements = {
            "{title}": title,
            "{desc}": desc,
            "{comments}": comments,
            "{image_analyses}": image_analyses,
            "{video_transcripts}": video_transcripts,
            "{video_ocr_tracks}": video_ocr_tracks,
            "{frame_analyses}": frame_analyses,
        }
        prompt = self.prompt_set.fusion_prompt_template
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        prompt += (
            "\n\n新视频证据结构说明：视频画面证据来自 Evidence Index。"
            "最终 evidence.source 可引用 image:<index>、video:<v>/moment:<m>、"
            "video_frame:<timestamp>、video_audio:<start-end>、comment:<id>。"
            "如果只有 Moment 粗审怀疑、没有 Precise Sheet 明确证据，不要直接 reject/high，"
            "优先输出 review 或 low，并说明需要复核。"
        )
        return prompt

    def _analyze_images(self, subject: AuditSubject, image_dir: Path) -> list[dict]:
        results = []
        job_root = settings.outputs_dir / self.job_id
        local_images = [Path(path) for path in subject.local_image_paths if Path(path).exists()]
        local_limit = min(len(local_images), settings.max_images_per_note)
        local_candidates = []
        for idx, image_path in enumerate(local_images[: settings.max_images_per_note]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：分析本地图片 {idx + 1}/{local_limit}")
            analysis_path = self._stage_local_image(image_path, image_dir, idx)
            local_candidates.append({
                "index": idx,
                "analysis_path": analysis_path,
                "original_path": image_path,
            })
        local_ocr = self._scan_image_ocr_batch(subject.note_id, local_candidates)
        for item in local_candidates:
            idx = item["index"]
            analysis_path = item["analysis_path"]
            image_path = item["original_path"]
            ocr_fields = self._image_ocr_fields(local_ocr.get(idx) or {})
            try:
                analysis = self.qwen.analyze_image(analysis_path, self.prompt_set.image_prompt)
                results.append({
                    "index": idx,
                    "evidence_id": f"image:{idx}",
                    "url": "",
                    "local_path": str(analysis_path),
                    "asset_rel": self._to_job_rel(str(analysis_path), job_root),
                    "original_path": str(image_path),
                    "source": "local",
                    **analysis,
                    **ocr_fields,
                })
            except Exception as exc:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：本地图片 {idx + 1} 分析失败：{exc}")
                results.append({
                    "index": idx,
                    "evidence_id": f"image:{idx}",
                    "url": "",
                    "local_path": str(analysis_path),
                    "asset_rel": self._to_job_rel(str(analysis_path), job_root),
                    "original_path": str(image_path),
                    "source": "local",
                    "error": str(exc),
                    **ocr_fields,
                })

        if results:
            return results

        remote_limit = min(len(subject.image_urls), settings.max_images_per_note)
        remote_candidates = []
        for idx, url in enumerate(subject.image_urls[: settings.max_images_per_note]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：下载并分析远程图片 {idx + 1}/{remote_limit}")
            target = image_dir / safe_filename_from_url(url, f"image_{idx:02d}")
            local = download_url_with_error(url, target, platform=subject.platform, referer=subject.url).path
            remote_candidates.append({
                "index": idx,
                "url": url,
                "local": local,
                "image_source": str(local or url),
            })
        remote_ocr = self._scan_image_ocr_batch(
            subject.note_id,
            [
                {"index": item["index"], "analysis_path": item["local"]}
                for item in remote_candidates
                if item.get("local")
            ],
        )
        for item in remote_candidates:
            idx = item["index"]
            url = item["url"]
            local = item.get("local")
            image_source = item["image_source"]
            ocr_fields = self._image_ocr_fields(remote_ocr.get(idx) or {})
            try:
                analysis = self.qwen.analyze_image(image_source, self.prompt_set.image_prompt)
                results.append({
                    "index": idx,
                    "evidence_id": f"image:{idx}",
                    "url": url,
                    "local_path": str(local) if local else "",
                    "asset_rel": self._to_job_rel(str(local), job_root) if local else None,
                    "source": "download",
                    **analysis,
                    **ocr_fields,
                })
            except Exception as exc:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：远程图片 {idx + 1} 分析失败：{exc}")
                results.append({
                    "index": idx,
                    "evidence_id": f"image:{idx}",
                    "url": url,
                    "local_path": str(local) if local else "",
                    "asset_rel": self._to_job_rel(str(local), job_root) if local else None,
                    "source": "download",
                    "error": str(exc),
                    **ocr_fields,
                })
        return results

    def _scan_image_ocr_batch(self, note_id: str, candidates: list[dict]) -> dict[int, dict]:
        if not settings.ocr_enabled or not candidates:
            return {}
        workers = max(1, settings.ocr_concurrency)
        results: dict[int, dict] = {}

        def scan(candidate: dict) -> tuple[int, dict]:
            idx = int(candidate.get("index") or 0)
            path = candidate.get("analysis_path")
            if not path:
                return idx, {}
            return idx, self.ocr.scan_image(
                Path(path),
                state_id=f"{note_id}_image_{idx:04d}",
                log=lambda message: job_store.log(self.job_id, f"笔记 {note_id}：{message}"),
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(scan, candidate): candidate for candidate in candidates}
            for future in as_completed(future_map):
                candidate = future_map[future]
                idx = int(candidate.get("index") or 0)
                try:
                    _, result = future.result()
                except Exception as exc:
                    result = {
                        "text": "",
                        "text_zh": "",
                        "engine": settings.ocr_engine,
                        "language_hint": settings.ocr_language_hint,
                        "confidence": 0.0,
                        "error": str(exc),
                    }
                results[idx] = result
        return results

    @staticmethod
    def _image_ocr_fields(image_ocr: dict) -> dict:
        if not image_ocr:
            return {}
        return {
            "ocr_text": image_ocr.get("text", ""),
            "ocr_text_zh": image_ocr.get("text_zh", ""),
            "ocr_engine": image_ocr.get("engine", ""),
            "ocr_language": image_ocr.get("language", ""),
            "ocr_confidence": image_ocr.get("confidence", 0.0),
            "ocr_error": image_ocr.get("error", ""),
        }

    def _stage_local_image(self, image_path: Path, image_dir: Path, index: int) -> Path:
        job_root = (settings.outputs_dir / self.job_id).resolve()
        resolved = image_path.resolve()
        try:
            resolved.relative_to(job_root)
            return resolved
        except ValueError:
            pass

        image_dir.mkdir(parents=True, exist_ok=True)
        suffix = image_path.suffix or ".jpg"
        target = image_dir / f"local_{index:02d}{suffix}"
        if resolved != target.resolve():
            shutil.copy2(resolved, target)
        return target

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
                title=subject.title,
                desc=subject.desc,
            ))

        if results:
            return results

        if subject.video_urls:
            job_store.log(
                self.job_id,
                f"笔记 {subject.note_id}：本地未找到视频文件，将尝试远程下载。"
                f"期望目录：crawler/{PLATFORM_DATA_DIRS.get(subject.platform, subject.platform)}/videos/{subject.note_id} "
                f"或 assets/{subject.note_id}/videos",
            )

        for idx, url in enumerate(subject.video_urls[:1]):
            job_store.log(self.job_id, f"笔记 {subject.note_id}：开始下载远程视频 {idx + 1}/1")
            target = video_dir / f"video_{idx:02d}.mp4"
            download = download_url_with_error(url, target, platform=subject.platform, referer=subject.url)
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
                title=subject.title,
                desc=subject.desc,
            ))
        return results

    def _analyze_video_file(
        self,
        index: int,
        video_path: Path,
        video_dir: Path,
        url: str,
        source: str,
        title: str = "",
        desc: str = "",
    ) -> dict:
        started_at = perf_counter()
        video_label = f"视频 {index + 1}"
        job_store.log(self.job_id, f"{video_label}：开始处理 {video_path}")
        transcript = {"text": "", "segments": []}
        timeline_frames: list[dict] = []
        moment_sheets: list[dict] = []
        moments: list[dict] = []
        precise_sheets: list[dict] = []
        ocr_track = {
            "states": [],
            "metrics": {
                "enabled": settings.ocr_enabled,
                "mode": "timeline_frame",
                "ocr_calls": 0,
                "translation_calls": 0,
                "errors": 0,
            },
        }
        errors = []

        try:
            job_store.log(self.job_id, f"{video_label}：开始抽取音频")
            audio_path = self.audio.extract_audio(video_path, video_dir / f"audio_{index:02d}")
            if audio_path:
                job_store.log(self.job_id, f"{video_label}：音频抽取完成，开始 ASR 转写")
                transcript = self.audio.transcribe(audio_path)
                transcript = self._translate_transcript_if_needed(
                    transcript,
                    video_label,
                    audio_path=audio_path,
                )
                transcript = self._persist_asr_raw(
                    transcript,
                    video_dir / f"audio_{index:02d}" / "asr_raw.json",
                    settings.outputs_dir / self.job_id,
                )
                job_store.log(
                    self.job_id,
                    f"{video_label}：ASR 转写完成，engine={transcript.get('asr_engine', transcript.get('provider', 'unknown'))}，"
                    f"device={transcript.get('device', 'unknown')}，"
                    f"文本长度={len(transcript.get('text', ''))}",
                )
            else:
                detail = getattr(self.audio, "last_extract_error", "") or "audio extraction failed"
                errors.append(f"audio extraction failed: {detail}")
                job_store.log(self.job_id, f"{video_label}：音频抽取失败：{detail}，跳过转写")
        except Exception as exc:
            errors.append(f"audio/transcribe failed: {exc}")
            job_store.log(self.job_id, f"{video_label}：音频/转写失败：{exc}")

        try:
            job_store.log(
                self.job_id,
                f"{video_label}：开始抽取 timeline frames，最多 {settings.max_video_frames} 帧，"
                f"scene={settings.video_scene_threshold}，fps-floor={settings.video_fps_floor_seconds}s",
            )
            frame_infos = self.frames.extract_timeline_frames(
                video_path=video_path,
                output_dir=video_dir / f"frames_{index:02d}",
                max_frames=settings.max_video_frames,
            )
            job_store.log(self.job_id, f"{video_label}：timeline frames 抽取完成，共 {len(frame_infos)} 帧")
            ocr_started = perf_counter()
            frame_ocr_results = self._scan_timeline_ocr_batch(index, video_label, frame_infos)
            for frame_idx, frame in enumerate(frame_infos, start=1):
                frame_ts = float(frame.get("timestamp") or 0.0)
                frame_ocr = frame_ocr_results.get(frame_idx) or {}
                self._update_ocr_metrics(ocr_track, frame_ocr)
                external_ocr = self._frame_ocr_as_external_ocr(frame_ocr, frame_ts)
                enriched = {
                    **frame,
                    "asset_rel": self._to_job_rel(frame.get("path"), settings.outputs_dir / self.job_id),
                    "external_ocr": external_ocr,
                    "ocr_alignment_status": "timeline_frame" if external_ocr else "none",
                    "ocr_text": frame_ocr.get("text", ""),
                    "ocr_text_zh": frame_ocr.get("text_zh", ""),
                    "ocr_engine": frame_ocr.get("engine", ""),
                    "ocr_language": frame_ocr.get("language", ""),
                    "ocr_error": frame_ocr.get("error", ""),
                    "ocr_confidence": frame_ocr.get("confidence", 0.0),
                    "video_index": index,
                    "frame_index": frame_idx,
                }
                timeline_frames.append(enriched)
                if external_ocr:
                    ocr_track["states"].extend(external_ocr)
            metrics = ocr_track.get("metrics") or {}
            job_store.log(
                self.job_id,
                f"{video_label}：timeline OCR 完成，ocr_calls={metrics.get('ocr_calls', 0)}，"
                f"translation_calls={metrics.get('translation_calls', 0)}，errors={metrics.get('errors', 0)}，"
                f"耗时={perf_counter() - ocr_started:.1f}s",
            )

            sheet_size = max(1, settings.video_moment_sheet_frames)
            chunks = [timeline_frames[i : i + sheet_size] for i in range(0, len(timeline_frames), sheet_size)]
            moment_jobs: list[dict] = []
            for moment_idx, chunk in enumerate(chunks, start=1):
                moment_id = f"video:{index + 1}/moment:{moment_idx}"
                sheet_path = video_dir / f"moment_sheets_{index:02d}" / f"moment_{moment_idx:02d}.jpg"
                self.frames.create_contact_sheet(chunk, sheet_path)
                start_ts = min(float(frame.get("timestamp") or 0.0) for frame in chunk)
                end_ts = max(float(frame.get("timestamp") or 0.0) for frame in chunk)
                sheet = {
                    "moment_id": moment_id,
                    "index": moment_idx,
                    "path": str(sheet_path),
                    "asset_rel": self._to_job_rel(str(sheet_path), settings.outputs_dir / self.job_id),
                    "frame_ids": [frame.get("frame_id") for frame in chunk],
                    "start": start_ts,
                    "end": end_ts,
                }
                moment_sheets.append(sheet)
                prompt = self._render_moment_prompt(
                    video_index=index,
                    moment_id=moment_id,
                    frames=chunk,
                    transcript=transcript,
                    title=title,
                    desc=desc,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
                moment_jobs.append({
                    "moment_idx": moment_idx,
                    "moment_id": moment_id,
                    "chunk": chunk,
                    "sheet": sheet,
                    "sheet_path": sheet_path,
                    "prompt": prompt,
                })

            vlm_mode = "remote_vlm" if settings.use_remote_vlm else ("dashscope_api" if self.qwen.enabled else "mock")
            moment_workers = max(1, min(settings.video_moment_concurrency, len(moment_jobs) or 1))
            if moment_jobs:
                job_store.log(
                    self.job_id,
                    f"{video_label}：开始 Moment 粗审，并发={moment_workers}，共 {len(moment_jobs)} 个",
                )

            def analyze_moment(job: dict) -> dict:
                moment_idx = int(job["moment_idx"])
                chunk = job["chunk"]
                sheet_path = job["sheet_path"]
                prompt = job["prompt"]
                vlm_mode = "remote_vlm" if settings.use_remote_vlm else ("dashscope_api" if self.qwen.enabled else "mock")
                job_store.log(self.job_id, f"{video_label}：Moment {moment_idx}/{len(chunks)} 粗审开始，mode={vlm_mode}")
                vlm_started = perf_counter()
                raw_analysis = self.qwen.analyze_image(sheet_path, prompt)
                analysis = self._normalize_moment_analysis(raw_analysis, {str(frame.get("frame_id")) for frame in chunk})
                job_store.log(
                    self.job_id,
                    f"{video_label}：Moment {moment_idx}/{len(chunks)} 粗审完成，"
                    f"status={analysis.get('status')}，candidate_frames={len(analysis.get('candidate_frame_ids') or [])}，"
                    f"耗时={perf_counter() - vlm_started:.1f}s",
                )
                return {**job, "analysis": analysis}

            if moment_workers <= 1 or len(moment_jobs) <= 1:
                analyzed_moments = [analyze_moment(job) for job in moment_jobs]
            else:
                analyzed_moments = []
                with ThreadPoolExecutor(max_workers=moment_workers) as executor:
                    future_map = {executor.submit(analyze_moment, job): job for job in moment_jobs}
                    for future in as_completed(future_map):
                        analyzed_moments.append(future.result())

            for moment_job in sorted(analyzed_moments, key=lambda job: int(job["moment_idx"])):
                moment_idx = int(moment_job["moment_idx"])
                moment_id = moment_job["moment_id"]
                chunk = moment_job["chunk"]
                sheet = moment_job["sheet"]
                analysis = moment_job["analysis"]
                moments.append({
                    **sheet,
                    "analysis": analysis,
                    "frames": [
                        {
                            "frame_id": frame.get("frame_id"),
                            "timestamp": frame.get("timestamp"),
                            "frame_number": frame.get("frame_number"),
                            "asset_rel": frame.get("asset_rel"),
                            "ocr_text": frame.get("ocr_text", ""),
                            "ocr_text_zh": frame.get("ocr_text_zh", ""),
                        }
                        for frame in chunk
                    ],
                })

                if analysis.get("status") != "suspicious":
                    continue
                if len(precise_sheets) >= settings.max_precise_sheets_per_video:
                    job_store.log(self.job_id, f"{video_label}：已达到单视频 Precise Sheet 上限，跳过后续精审")
                    continue
                valid_candidates = [
                    frame_id
                    for frame_id in analysis.get("candidate_frame_ids") or []
                    if any(str(frame.get("frame_id")) == str(frame_id) for frame in chunk)
                ]
                for candidate_frame_id in valid_candidates[: max(0, settings.max_precise_sheets_per_moment)]:
                    if len(precise_sheets) >= settings.max_precise_sheets_per_video:
                        break
                    center_frame = next((frame for frame in chunk if str(frame.get("frame_id")) == str(candidate_frame_id)), None)
                    if not center_frame:
                        continue
                    precise_idx = len(precise_sheets) + 1
                    sheet_id = f"v{index + 1}_m{moment_idx}_p{precise_idx}"
                    precise_frames = self.frames.extract_precise_window(
                        video_path=video_path,
                        output_dir=video_dir / f"precise_frames_{index:02d}" / sheet_id,
                        center_timestamp=float(center_frame.get("timestamp") or 0.0),
                        sheet_id=sheet_id,
                        window_seconds=settings.precise_window_seconds,
                        frame_count=settings.precise_sheet_frames,
                    )
                    if not precise_frames:
                        continue
                    precise_sheet_path = video_dir / f"precise_sheets_{index:02d}" / f"{sheet_id}.jpg"
                    self.frames.create_contact_sheet(precise_frames, precise_sheet_path)
                    precise_prompt = self._render_precise_prompt(
                        video_index=index,
                        moment_id=moment_id,
                        candidate_frame_id=str(candidate_frame_id),
                        precise_frames=precise_frames,
                        moment_frames=chunk,
                        coarse_analysis=analysis,
                        transcript=transcript,
                    )
                    job_store.log(self.job_id, f"{video_label}：Precise Sheet {precise_idx} 精审开始，candidate={candidate_frame_id}")
                    precise_started = perf_counter()
                    raw_precise = self.qwen.analyze_image(precise_sheet_path, precise_prompt)
                    precise_analysis = self._normalize_precise_analysis(raw_precise, precise_frames, center_frame)
                    job_store.log(
                        self.job_id,
                        f"{video_label}：Precise Sheet {precise_idx} 精审完成，"
                        f"risk_items={len(precise_analysis.get('risk_items') or [])}，"
                        f"耗时={perf_counter() - precise_started:.1f}s",
                    )
                    precise_sheets.append({
                        "precise_sheet_id": sheet_id,
                        "moment_id": moment_id,
                        "candidate_frame_id": str(candidate_frame_id),
                        "center_timestamp": float(center_frame.get("timestamp") or 0.0),
                        "path": str(precise_sheet_path),
                        "asset_rel": self._to_job_rel(str(precise_sheet_path), settings.outputs_dir / self.job_id),
                        "frames": [
                            {
                                **frame,
                                "asset_rel": self._to_job_rel(frame.get("path"), settings.outputs_dir / self.job_id),
                            }
                            for frame in precise_frames
                        ],
                        "analysis": precise_analysis,
                    })

        except Exception as exc:
            errors.append(f"frame analysis failed: {exc}")
            job_store.log(self.job_id, f"{video_label}：视频画面分析失败：{exc}")

        elapsed = perf_counter() - started_at
        job_store.log(self.job_id, f"{video_label}：处理完成，耗时 {elapsed:.1f}s")

        result = {
            "index": index,
            "url": url,
            "local_path": str(video_path),
            "source": source,
            "transcript": transcript,
            "ocr_track": ocr_track,
            "ocr_metrics": ocr_track.get("metrics") or {},
            "timeline_frames": timeline_frames,
            "moment_sheets": moment_sheets,
            "moments": moments,
            "precise_sheets": precise_sheets,
            "frames": timeline_frames,
        }

        curve_png = video_dir / f"frames_{index:02d}" / "keyframe_curve.png"
        curve_json = video_dir / f"frames_{index:02d}" / "keyframe_curve.json"
        if curve_png.exists():
            job_root = settings.outputs_dir / self.job_id
            result["keyframe_curve_path"] = str(curve_png)
            result["selected_frame_count"] = len(timeline_frames)
            try:
                result["keyframe_curve_rel"] = curve_png.relative_to(job_root).as_posix()
                if curve_json.exists():
                    result["keyframe_curve_json_rel"] = curve_json.relative_to(job_root).as_posix()
            except ValueError:
                pass

        if errors:
            result["errors"] = errors
        return result

    def _scan_timeline_ocr_batch(self, video_index: int, video_label: str, frame_infos: list[dict]) -> dict[int, dict]:
        if not frame_infos:
            return {}
        workers = max(1, settings.ocr_concurrency) if settings.ocr_enabled else 1

        def scan(frame_idx: int, frame: dict) -> tuple[int, dict]:
            job_store.log(self.job_id, f"{video_label}：OCR timeline frame {frame_idx}/{len(frame_infos)}")
            frame_ts = float(frame.get("timestamp") or 0.0)
            result = self.ocr.scan_image(
                Path(frame["path"]),
                timestamp=frame_ts,
                frame_num=frame.get("frame_number"),
                state_id=f"video_{video_index + 1}_frame_{frame_idx:04d}",
                log=lambda message: job_store.log(self.job_id, f"{video_label}：{message}"),
            )
            return frame_idx, result

        if workers <= 1 or len(frame_infos) <= 1:
            return dict(scan(idx, frame) for idx, frame in enumerate(frame_infos, start=1))

        results: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(scan, idx, frame): idx
                for idx, frame in enumerate(frame_infos, start=1)
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    frame_idx, result = future.result()
                except Exception as exc:
                    results[idx] = {
                        "text": "",
                        "text_zh": "",
                        "translation": {},
                        "confidence": 0.0,
                        "engine": settings.ocr_engine,
                        "language_hint": settings.ocr_language_hint,
                        "error": str(exc),
                        "enabled": settings.ocr_enabled,
                    }
                else:
                    results[frame_idx] = result
        return results

    @staticmethod
    def _update_ocr_metrics(ocr_track: dict, frame_ocr: dict) -> None:
        metrics = ocr_track.get("metrics") or {}
        ocr_track["metrics"] = metrics
        if frame_ocr.get("enabled", settings.ocr_enabled):
            metrics["ocr_calls"] = int(metrics.get("ocr_calls") or 0) + 1
        if frame_ocr.get("translation"):
            metrics["translation_calls"] = int(metrics.get("translation_calls") or 0) + 1
        if frame_ocr.get("error"):
            metrics["errors"] = int(metrics.get("errors") or 0) + 1

    def _render_moment_prompt(
        self,
        *,
        video_index: int,
        moment_id: str,
        frames: list[dict],
        transcript: dict,
        title: str,
        desc: str,
        start_ts: float,
        end_ts: float,
    ) -> str:
        payload = {
            "moment_id": moment_id,
            "video": video_index + 1,
            "time_range": self._time_range(start_ts, end_ts),
            "title": self._truncate_text(title, 160),
            "desc": self._truncate_text(desc, 260),
            "frames": [
                {
                    "frame_id": frame.get("frame_id"),
                    "timestamp": round(float(frame.get("timestamp") or 0.0), 2),
                    "ocr_text": self._truncate_text(frame.get("ocr_text", ""), 160),
                    "ocr_text_zh": self._truncate_text(frame.get("ocr_text_zh", ""), 160),
                }
                for frame in frames
            ],
            "asr_segments": self._segments_for_range(transcript, start_ts, end_ts, max_items=8),
        }
        return (
            "你是视频审核系统中的 Moment 粗审器。输入图片是一张 3x3 contact sheet，"
            "每个格子底部标注 frame_id 和 timestamp；最后不足 9 帧的位置为空。\n"
            "请沿用下面原关键帧审核 prompt 的判断边界，但本次只做粗审，不做最终处罚。\n\n"
            "粗审只输出合法 JSON，字段必须完全一致：\n"
            "{\n"
            '  "status": "safe|suspicious",\n'
            '  "candidate_frame_ids": ["只能从本次 frames 中选择，如 f0001"],\n'
            '  "risk_types": ["风险类别"],\n'
            '  "reason": "如果 suspicious，用一句话说明怀疑点；safe 时写空字符串",\n'
            '  "safe_context": "如果 safe，说明为什么未发现明显违规线索；suspicious 时写空字符串"\n'
            "}\n\n"
            "约束：\n"
            "1. 只能引用本 contact sheet 中存在的 frame_id，不要输出自由时间段。\n"
            "2. 没有明确画面证据时输出 safe，candidate_frame_ids 为空数组。\n"
            "3. OCR/ASR 只是上下文线索，不要仅凭单个敏感词升级风险。\n\n"
            f"原关键帧审核 prompt：\n{self.prompt_set.frame_prompt}\n\n"
            "本 Moment 上下文 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def _render_precise_prompt(
        self,
        *,
        video_index: int,
        moment_id: str,
        candidate_frame_id: str,
        precise_frames: list[dict],
        moment_frames: list[dict],
        coarse_analysis: dict,
        transcript: dict,
    ) -> str:
        start_ts = min(float(frame.get("timestamp") or 0.0) for frame in moment_frames)
        end_ts = max(float(frame.get("timestamp") or 0.0) for frame in moment_frames)
        payload = {
            "video": video_index + 1,
            "moment_id": moment_id,
            "candidate_frame_id": candidate_frame_id,
            "coarse_reason": coarse_analysis.get("reason", ""),
            "coarse_risk_types": coarse_analysis.get("risk_types", []),
            "precise_frames": [
                {
                    "precise_frame_id": frame.get("frame_id"),
                    "timestamp": round(float(frame.get("timestamp") or 0.0), 2),
                }
                for frame in precise_frames
            ],
            "moment_ocr": self._ocr_items_for_frames(moment_frames),
            "asr_segments": self._segments_for_range(transcript, start_ts, end_ts, max_items=8),
        }
        return (
            "你是视频审核系统中的局部精审器。输入图片是一张围绕候选帧前后约 1 秒抽取的 3x3 Precise Sheet。"
            "请只复核粗审怀疑点是否有明确画面证据。\n\n"
            "请只输出合法 JSON，字段必须完全一致：\n"
            "{\n"
            '  "visual_summary": "客观概括局部连续画面",\n'
            '  "safe_context": "如果证据不足或属于正常语境，说明原因；有风险时写空字符串",\n'
            '  "risk_items": [\n'
            "    {\n"
            '      "risk_type": "风险类别",\n'
            '      "evidence": "必须引用可见画面元素或局部 OCR/ASR",\n'
            '      "reason": "为什么超出正常展示或需要复核",\n'
            '      "severity": "low|medium|high",\n'
            '      "precise_frame_id": "必须来自 precise_frames",\n'
            '      "timestamp": 0.0\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "约束：\n"
            "1. 若没有比粗审更明确的证据，risk_items 输出空数组。\n"
            "2. 每条 risk_items 必须引用 precise_frame_id 和 timestamp。\n"
            "3. 只用相关局部 OCR/ASR 辅助判断，不要引入全量视频臆测。\n\n"
            f"原关键帧审核 prompt：\n{self.prompt_set.frame_prompt}\n\n"
            "局部精审上下文 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def _normalize_moment_analysis(self, analysis: dict, valid_frame_ids: set[str]) -> dict:
        status = str(analysis.get("status") or "").strip().lower()
        raw_candidates = analysis.get("candidate_frame_ids") or analysis.get("candidate_frames") or []
        if isinstance(raw_candidates, str):
            raw_candidates = [raw_candidates]
        candidate_frame_ids = []
        for value in raw_candidates:
            frame_id = str(value).strip()
            if frame_id in valid_frame_ids and frame_id not in candidate_frame_ids:
                candidate_frame_ids.append(frame_id)
        risk_types = analysis.get("risk_types") or []
        if isinstance(risk_types, str):
            risk_types = [risk_types]
        risk_types = [str(item).strip() for item in risk_types if str(item).strip()]
        if status not in {"safe", "suspicious"}:
            status = "suspicious" if candidate_frame_ids or risk_types else "safe"
        if status == "safe":
            candidate_frame_ids = []
        return {
            "status": status,
            "candidate_frame_ids": candidate_frame_ids,
            "risk_types": risk_types,
            "reason": self._truncate_text(analysis.get("reason", ""), 240),
            "safe_context": self._truncate_text(analysis.get("safe_context", ""), 240),
            "raw": analysis,
        }

    def _normalize_precise_analysis(self, analysis: dict, precise_frames: list[dict], center_frame: dict) -> dict:
        valid_by_id = {str(frame.get("frame_id")): frame for frame in precise_frames}
        center_ts = float(center_frame.get("timestamp") or 0.0)
        risk_items = []
        for item in analysis.get("risk_items") or []:
            if not isinstance(item, dict):
                continue
            frame_id = str(item.get("precise_frame_id") or item.get("frame_id") or "").strip()
            if frame_id not in valid_by_id:
                nearest = min(
                    precise_frames,
                    key=lambda frame: abs(float(frame.get("timestamp") or 0.0) - center_ts),
                    default=None,
                )
                frame_id = str((nearest or {}).get("frame_id") or "")
            frame = valid_by_id.get(frame_id) or {}
            timestamp = item.get("timestamp")
            try:
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                timestamp = float(frame.get("timestamp") or center_ts)
            risk_items.append({
                "risk_type": self._truncate_text(item.get("risk_type", ""), 80),
                "evidence": self._truncate_text(item.get("evidence", ""), 180),
                "reason": self._truncate_text(item.get("reason", ""), 180),
                "severity": str(item.get("severity") or "low").lower(),
                "precise_frame_id": frame_id,
                "timestamp": timestamp,
            })
        return {
            "visual_summary": self._truncate_text(analysis.get("visual_summary", ""), 260),
            "safe_context": self._truncate_text(analysis.get("safe_context", ""), 260),
            "risk_items": risk_items,
            "raw": analysis,
        }

    def _segments_for_range(self, transcript: dict, start_ts: float, end_ts: float, max_items: int = 8) -> list[dict]:
        segments = transcript.get("segments") or []
        out: list[dict] = []
        for seg in segments:
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") if seg.get("end") is not None else start)
            except (TypeError, ValueError):
                continue
            if end < start_ts or start > end_ts:
                continue
            text = self._truncate_text(seg.get("text", ""), 220)
            text_zh = self._truncate_text(seg.get("translation_zh", ""), 260)
            if text or text_zh:
                out.append({
                    "start": start,
                    "end": end,
                    "text": text,
                    "translation_zh": text_zh,
                    "language": transcript.get("language", ""),
                })
            if len(out) >= max_items:
                break
        if out:
            return out
        text = self._truncate_text(transcript.get("text_zh") or transcript.get("text") or "", 360)
        return [{"start": start_ts, "end": end_ts, "text": text}] if text else []

    def _ocr_items_for_frames(self, frames: list[dict]) -> list[dict]:
        out: list[dict] = []
        for frame in frames:
            text = self._truncate_text(frame.get("ocr_text", ""), settings.fusion_ocr_text_max_chars)
            text_zh = self._truncate_text(frame.get("ocr_text_zh", ""), settings.fusion_ocr_text_max_chars)
            if not text and not text_zh:
                continue
            out.append({
                "frame_id": frame.get("frame_id"),
                "timestamp": round(float(frame.get("timestamp") or 0.0), 2),
                "text": text,
                "text_zh": text_zh,
                "engine": frame.get("ocr_engine", ""),
                "language": frame.get("ocr_language", ""),
                "confidence": frame.get("ocr_confidence", 0.0),
            })
        return out

    def _attach_ocr_asset_rels(self, ocr_track: dict, job_root: Path) -> None:
        for state in ocr_track.get("states", []) or []:
            state["frame_asset_rel"] = self._to_job_rel(state.get("frame_path"), job_root)

    @staticmethod
    def _frame_ocr_as_external_ocr(frame_ocr: dict, timestamp: float) -> list[dict]:
        if not frame_ocr.get("text") and not frame_ocr.get("text_zh"):
            return []
        return [{
            "state_id": frame_ocr.get("state_id", ""),
            "text": frame_ocr.get("text", ""),
            "text_zh": frame_ocr.get("text_zh", ""),
            "start_ts": timestamp,
            "end_ts": timestamp,
            "sample_ts": timestamp,
            "matched_frame_ts": timestamp,
            "alignment_confidence": 1.0,
            "confidence": frame_ocr.get("confidence", 0.0),
            "confidence_label": frame_ocr.get("confidence_label", ""),
            "engine": frame_ocr.get("engine", ""),
            "language": frame_ocr.get("language", ""),
            "language_hint": frame_ocr.get("language_hint", ""),
        }]

    def _render_frame_prompt(self, external_ocr: list[dict]) -> str:
        return (
            self.prompt_set.frame_prompt
            + "\n\n外部 OCR 结果（可信文字来源，可能为空）：\n"
            + json.dumps(external_ocr, ensure_ascii=False, indent=2)
        )

    def _translate_transcript_if_needed(self, transcript: dict, video_label: str, *, audio_path: Path | None = None) -> dict:
        text = (transcript.get("text") or "").strip()
        language = str(transcript.get("language") or "")
        if not text:
            return transcript
        trust_language_label = not (
            str(transcript.get("asr_engine") or transcript.get("provider") or "").lower() == "dolphin"
            and str(settings.dolphin_lang_sym or "").lower().startswith("ug")
        )
        if not self.translator.should_translate(text, language, trust_language_label=trust_language_label):
            return transcript
        arabic_ratio = self.translator.arabic_script_ratio(text)
        mms_result = {}
        if audio_path and settings.use_remote_mms_asr:
            job_store.log(
                self.job_id,
                f"{video_label}：ASR 文本阿拉伯字母比例={arabic_ratio:.2f}，开始 MMS 维语复核",
            )
            mms_result = self.mms_audio.transcribe(audio_path)
            transcript["mms"] = self._compact_mms_result(mms_result)
            if mms_result.get("raw") is not None:
                transcript["mms_raw"] = mms_result.get("raw")
            if mms_result.get("error"):
                job_store.log(self.job_id, f"{video_label}：MMS 复核失败：{mms_result.get('error')}")
            else:
                job_store.log(
                    self.job_id,
                    f"{video_label}：MMS 复核完成，文本长度={len(mms_result.get('text', ''))}",
                )
        elif audio_path:
            transcript["mms"] = {
                "enabled": False,
                "reason": "USE_REMOTE_MMS_ASR=false",
            }

        if settings.asr_translate_engine == "qwen_text":
            job_store.log(
                self.job_id,
                f"{video_label}：开始 LLM ASR 分段翻译，model={settings.asr_translate_model}，保留 Dolphin 时间戳",
            )
            translation = self._translate_asr_segments_with_llm(transcript, mms_result)
        else:
            job_store.log(self.job_id, f"{video_label}：开始 HY-MT ASR 翻译")
            translation = self.translator.translate_if_needed(
                text,
                source_language=language or "ug",
                context="video_audio",
            )
        transcript["translation"] = translation
        if translation.get("segments"):
            self._apply_asr_translation_segments(transcript, translation)
        if translation.get("text"):
            transcript["text_zh"] = translation["text"]
            detail = ""
            if translation.get("elapsed_seconds") is not None:
                detail = (
                    f"，耗时={float(translation.get('elapsed_seconds') or 0.0):.1f}s"
                    f"，prompt_chars={int(translation.get('prompt_chars') or 0)}"
                    f"，segments={int(translation.get('segment_count') or 0)}"
                    f"，mms_chars={int(translation.get('mms_reference_chars') or 0)}"
                )
            job_store.log(self.job_id, f"{video_label}：ASR 翻译完成，译文长度={len(translation['text'])}{detail}")
        else:
            job_store.log(self.job_id, f"{video_label}：ASR 翻译未完成：{translation.get('error') or translation.get('reason')}")
        return transcript

    @staticmethod
    def _compact_mms_result(mms_result: dict) -> dict:
        if not mms_result:
            return {}
        return {
            "provider": mms_result.get("provider", "mms"),
            "asr_engine": mms_result.get("asr_engine", "mms"),
            "text": mms_result.get("text", ""),
            "language": mms_result.get("language", "ug"),
            "model": mms_result.get("model", ""),
            "target_lang": mms_result.get("target_lang", ""),
            "chunks": (mms_result.get("chunks") or [])[:20],
            "elapsed_seconds": mms_result.get("elapsed_seconds"),
            "error": mms_result.get("error", ""),
        }

    def _translate_asr_segments_with_llm(self, transcript: dict, mms_result: dict) -> dict:
        segments = self._compact_asr_segments_for_translation(transcript)
        if not segments:
            return {"translated": False, "text": "", "reason": "no ASR segments"}
        mms_text = self._truncate_text(mms_result.get("text", ""), 2400)
        prompt = self._render_asr_translation_prompt(
            transcript=transcript,
            segments=segments,
            mms_text=mms_text,
        )
        try:
            started = perf_counter()
            result = self.qwen.audit_text(prompt, max_tokens=1400, model=settings.asr_translate_model)
        except Exception as exc:
            return {"translated": False, "text": "", "error": str(exc), "provider": "qwen_text"}
        translation = self._normalize_asr_translation(result, segments)
        translation["elapsed_seconds"] = perf_counter() - started
        translation["prompt_chars"] = len(prompt)
        translation["segment_count"] = len(segments)
        translation["mms_reference_chars"] = len(mms_text)
        return translation

    def _compact_asr_segments_for_translation(self, transcript: dict) -> list[dict]:
        out: list[dict] = []
        used_chars = 0
        for idx, seg in enumerate(transcript.get("segments") or [], start=1):
            text = self._truncate_text(seg.get("text", ""), 220)
            if not text:
                continue
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") if seg.get("end") is not None else start)
            except (TypeError, ValueError):
                start = 0.0
                end = 0.0
            used_chars += len(text)
            if settings.transcript_max_chars > 0 and used_chars > settings.transcript_max_chars:
                break
            out.append({
                "index": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
            })
        if out:
            return out
        text = self._truncate_text(transcript.get("text", ""), settings.transcript_max_chars)
        return [{"index": 1, "start": 0.0, "end": 0.0, "text": text}] if text else []

    def _render_asr_translation_prompt(self, *, transcript: dict, segments: list[dict], mms_text: str) -> str:
        payload = {
            "language": transcript.get("language") or "ug",
            "primary_asr_engine": transcript.get("asr_engine") or transcript.get("provider") or "dolphin",
            "reference_asr_engine": "mms" if mms_text else "",
            "dolphin_segments": segments,
            "mms_reference_text": mms_text,
        }
        return (
            "你是维吾尔语 ASR 复核与中文翻译器。请以 dolphin_segments 的 start/end 为唯一时间戳来源，"
            "逐段翻译为中文。MMS 文本只是复核参考，用来帮助判断 Dolphin 是否漏识别、重复或明显错听；"
            "不要根据 MMS 重新创造时间戳，不要合并或拆分 Dolphin 段。\n\n"
            "只输出合法 JSON，字段必须完全一致：\n"
            "{\n"
            '  "segments": [\n'
            "    {\n"
            '      "index": 1,\n'
            '      "start": 0.0,\n'
            '      "end": 0.0,\n'
            '      "source_text": "原 ASR 文本",\n'
            '      "translation_zh": "中文译文",\n'
            '      "mms_reference_used": true,\n'
            '      "confidence": "high|medium|low",\n'
            '      "notes": "必要时说明 Dolphin/MMS 差异"\n'
            "    }\n"
            "  ],\n"
            '  "global_translation_zh": "按时间顺序合并后的中文译文",\n'
            '  "asr_consistency": "Dolphin 与 MMS 的一致性说明"\n'
            "}\n\n"
            "输入 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def _normalize_asr_translation(self, result: dict, source_segments: list[dict]) -> dict:
        raw_segments = result.get("segments") or []
        by_index = {}
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            if index:
                by_index[index] = item
        segments = []
        for source in source_segments:
            item = by_index.get(source["index"], {})
            translation = self._truncate_text(item.get("translation_zh", ""), 500)
            segments.append({
                "index": source["index"],
                "start": source["start"],
                "end": source["end"],
                "source_text": item.get("source_text") or source["text"],
                "translation_zh": translation,
                "mms_reference_used": bool(item.get("mms_reference_used")),
                "confidence": str(item.get("confidence") or "medium").lower(),
                "notes": self._truncate_text(item.get("notes", ""), 180),
            })
        global_text = self._truncate_text(
            result.get("global_translation_zh", "") or " ".join(seg["translation_zh"] for seg in segments if seg["translation_zh"]),
            settings.transcript_max_chars,
        )
        return {
            "translated": bool(global_text),
            "provider": "qwen_text",
            "model": settings.asr_translate_model,
            "text": global_text,
            "segments": segments,
            "asr_consistency": self._truncate_text(result.get("asr_consistency", ""), 260),
            "raw": result,
        }

    @staticmethod
    def _apply_asr_translation_segments(transcript: dict, translation: dict) -> None:
        translated_by_index = {
            int(item.get("index") or 0): item
            for item in translation.get("segments") or []
            if isinstance(item, dict)
        }
        updated = []
        for idx, seg in enumerate(transcript.get("segments") or [], start=1):
            item = translated_by_index.get(idx)
            if item:
                seg = {
                    **seg,
                    "source_text_dolphin": seg.get("text", ""),
                    "translation_zh": item.get("translation_zh", ""),
                    "mms_reference_used": item.get("mms_reference_used", False),
                    "translation_confidence": item.get("confidence", ""),
                    "translation_notes": item.get("notes", ""),
                }
            else:
                seg = {**seg, "source_text_dolphin": seg.get("text", "")}
            updated.append(seg)
        transcript["segments"] = updated

    def _persist_asr_raw(self, transcript: dict, target: Path, job_root: Path) -> dict:
        raw_payload = {}
        if transcript.get("raw") is not None:
            raw_payload["dolphin_raw"] = transcript.pop("raw")
        if transcript.get("mms_raw") is not None:
            raw_payload["mms_raw"] = transcript.pop("mms_raw")
        translation = transcript.get("translation") or {}
        if translation.get("raw") is not None:
            raw_payload["translation_raw"] = translation.pop("raw")
        mms = transcript.get("mms") or {}
        if mms.get("raw") is not None:
            raw_payload["mms_raw"] = mms.pop("raw")
        if not raw_payload:
            return transcript
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        transcript["asr_raw_path"] = str(target)
        transcript["asr_raw_rel"] = self._to_job_rel(str(target), job_root)
        return transcript
