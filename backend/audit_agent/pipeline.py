from __future__ import annotations

import hashlib
import json
import queue
import re
import shutil
import shlex
import threading
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter, time_ns

import requests

from .auth_state_cipher import auth_state_cipher
from .asr_chunks import ASROutOfMemoryError, is_asr_oom
from .asset_utils import download_url_with_error, safe_filename_from_url, split_csv_urls
from .config import settings
from .crawler_account_store import crawler_account_store
from .crawler_adapter import (
    IMAGE_EXTENSIONS,
    PLATFORM_DATA_DIRS,
    VIDEO_EXTENSIONS,
    CrawlerAuthenticationError,
    CrawlOutput,
    CrawlerVerificationError,
    CrawlerRateLimitError,
    MediaCrawlerAdapter,
)
from .account_rotation import AccountRotationManager
from .evidence_groups import build_evidence_groups
from .ingestion import AuditResultStore, BatchWriter, IngestionStore, content_identity
from .job_store import job_store
from .job_state import failure_metadata
from .knowledge_packages import get_default_knowledge_package
from .models import AuditSubject
from .ocr_processor import VideoOCRTracker
from .prompts import get_prompt_set
from .qwen_client import QwenClient, QwenTimeoutError, QwenProviderError
from .rule_compiler import DEFAULT_THRESHOLDS, compact_library_policy
from .translation import TranslationProcessor
from .video_processor import DemoAudioProcessor, DemoFrameExtractor


_crawler_lock = threading.Lock()
_analysis_locks_guard = threading.Lock()
_analysis_locks: dict[str, threading.RLock] = {}


def _analysis_lock_for(job_id: str) -> threading.RLock:
    with _analysis_locks_guard:
        return _analysis_locks.setdefault(job_id, threading.RLock())


def _authorized_crawler_account_ids(job_id: str) -> frozenset[str] | None:
    """Return public accounts plus the task owner's private accounts."""
    if settings.app_auth_mode != "required":
        return None
    job = job_store.get(job_id) or {}
    owner_user_id = str(job.get("owner_user_id") or "").strip()
    if not owner_user_id:
        return frozenset()
    from backend.application_auth.store import AuthStore

    store = AuthStore(
        settings.app_auth_db,
        default_validity_days=settings.app_account_validity_days,
        activation_mode=settings.app_account_activation_mode,
    )
    private_ids = store.owned_crawler_account_ids(owner_user_id)
    public_ids = frozenset(
        str(account["id"])
        for account in crawler_account_store.list()
        if str(account.get("access_scope") or "private") == "public"
    )
    return private_ids | public_ids

AUDIO_URL_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
AUDIO_FILE_SIGNATURES = (
    b"ID3",
    b"\xff\xfb",
    b"\xff\xf3",
    b"\xff\xf2",
    b"\xff\xf1",
    b"\xff\xf9",
)
VIDEO_TEXT_CONTEXT_MAX_CHARS = 1800
VIDEO_VISUAL_CONTEXT_MAX_CHARS = 1000
VIDEO_CANDIDATE_CONTEXT_MAX_ITEMS = 8
VIDEO_CANDIDATE_TEXT_MAX_ITEMS = 3


class FusionAuditTimeoutError(RuntimeError):
    pass


class FusionAuditContractError(RuntimeError):
    def __init__(self, message: str, *, invalid_evidence_ids=()):
        super().__init__(message)
        self.invalid_evidence_ids = list(invalid_evidence_ids)


class AuditProviderCallError(RuntimeError):
    pass


class AuditProviderUnavailableError(RuntimeError):
    pass


def search_resume_parameters(
    platform: str,
    initial_start_page: int,
    checkpoint_keyword: str,
    checkpoint_page: int,
) -> tuple[int, str, int | None]:
    """Keep Douyin's saved page scoped to its checkpoint keyword."""
    initial_page = int(initial_start_page or 0)
    saved_keyword = str(checkpoint_keyword or "").strip()
    if platform == "dy":
        initial_index = initial_page - 1 if initial_page > 0 else 0
        saved_page = max(initial_index, int(checkpoint_page))
        if saved_keyword:
            return initial_page, saved_keyword, saved_page
        return initial_page, "", None
    saved_page = max(initial_page, int(checkpoint_page))
    return saved_page, "", None


def crawler_start_page(platform: str, configured_start_page: int) -> int:
    """Keep the user-facing page number intact for the crawler CLI."""
    return max(0, int(configured_start_page or 0))


def _severity_rank(severity) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(severity).lower(), 0)


class AuditPipeline:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.qwen = QwenClient()
        self.crawler = MediaCrawlerAdapter()
        self.audio = DemoAudioProcessor()
        self.translator = TranslationProcessor()
        self.frames = DemoFrameExtractor()
        self.ocr = VideoOCRTracker(self.translator)
        self.ingestion = IngestionStore()
        self.audit_results = AuditResultStore()
        self.prompt_set = get_prompt_set("soft")
        self.prompt_profile_snapshot: dict = {}
        self.audit_config_revision_id = ""
        self.rule_snapshot: dict = {}
        self.authoritative_m3 = False

    @staticmethod
    def authoritative_provider_validator(configuration: dict) -> None:
        QwenClient.validate_authoritative_configuration(configuration)

    def _verified_m3_snapshot(self):
        validator = getattr(self, "_m3_snapshot_validator", None)
        if callable(validator):
            return validator()
        if not self.job_id.startswith("m3-"):
            return None
        # Direct Job resume must pass the same Run/Job/revision boundary as Worker.
        from backend.investigation_creation.store import InvestigationCreationStore
        from backend.investigation_creation.adapters import AuditPipelineExecutionAdapter
        run = InvestigationCreationStore().get_run_for_job(self.job_id)
        if run is None:
            raise ValueError("M3 Job has no authoritative frozen Run")
        if run.confirmed_configuration.get("schema_version") not in {"investigation-run-config-v3", "investigation-run-config-v4"}:
            return None
        adapter = AuditPipelineExecutionAdapter()
        configuration = adapter.verify_run_job(run)
        adapter.validate_m3_configuration(
            configuration,
            schema_version=str(run.confirmed_configuration.get("schema_version") or ""),
            job_id=self.job_id,
        )
        return configuration

    def run(self, request, crawl_epoch: int | None = None) -> None:
        if settings.app_auth_mode == "required":
            from backend.task_admission.execution import assert_execution, current_execution
            assert_execution(self.job_id)
        from backend.task_admission.execution import current_execution
        admission_execution = current_execution()
        self._admission_execution = admission_execution
        crawl_epoch_is_current = lambda: True
        crawler_account_id = ""
        try:
            job_snapshot = job_store.get(self.job_id) or {}
            initial_control = dict(job_snapshot.get("control") or {})
            active_crawl_epoch = int(
                initial_control.get("crawl_epoch") or 0
                if crawl_epoch is None
                else crawl_epoch
            )

            def control() -> dict:
                result = job_store.control(self.job_id)
                if settings.app_auth_mode == "required":
                    from backend.task_admission.execution import assert_execution
                    from backend.task_admission.store import AdmissionError
                    try:
                        assert_execution(self.job_id, admission_execution)
                    except AdmissionError:
                        result.update(crawl_stop_requested=True,analysis_stop_requested=True,stop_all_requested=True)
                return result

            def crawl_epoch_is_current() -> bool:
                return int(control().get("crawl_epoch") or 0) == active_crawl_epoch

            analysis_suspended = bool(
                initial_control.get("analysis_paused")
                or initial_control.get("analysis_stop_requested")
                or str(job_snapshot.get("analysis_status") or "") in {"paused", "stopped"}
            )
            job_store.update(
                self.job_id,
                status="running",
                crawl_status="running" if request.run_crawler else "skipped",
                analysis_status=(
                    str(job_snapshot.get("analysis_status") or "pending")
                    if analysis_suspended
                    or str(job_snapshot.get("analysis_status") or "")
                    in {"running", "pausing"}
                    else "pending"
                ),
            )
            job_store.update_control(self.job_id, failure={})
            job_store.log(self.job_id, "开始任务")
            verified = self._verified_m3_snapshot()
            if verified is not None:
                from types import SimpleNamespace
                request = SimpleNamespace(**{**verified, "_authoritative_m3_contract": True,
                    "_confirmed_analyze_limit": verified["analyze_limit"]})
                self.audit_config_revision_id = verified["_verified_audit_config_revision_id"]
                self.rule_snapshot = verified["rule_snapshot"]
                self._set_prompt_context(self._prompt_category_from_source(request), verified["prompt_profile_snapshot"])
            else:
                self.audit_config_revision_id = str(job_snapshot.get("current_audit_config_revision_id") or "")
                self.rule_snapshot = self._rule_snapshot_from_source(job_snapshot or request)
                self._set_prompt_context(self._prompt_category_from_source(request), self._prompt_profile_from_source(request))

            collect_media = bool(getattr(request, "collect_media", True))
            if not collect_media:
                job_store.log(
                    self.job_id,
                    "图片与视频采集及审核已关闭：仅处理帖子文本和已采集评论",
                    stage="configuration",
                    level="info",
                    reason="任务参数 collect_media=false",
                    retryable=False,
                    action="skip_image_and_video_analysis",
                )

            configured_total_notes = int(
                getattr(request, "max_total_notes", 0) or 0
            )
            if configured_total_notes > 0:
                crawl_total_notes = configured_total_notes
            elif request.crawl_mode == "creator":
                crawl_total_notes = int(request.max_notes)
            else:
                historical_keyword_count = max(
                    1,
                    len(
                        [
                            term
                            for term in str(request.keyword or "").split(",")
                            if term.strip()
                        ]
                    ),
                )
                crawl_total_notes = int(request.max_notes) * historical_keyword_count

            def analysis_stop_requested() -> bool:
                current = control()
                return bool(current.get("stop_all_requested") or current.get("analysis_stop_requested"))

            crawl_finished = threading.Event()

            def wait_if_analysis_paused() -> bool:
                pause_announced = False
                while True:
                    current = control()
                    if current.get("stop_all_requested") or current.get(
                        "analysis_stop_requested"
                    ):
                        job_store.update(self.job_id, analysis_status="stopped")
                        return False
                    if not current.get("analysis_paused"):
                        return True
                    if not pause_announced:
                        job_store.update(
                            self.job_id,
                            status="analysis_paused",
                            analysis_status="paused",
                        )
                        job_store.log(
                            self.job_id,
                            "分析已在安全边界暂停，采集与入库继续",
                            stage="control",
                        )
                        pause_announced = True
                    # Do not keep an ended crawl alive solely to wait for a
                    # paused analyzer. Persisted queued contents can be resumed.
                    if crawl_finished.is_set():
                        return False
                    threading.Event().wait(0.2)

            confirmed_limit_value = getattr(request, "_confirmed_analyze_limit", None)
            self.authoritative_m3 = (
                getattr(request, "_authoritative_m3_contract", None) is True
            )
            selection_limit = (
                max(0, int(confirmed_limit_value))
                if confirmed_limit_value is not None and getattr(request, "auto_analyze", None) is None
                else None
            )
            selected_memberships: list[dict] = []
            selected_refs: list[dict] = []
            selected_identities: set[str] = set()
            if request.run_crawler:
                source_root = settings.outputs_dir / self.job_id
                crawl_dir = source_root / "crawler"
                job_store.log(self.job_id, f"等待 MediaCrawler 爬取锁：{request.platform}")
                last_progress = {"done": -1}
                # A resumed task keeps every already-persisted result, regardless
                # of whether it came from an authoritative M3 run or an ordinary
                # Job. This is the durable idempotency boundary for analysis.
                results: list[dict] = list(job_snapshot.get("items") or [])
                analyzed_ids: set[str] = {
                    str(item.get("content_key") or item.get("note_id") or "")
                    for item in results
                    if item.get("content_key") or item.get("note_id")
                }
                analyze_limit = max(0, request.analyze_limit)
                stream_queue: queue.Queue[dict | tuple[list[dict], list[dict]] | None] = queue.Queue()
                analysis_errors: list[BaseException] = []
                batch_writer = BatchWriter(
                    job_id=self.job_id,
                    platform=request.platform,
                    keyword=request.keyword if request.crawl_mode == "search" else (request.creator_url or request.creator_id),
                    category=request.lexicon_category if getattr(request, "keyword_source", "keyword") == "lexicon" else "",
                    root=source_root,
                    batch_size=1 if selection_limit is not None else None,
                )
                raw_items_dir = source_root / "raw_items"
                stop_flusher = threading.Event()
                auto_analyze_crawled_content = bool(
                    settings.auto_analyze_crawled_content
                    and getattr(request, "auto_analyze", True)
                    and analyze_limit > 0
                )
                if not auto_analyze_crawled_content:
                    analyze_limit = 0
                    selection_limit = None
                    job_store.update(self.job_id, analysis_status="pending")
                    job_store.log(
                        self.job_id,
                        "当前为采集入库模式：内容会入库，审核分析暂不自动执行，可稍后点击继续分析",
                    )
                analysis_media_scope = self._analysis_media_scope()
                if analysis_media_scope == "image_text":
                    job_store.log(self.job_id, "当前分析范围：仅分析图文内容，视频内容只入库不审核")
                stream_items_enabled = (
                    settings.stream_crawl_analysis or selection_limit is not None
                )
                use_batch_ingestion = selection_limit is not None or (
                    stream_items_enabled and settings.batch_ingestion_enabled
                )
                stream_analysis = (
                    stream_items_enabled
                    and auto_analyze_crawled_content
                )
                if stream_analysis and analyze_limit > 0 and not analysis_suspended:
                    job_store.update(self.job_id, analysis_status="running")
                skip_final_supplement = use_batch_ingestion and auto_analyze_crawled_content
                stream_callback_enabled = use_batch_ingestion or stream_analysis
                if selection_limit is not None:
                    (
                        selected_memberships,
                        selected_refs,
                    ) = self.ingestion.validated_selection_for_task(self.job_id)
                    selected_identities = {
                        str(membership.get("content_key") or "")
                        for membership in selected_memberships
                        if membership.get("content_key")
                    }
                crawler_concurrency = max(
                    1,
                    min(
                        int(request.max_concurrency or 1),
                        max(1, settings.crawler_max_concurrency),
                    ),
                )
                if crawler_concurrency < int(request.max_concurrency or 1):
                    job_store.log(
                        self.job_id,
                        f"采集并发已限速：请求={request.max_concurrency}，实际={crawler_concurrency}",
                    )
                def crawl_stop_requested() -> bool:
                    current = control()
                    return bool(current.get("crawl_stop_requested") or current.get("stop_all_requested"))

                def log_crawl_progress(done: int, total: int) -> None:
                    if done <= 0 or done == last_progress["done"]:
                        return
                    last_progress["done"] = done
                    job_store.log(self.job_id, f"已爬取 {done}/{total} 条")

                def persist_crawl_checkpoint(keyword: str, page: int) -> None:
                    # The crawler reports the page it is about to request. Persisting
                    # that boundary lets the same task resume without changing the
                    # frozen configuration or inheriting another task's cursor.
                    job_store.update(
                        self.job_id,
                        crawl_checkpoint_keyword=keyword,
                        crawl_checkpoint_page=page,
                    )

                def refresh_selected_contents() -> None:
                    (
                        refreshed_memberships,
                        refreshed_refs,
                    ) = self.ingestion.validated_selection_for_task(
                        self.job_id
                    )
                    selected_memberships[:] = refreshed_memberships
                    selected_refs[:] = refreshed_refs
                    selected_identities.clear()
                    selected_identities.update(
                        str(membership.get("content_key") or "")
                        for membership in refreshed_memberships
                        if membership.get("content_key")
                    )

                def enqueue_stream_batch(
                    contents: list[dict], comments: list[dict]
                ) -> None:
                    if selection_limit is not None:
                        refresh_selected_contents()
                        for content in contents:
                            identity = content_identity(content, request.platform)
                            if (
                                not identity
                                or identity in selected_identities
                                or len(selected_memberships) >= selection_limit
                            ):
                                continue
                            batch_paths = batch_writer.add([content], comments)
                            for batch_path in batch_paths:
                                ingest_completed_batch(batch_path)
                            refresh_selected_contents()
                        return
                    if not contents:
                        return
                    if use_batch_ingestion:
                        for batch_path in batch_writer.add(contents, comments):
                            ingest_completed_batch(batch_path)
                        return
                    if stream_analysis:
                        stream_queue.put((contents, comments))

                def ingest_completed_batch(batch_path: Path) -> None:
                    queued = self.ingestion.ingest_batch(batch_path, raw_items_dir)
                    job_store.log(
                        self.job_id,
                        f"ingestion 完成 batch：{batch_path.name}，新内容 {len(queued)} 条",
                    )
                    if stream_analysis:
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
                        include_media=collect_media,
                    )
                    for subject in subjects:
                        subject_key = content_key or content_identity(ref["item"], request.platform) or subject.note_id
                        if not subject_key or subject_key in analyzed_ids:
                            continue
                        if not self._should_analyze_subject(subject):
                            analyzed_ids.add(subject_key)
                            self._mark_subject_skipped(request.platform, subject_key, subject)
                            continue
                        if analyze_limit and len(results) >= analyze_limit:
                            return
                        if not wait_if_analysis_paused():
                            return
                        claim = self.ingestion.claim_content_for_analysis(
                            self.job_id,
                            request.platform,
                            subject_key,
                            analyze_limit=analyze_limit,
                        )
                        if claim is False:
                            analyzed_ids.add(subject_key)
                            continue
                        analyzed_ids.add(subject_key)
                        job_store.log(self.job_id, f"边抓边分析：{subject.note_id}")
                        try:
                            self._begin_subject_audit()
                            result = self._analyze_subject(subject)
                            self._assert_authoritative_provider_healthy()
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
                        except Exception as exc:
                            self._record_subject_failure(request.platform, subject_key, subject, exc)
                            analyzed_ids.add(subject.note_id)
                            continue
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
                        include_media=collect_media,
                    )
                    for subject in subjects:
                        if not subject.note_id or subject.note_id in analyzed_ids:
                            continue
                        subject_key = subject.note_id
                        if not self._should_analyze_subject(subject):
                            analyzed_ids.add(subject.note_id)
                            self._mark_subject_skipped(request.platform, subject.note_id, subject)
                            continue
                        if analyze_limit and len(results) >= analyze_limit:
                            return
                        if not wait_if_analysis_paused():
                            return
                        claim = self.ingestion.claim_content_for_analysis(
                            self.job_id,
                            request.platform,
                            subject_key,
                            analyze_limit=analyze_limit,
                        )
                        if claim is False:
                            analyzed_ids.add(subject.note_id)
                            continue
                        analyzed_ids.add(subject.note_id)
                        job_store.log(self.job_id, f"边抓边分析：{subject.note_id}")
                        try:
                            self._begin_subject_audit()
                            result = self._analyze_subject(subject)
                            self._assert_authoritative_provider_healthy()
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
                        except Exception as exc:
                            self._record_subject_failure(request.platform, subject_key, subject, exc)
                            analyzed_ids.add(subject.note_id)
                            continue
                        results.append(persisted or result)
                        job_store.update(self.job_id, items=results)
                        job_store.log(self.job_id, f"边抓边分析完成：{subject.note_id}")

                def consume_stream_queue() -> None:
                    try:
                        with _analysis_lock_for(self.job_id):
                            latest_items = (job_store.get(self.job_id) or {}).get("items") or []
                            results[:] = list(latest_items)
                            analyzed_ids.update(
                                str(item.get("content_key") or item.get("note_id") or "")
                                for item in results
                            )
                            while True:
                                current = control()
                                if current.get("analysis_stop_requested") or current.get("analysis_paused") or current.get("stop_all_requested"):
                                    job_store.update(self.job_id, analysis_status=(
                                        "paused" if current.get("analysis_paused") else "stopped"
                                    ))
                                    if crawl_finished.wait(0.1):
                                        return
                                    continue
                                try:
                                    batch = stream_queue.get(timeout=0.1)
                                except queue.Empty:
                                    if crawl_finished.is_set():
                                        return
                                    continue
                                if batch is None:
                                    return
                                if isinstance(batch, tuple):
                                    contents, comments = batch
                                    analyze_stream_batch(contents, comments)
                                else:
                                    analyze_stream_ref(batch)
                    except Exception as exc:
                        analysis_errors.append(exc)
                        job_store.update_control(self.job_id, analysis_stop_requested=True)
                        job_store.log(
                            self.job_id,
                            f"审核线程异常：{type(exc).__name__}；仅停止审核，采集继续",
                            level="error",
                            reason="审核线程发生不可恢复异常",
                            error_code="analysis_thread_failed",
                            retryable=False,
                            action="stop_analysis_only",
                        )

                stream_analyzer = None
                batch_flusher = None
                if stream_analysis:
                    stream_analyzer = threading.Thread(
                        target=consume_stream_queue,
                        name=f"audit-stream-analyzer-{self.job_id}",
                        daemon=True,
                    )
                    stream_analyzer.start()
                if use_batch_ingestion and selection_limit is None:
                    batch_flusher = threading.Thread(
                        target=flush_batches_periodically,
                        name=f"audit-batch-flusher-{self.job_id}",
                        daemon=True,
                    )
                    batch_flusher.start()

                crawl_returned = False
                try:
                    with (_crawler_lock if settings.app_auth_mode != "required" else nullcontext()):
                        from backend.task_admission.resources import account_lease, selected_account
                        execution_account = control().get("execution_account") or {}
                        preferred_account_id = str(
                            selected_account()
                            or execution_account.get("id")
                            or getattr(request, "crawler_account_id", "")
                            or ""
                        ).strip()
                        candidates = crawler_account_store.available_accounts(request.platform)
                        authorized_account_ids = _authorized_crawler_account_ids(
                            self.job_id
                        )
                        if authorized_account_ids is not None:
                            candidates = [
                                item
                                for item in candidates
                                if str(item.get("id") or "")
                                in authorized_account_ids
                            ]
                        candidates.sort(
                            key=lambda item: 0 if str(item.get("id")) == preferred_account_id else 1
                        )
                        if preferred_account_id:
                            configured = crawler_account_store.get(preferred_account_id)
                            if configured and configured["platform"] != request.platform:
                                raise RuntimeError("所选采集账号与任务平台不匹配")
                        # Historical non-account Jobs remain readable. Account-backed
                        # Jobs must use the current eligible pool, not a stale identity.
                        if not candidates and preferred_account_id:
                            configured = crawler_account_store.get(preferred_account_id)
                            if configured and configured.get("failure_kind") == "verify":
                                raise RuntimeError(
                                    "crawler_account_verification_required: 当前没有结束冷却的可用账号。"
                                )
                            raise CrawlerAuthenticationError("当前没有可用采集账号，请重新登录")
                        if not candidates and settings.app_auth_mode == "required":
                            raise CrawlerAuthenticationError(
                                "当前没有已授权且可用的采集账号，请联系管理员"
                            )
                        if not candidates:
                            candidates = [None]

                        crawler_account_id = ""
                        account = None
                        account_auth_state = None
                        last_account_error: Exception | None = None
                        empty_recheck_used = False

                        def mark_crawler_started() -> None:
                            if crawler_account_id:
                                crawler_account_store.mark_used(crawler_account_id)

                        def record_execution_account() -> None:
                            # Runtime identity is separate from the confirmed M3
                            # payload. Keep legacy Job fields for existing callers.
                            if not crawler_account_id or not account:
                                return
                            if admission_execution is not None:
                                admission_store, admission_task, admission_token = admission_execution
                                with admission_store.connect() as db:
                                    db.execute("UPDATE task_admissions SET resource_account_id=? WHERE task_id=? AND execution_token=? AND state='RESERVED'", (crawler_account_id, admission_task, admission_token))
                            identity = {"id": crawler_account_id,
                                        "display_name": str(account.get("display_name") or crawler_account_id)}
                            job_store.update_control(self.job_id, execution_account=identity)
                            if not self.authoritative_m3:
                                job_store.update(self.job_id, crawler_account_id=identity["id"],
                                                 crawler_account_display_name=identity["display_name"])

                        def latest_resume_parameters():
                            latest = job_store.get(self.job_id) or {}
                            initial_page = crawler_start_page(
                                request.platform, int(request.start_page or 0)
                            )
                            checkpoint_keyword = str(
                                latest.get("crawl_checkpoint_keyword") or ""
                            )
                            checkpoint_page = latest.get("crawl_checkpoint_page")
                            if not checkpoint_keyword or checkpoint_page is None:
                                checkpoint_page = initial_page
                            return search_resume_parameters(
                                request.platform,
                                initial_page,
                                checkpoint_keyword,
                                int(checkpoint_page),
                            )

                        skip_content_ids_file = (
                            Path(str(request.skip_content_ids_file))
                            if getattr(request, "skip_content_ids_file", "") else None
                        )
                        def run_with_current_account(save_root: Path):
                            if request.crawl_mode == "creator":
                                creator_ref = request.creator_url or request.creator_id
                                job_store.log(self.job_id, f"启动 MediaCrawler 博主主页爬取 {request.platform}: {creator_ref}")
                                return self.crawler.run_creator(
                                    platform=request.platform,
                                    creator_id=creator_ref,
                                    max_notes=min(
                                        request.max_notes,
                                        crawl_total_notes,
                                    ),
                                    max_comments=request.max_comments,
                                    max_concurrency=crawler_concurrency,
                                    max_items_per_minute=int(getattr(request, "max_items_per_minute", 5) or 5),
                                    get_sub_comment=request.get_sub_comment,
                                    collect_comments=bool(getattr(request, "collect_comments", True)),
                                    collect_media=bool(getattr(request, "collect_media", True)),
                                    save_root=save_root,
                                    progress_callback=log_crawl_progress,
                                    content_callback=enqueue_stream_batch if stream_callback_enabled else None,
                                    stream_items=stream_callback_enabled,
                                    stop_checker=crawl_stop_requested,
                                    auth_state=account_auth_state,
                                    account_id=crawler_account_id,
                                    started_callback=mark_crawler_started,
                                )
                            if getattr(request, "keyword_source", "keyword") == "lexicon":
                                job_store.log(
                                    self.job_id,
                                    f"词库展开 {len(getattr(request, 'lexicon_keywords', []) or [])} 个关键词：{request.lexicon_category}",
                                )
                            job_store.log(self.job_id, f"启动 MediaCrawler 关键词爬取 {request.platform}: {request.keyword}")
                            crawler_start_page, crawler_resume_keyword, crawler_resume_page = latest_resume_parameters()
                            return self.crawler.run_search(
                                platform=request.platform,
                                keyword=request.keyword,
                                start_page=crawler_start_page,
                                max_notes=request.max_notes,
                                max_total_notes=crawl_total_notes,
                                max_comments=request.max_comments,
                                max_concurrency=crawler_concurrency,
                                max_items_per_minute=int(getattr(request, "max_items_per_minute", 5) or 5),
                                get_sub_comment=request.get_sub_comment,
                                collect_comments=bool(getattr(request, "collect_comments", True)),
                                collect_media=bool(getattr(request, "collect_media", True)),
                                save_root=save_root,
                                progress_callback=log_crawl_progress,
                                content_callback=enqueue_stream_batch if stream_callback_enabled else None,
                                stream_items=stream_callback_enabled,
                                stop_checker=crawl_stop_requested,
                                auth_state=account_auth_state,
                                account_id=crawler_account_id,
                                started_callback=mark_crawler_started,
                                checkpoint_callback=persist_crawl_checkpoint,
                                skip_content_ids_file=skip_content_ids_file,
                                reusable_content_db=self.ingestion.db_path if request.platform == "dy" else None,
                                current_task_id=self.job_id,
                                resume_keyword=crawler_resume_keyword,
                                resume_page=crawler_resume_page,
                            )

                        output = None
                        for candidate_index, candidate in enumerate(candidates):
                            if crawl_stop_requested():
                                break
                            account = candidate
                            crawler_account_id = str((candidate or {}).get("id") or "")
                            with account_lease(request.platform, crawler_account_id) as account_acquired:
                                if not account_acquired:
                                    continue
                                if crawler_account_id and crawler_account_id not in {
                                    item["id"] for item in crawler_account_store.available_accounts(request.platform)
                                }:
                                    # Another task may have cooled or expired this
                                    # account since our candidate snapshot.
                                    continue
                                # Selection and use share the lease with login and
                                # maintenance; grants are checked again at execution.
                                allowed = _authorized_crawler_account_ids(self.job_id)
                                if allowed is not None and crawler_account_id not in allowed:
                                    continue
                                if crawler_account_id:
                                    try:
                                        account_auth_state = auth_state_cipher.decrypt(
                                            crawler_account_store.get_auth_state_ciphertext(crawler_account_id)
                                        )
                                    except Exception as exc:
                                        crawler_account_store.mark_expired(crawler_account_id, str(exc))
                                        last_account_error = CrawlerAuthenticationError("登录态无法读取，请重新登录")
                                        job_store.log(self.job_id, "采集账号登录态无法读取，继续尝试账号池中的下一账号")
                                        continue
                                    record_execution_account()
                                    if not self.authoritative_m3:
                                        job_store.log(
                                            self.job_id,
                                            f"执行账号：{account.get('display_name') or crawler_account_id}",
                                        )
                                else:
                                    account_auth_state = None

                                save_root = (
                                    crawl_dir
                                    if candidate_index == 0
                                    else crawl_dir / f"rotation-{crawler_account_id or candidate_index}"
                                )
                                try:
                                    candidate_output = run_with_current_account(save_root)
                                except CrawlerVerificationError as exc:
                                    last_account_error = exc
                                    if crawler_account_id:
                                        AccountRotationManager(crawler_account_store, auth_state_cipher).cool_down(
                                            crawler_account_id, reason="verify", cooldown_seconds=300
                                        )
                                    remaining = len(candidates) - candidate_index - 1
                                    job_store.log(
                                        self.job_id,
                                        "账号触发平台验证，已进入冷却；"
                                        + (f"继续尝试剩余 {remaining} 个可用账号" if remaining else "本轮账号池已耗尽"),
                                    )
                                    continue
                                except CrawlerAuthenticationError as exc:
                                    last_account_error = exc
                                    if crawler_account_id:
                                        crawler_account_store.mark_expired(crawler_account_id, str(exc))
                                    remaining = len(candidates) - candidate_index - 1
                                    job_store.log(
                                        self.job_id,
                                        "账号登录态已失效；"
                                        + (f"继续尝试剩余 {remaining} 个可用账号" if remaining else "本轮账号池已耗尽"),
                                    )
                                    continue

                                if (
                                    request.crawl_mode == "search"
                                    and not candidate_output.contents
                                    and crawler_account_id
                                    and not crawl_stop_requested()
                                    and not empty_recheck_used
                                    and candidate_index + 1 < len(candidates)
                                ):
                                    empty_recheck_used = True
                                    AccountRotationManager(crawler_account_store, auth_state_cipher).cool_down(
                                        crawler_account_id, reason="empty_search", cooldown_seconds=300
                                    )
                                    job_store.log(self.job_id, "搜索结果为空，使用账号池中的下一账号复核")
                                    continue
                                output = candidate_output
                                break

                        if output is None:
                            if crawl_stop_requested():
                                # A user stop wins a race with an account error. The
                                # same Job/checkpoint remains resumable and must not
                                # be rewritten as FAILED merely because no adapter
                                # result was returned before the safe stop boundary.
                                output = CrawlOutput(
                                    platform=request.platform,
                                    contents=[],
                                    comments=[],
                                    output_dir=crawl_dir,
                                )
                            elif last_account_error is not None:
                                raise last_account_error
                            else:
                                raise RuntimeError("crawler_account_login_required: 当前没有可用采集账号。")
                    crawl_returned = True
                finally:
                    if batch_flusher:
                        stop_flusher.set()
                        batch_flusher.join()
                    if use_batch_ingestion:
                        for batch_path in batch_writer.flush_due(force=True):
                            ingest_completed_batch(batch_path)
                    if crawl_returned and crawl_epoch_is_current():
                        crawl_was_stopped = crawl_stop_requested()
                        job_store.update(
                            self.job_id,
                            status="crawl_paused" if crawl_was_stopped else "analysis_running",
                            crawl_status="stopped" if crawl_was_stopped else "completed",
                        )
                        job_store.log(
                            self.job_id,
                            "采集已停止，后台继续处理已入库内容"
                            if crawl_was_stopped
                            else "采集已完成，后台继续处理已入库内容",
                            stage="control",
                        )
                    if stream_analyzer:
                        crawl_finished.set()
                        stream_queue.put(None)
                        stream_analyzer.join()
                if analysis_errors:
                    job_store.update_control(self.job_id, analysis_stop_requested=True)
                    job_store.log(self.job_id, "审核线程已停止，采集已独立完成；待审核内容保留")
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
                crawler_concurrency = request.max_concurrency

            job_store.log(
                self.job_id,
                f"crawl limits requested: max_notes={request.max_notes}, "
                f"max_total_notes={crawl_total_notes}, "
                f"max_comments={request.max_comments}, "
                f"max_items_per_minute={getattr(request, 'max_items_per_minute', 5)}, "
                f"max_concurrency={request.max_concurrency}, effective_max_concurrency="
                f"{crawler_concurrency}, "
                f"crawler_sleep_seconds={settings.crawler_sleep_seconds}",
            )
            job_store.log(
                self.job_id,
                f"crawler output loaded: contents={len(output.contents)}, comments={len(output.comments)}",
            )
            if selection_limit is not None:
                if request.run_crawler:
                    enqueue_stream_batch(output.contents, output.comments)
                    (
                        selected_memberships,
                        selected_refs,
                    ) = self.ingestion.validated_selection_for_task(
                        self.job_id
                    )
                else:
                    (
                        selected_memberships,
                        selected_refs,
                    ) = self.ingestion.validated_selection_for_task(
                        self.job_id
                    )
                    if not selected_memberships:
                        output.contents = self._select_distinct_contents(
                            output.contents,
                            output.platform,
                            selection_limit,
                        )
                if request.run_crawler and not selected_memberships:
                    raise RuntimeError(
                        "no_valid_content_selected: crawler returned no content "
                        "with a valid content identity"
                    )
                if not request.run_crawler and not selected_memberships and not output.contents:
                    raise RuntimeError(
                        "no_valid_content_selected: source output contained no content "
                        "with a valid content identity"
                    )
                if selected_memberships:
                    output.contents = [dict(ref["item"]) for ref in selected_refs]
                    output.comments = [
                        dict(comment)
                        for ref in selected_refs
                        for comment in ref.get("comments", [])
                    ]
                skip_final_supplement = False
                job_store.log(
                    self.job_id,
                    f"已从采集结果中选取 {len(output.contents)} 条内容进行研判",
                )
            if request.run_crawler and not output.contents and not output.comments:
                job_store.log(self.job_id, self._empty_crawl_hint(crawl_dir, request.platform))
            ingested_refs_by_key = {}
            if selection_limit is not None and request.run_crawler:
                ingested_refs_by_key = self._refs_by_content_key(selected_refs)
            elif not skip_final_supplement and output.contents:
                ingested_refs_by_key = self._ingest_loaded_output(
                    platform=output.platform,
                    contents=output.contents,
                    comments=output.comments,
                    keyword=request.keyword if request.crawl_mode == "search" else (request.creator_url or request.creator_id),
                    category=request.lexicon_category if getattr(request, "keyword_source", "keyword") == "lexicon" else "",
                )
                if selection_limit is not None:
                    (
                        selected_memberships,
                        selected_refs,
                    ) = self.ingestion.validated_selection_for_task(self.job_id)
                    ingested_refs_by_key = self._refs_by_content_key(selected_refs)
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
                include_media=collect_media,
            )
            total_comments = sum(len(subject.comments) for subject in subjects)
            job_store.log(self.job_id, f"subjects built: subjects={len(subjects)}, comments={total_comments}")
            job_store.log(self.job_id, f"loaded subjects: {len(subjects)}")

            remaining_limit = max(0, analyze_limit - len(results))
            remaining_subjects = []
            if not skip_final_supplement and remaining_limit > 0:
                remaining_subjects = [
                    subject for subject in subjects if subject.note_id not in analyzed_ids
                ]
                remaining_subjects = self._filter_subjects_for_analysis(
                    output.platform,
                    remaining_subjects,
                    ingested_refs_by_key,
                )
            total = min(len(remaining_subjects), remaining_limit)
            job_store.log(
                self.job_id,
                f"analysis limit applied: analyze_limit={request.analyze_limit}, effective_analyze_limit={analyze_limit}, already_analyzed={len(results)}, will_analyze_remaining={total}",
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
                            claim = self.ingestion.claim_content_for_analysis(
                                self.job_id,
                                output.platform,
                                subject_key,
                                analyze_limit=analyze_limit,
                            )
                            if claim is False:
                                analyzed_ids.add(subject.note_id)
                                continue
                        with _analysis_lock_for(self.job_id):
                            self._begin_subject_audit()
                            result = self._analyze_subject(subject)
                            self._assert_authoritative_provider_healthy()
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
                    except Exception as exc:
                        self._record_subject_failure(output.platform, subject_key, subject, exc)
                        analyzed_ids.add(subject.note_id)
                        continue
                    results.append(persisted or result)
                    analyzed_ids.add(subject.note_id)
                    job_store.update(self.job_id, items=results)
                    job_store.log(self.job_id, f"完成第 {idx}/{total} 条：{subject.note_id}")
                if analysis_stopped:
                    break

            final_control = job_store.control(self.job_id)
            stats = self.ingestion.stats_for_task(self.job_id)
            completed_analysis_status = (
                "partial"
                if stats.get("failed_analysis_count", 0) > 0
                else "pending"
                if stats.get("queued_analysis_count", 0) > 0 or stats.get("analyzing_count", 0) > 0
                else "completed"
                if stats.get("completed_analysis_count", 0) > 0
                else "idle"
            )
            if getattr(request, "run_crawler", False) and not crawl_epoch_is_current():
                job_store.log(
                    self.job_id,
                    "旧采集轮次已结束，忽略其终态回写",
                    stage="control",
                )
            elif final_control.get("stop_all_requested"):
                job_store.update(
                    self.job_id,
                    status="stopped",
                    crawl_status="stopped",
                    analysis_status="stopped",
                    items=results,
                )
                job_store.log(self.job_id, "任务已停止")
            elif final_control.get("analysis_stop_requested"):
                reset_count = self.ingestion.reset_analyzing_for_task(self.job_id)
                job_store.update(
                    self.job_id,
                    status="analysis_stopped",
                    analysis_status="stopped",
                    items=results,
                )
                suffix = f"，{reset_count} 条处理中内容已回到待分析" if reset_count else ""
                job_store.log(self.job_id, f"分析已停止，可点击继续分析{suffix}")
            elif final_control.get("analysis_paused"):
                reset_count = self.ingestion.reset_analyzing_for_task(self.job_id)
                job_store.update(
                    self.job_id,
                    status="analysis_paused",
                    analysis_status="paused",
                    items=results,
                )
                job_store.log(self.job_id, "任务采集结束，分析保持暂停")
            elif final_control.get("crawl_stop_requested"):
                job_store.update(
                    self.job_id,
                    status="crawl_paused",
                    crawl_status="stopped",
                    analysis_status=completed_analysis_status,
                    items=results,
                )
                job_store.log(self.job_id, "任务采集已暂停，已处理当前可分析内容")
            else:
                job_store.update(
                    self.job_id,
                    status="completed",
                    crawl_status="completed" if request.run_crawler else "skipped",
                    analysis_status=completed_analysis_status,
                    items=results,
                )
                job_store.log(self.job_id, "任务完成")
        except AuditProviderUnavailableError as exc:
            if getattr(request, "run_crawler", False) and not crawl_epoch_is_current():
                job_store.log(self.job_id, "旧采集轮次审核服务不可用，未覆盖当前轮次状态")
                return
            job_store.update(
                self.job_id,
                status="failed",
                analysis_status="failed",
                error="audit_provider_unavailable: 审核服务当前不可用",
            )
            job_store.update_control(
                self.job_id,
                failure=failure_metadata(
                    "audit_provider_unavailable", "审核服务当前不可用"
                ),
            )
            job_store.log(
                self.job_id,
                f"任务失败：audit_provider_unavailable: {exc}",
                level="error",
                reason="审核服务当前不可用",
                error_code="audit_provider_unavailable",
                retryable=True,
                action="retry_later",
            )
        except AuditProviderCallError as exc:
            if getattr(request, "run_crawler", False) and not crawl_epoch_is_current():
                job_store.log(self.job_id, "旧采集轮次审核调用失败，未覆盖当前轮次状态")
                return
            job_store.update(
                self.job_id,
                status="failed",
                analysis_status="failed",
                error="audit_provider_failed: 审核服务调用失败",
            )
            job_store.update_control(
                self.job_id,
                failure=failure_metadata("audit_provider_failed", "审核服务调用失败"),
            )
            job_store.log(
                self.job_id,
                f"任务失败：audit_provider_failed: {exc}",
                level="error",
                reason="审核服务调用失败",
                error_code="audit_provider_failed",
                retryable=True,
                action="retry_later",
            )
        except Exception as exc:
            if getattr(request, "run_crawler", False) and not crawl_epoch_is_current():
                job_store.log(self.job_id, f"旧采集轮次异常结束，未覆盖当前轮次状态：{exc}")
                return
            if isinstance(exc, CrawlerVerificationError):
                if crawler_account_id:
                    AccountRotationManager(crawler_account_store, auth_state_cipher).cool_down(
                        crawler_account_id, reason="verify", cooldown_seconds=300)
                exc = RuntimeError("crawler_account_verification_required: 采集账号需要平台验证，请完成验证后再继续；登录态未判定失效。")
            elif isinstance(exc, CrawlerAuthenticationError):
                if crawler_account_id:
                    crawler_account_store.mark_expired(crawler_account_id, "登录态已失效")
                exc = RuntimeError("crawler_account_login_required: 采集账号登录态已失效，请重新登录。")
            elif isinstance(exc, CrawlerRateLimitError):
                exc = RuntimeError("crawler_rate_limited: 平台请求限流，请等待冷却结束后再继续。")
            error_text = str(exc)
            error_code = error_text.split(":", 1)[0].strip()
            current = job_store.get(self.job_id) or {}
            crawl_status = str(current.get("crawl_status") or "")
            job_store.update(
                self.job_id,
                status="failed",
                crawl_status=(crawl_status if crawl_status in {"completed", "stopped", "skipped"} else "failed"),
                analysis_status="failed",
                error=str(exc),
            )
            job_store.update_control(
                self.job_id,
                failure=failure_metadata(error_code, error_text),
            )
            job_store.log(
                self.job_id,
                f"任务失败：{exc}",
                level="error",
                reason=error_text,
                error_code=error_code or "task_failed",
                retryable=error_code in {
                    "crawler_account_verification_required",
                    "crawler_account_login_required",
                    "crawler_rate_limited",
                },
                action="retry_after_recovery",
            )

    def _begin_subject_audit(self) -> None:
        # A previous post's error must not poison the next post's valid result.
        if hasattr(getattr(self, "qwen", None), "provider_failure"):
            self.qwen.provider_failure = ""

    def _record_subject_failure(self, platform, content_key, subject, exc) -> None:
        if content_key:
            self.ingestion.mark_content_status(platform, content_key, "failed", task_id=self.job_id)
        chain = []
        cause = exc
        while cause is not None and id(cause) not in {id(e) for e in chain}:
            chain.append(cause)
            cause = cause.__cause__ or cause.__context__
        provider_failure = bool(getattr(getattr(self, "qwen", None), "provider_failure", "")) or any(
            isinstance(e, (QwenProviderError, AuditProviderUnavailableError)) for e in chain)
        if isinstance(exc, AuditProviderCallError) and any(
            isinstance(e, (requests.RequestException, TimeoutError)) for e in chain
        ):
            provider_failure = True
        # Preserve error types/status only: raw network errors can contain credentials.
        statuses = [e.response.status_code for e in chain
                    if isinstance(e, requests.HTTPError) and e.response is not None]
        # Per-post failures never issue user controls or impose a failure-count
        # circuit breaker. Drain the selected posts so valid results can report.
        messages = [str(e).lower() for e in chain]
        messages.extend(e.response.text.lower() for e in chain
                        if isinstance(e, requests.HTTPError) and e.response is not None)
        oom = any(isinstance(e, ASROutOfMemoryError) or is_asr_oom(e) for e in chain) or any(is_asr_oom(m) for m in messages)
        content_blocked = any("data_inspection_failed" in m for m in messages)
        self._begin_subject_audit()
        folder = settings.outputs_dir / self.job_id / "post_failures"
        folder.mkdir(parents=True, exist_ok=True)
        if oom:
            reason = "语音转写 GPU 显存不足，当前帖子未完成审核"
            error_code = "asr_gpu_out_of_memory"
        elif content_blocked:
            reason = "模型供应商内容安全检查拦截，本帖审核未完成，不代表已判定违规"
            error_code = "audit_content_blocked"
        elif any(isinstance(e, AuditProviderUnavailableError) for e in chain):
            reason = "审核服务未配置或不可用，当前帖子未完成审核"
            error_code = "audit_provider_unavailable"
        elif statuses:
            reason = f"审核接口返回 HTTP {statuses[-1]}"
            error_code = f"audit_http_{statuses[-1]}"
        elif any(isinstance(e, (QwenTimeoutError, requests.Timeout, TimeoutError)) for e in chain):
            reason = "审核请求超时，当前请求补试已结束"
            error_code = "audit_timeout"
        elif isinstance(exc, FusionAuditContractError):
            reason = "审核输出不满足证据或格式合同；详见阶段诊断"
            error_code = "fusion_contract_invalid"
        else:
            reason = "审核步骤执行异常；详见阶段诊断"
            error_code = "post_audit_failed"
        record = {"note_id": subject.note_id, "content_key": content_key, "reason": reason,
                  "error_code": error_code,
                  "stage": getattr(self, "_current_audit_stage", "post_audit"),
                  "error_type": type(exc).__name__, "cause_types": [type(e).__name__ for e in chain],
                  "http_statuses": statuses, "action": "skip_post"}
        (folder / f"{subject.note_id}-{time_ns()}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        job_store.log(
            self.job_id,
            f"笔记 {subject.note_id}：{reason}；已记录并跳过",
            level="error",
            reason=reason,
            error_code=error_code,
            retryable=bool(provider_failure and not oom and not content_blocked
                           and not any(code in {401, 402, 403} for code in statuses)),
            action=record["action"],
        )

    def _assert_authoritative_provider_healthy(self) -> None:
        if not getattr(self, "authoritative_m3", False):
            return
        qwen = getattr(self, "qwen", None)
        provider_failure = str(getattr(qwen, "provider_failure", "") or "")
        if provider_failure:
            raise AuditProviderCallError(provider_failure)

    def resume_pending_analysis(self, analyze_limit: int = 0, analysis_batch_size: int = 5) -> None:
        if settings.app_auth_mode == "required":
            from backend.task_admission.execution import assert_execution
            assert_execution(self.job_id)
            from backend.task_admission.execution import current_execution
            self._admission_execution = current_execution()
        with _analysis_lock_for(self.job_id):
            self._resume_pending_analysis(analyze_limit, analysis_batch_size)

    def _resume_pending_analysis(self, analyze_limit: int = 0, analysis_batch_size: int = 5) -> None:
        try:
            job = job_store.get(self.job_id)
            if not job:
                return
            verified = self._verified_m3_snapshot()
            if verified is not None:
                job = {**job, **verified, "current_audit_config_revision_id": verified["_verified_audit_config_revision_id"]}
                analyze_limit = verified["analyze_limit"]
                analysis_batch_size = verified["analysis_batch_size"]
                self.authoritative_m3 = True
            platform = str(job.get("platform") or "xhs")
            collect_media = bool(job.get("collect_media", True))
            source_root = self._resume_source_root(job)
            refs = self.ingestion.pending_for_task(self.job_id, limit=max(0, analyze_limit))
            existing_items = job.get("items") or []
            results = list(existing_items)
            analyzed_ids = {str(item.get("note_id") or "") for item in results if item.get("note_id")}
            job_store.update(self.job_id, status="analysis_running", analysis_status="running")
            self.audit_config_revision_id = str(job.get("current_audit_config_revision_id") or "")
            self.rule_snapshot = self._rule_snapshot_from_source(job)
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
            if not collect_media:
                job_store.log(
                    self.job_id,
                    "继续分析时图片与视频审核保持关闭：仅处理帖子文本和已采集评论",
                    stage="configuration",
                    level="info",
                    reason="任务冻结参数 collect_media=false",
                    retryable=False,
                    action="skip_image_and_video_analysis",
                )
            if self._analysis_media_scope() == "image_text":
                job_store.log(self.job_id, "继续分析范围：仅分析图文内容，视频内容将跳过")
            job_store.log(self.job_id, f"继续分析开始：待处理 {len(refs)} 条")
            batch_size = max(1, analysis_batch_size)
            for batch_start in range(0, len(refs), batch_size):
                if settings.app_auth_mode == "required":
                    from backend.task_admission.execution import assert_execution
                    assert_execution(self.job_id)
                current_control = job_store.control(self.job_id)
                if current_control.get("stop_all_requested") or current_control.get("analysis_stop_requested") or current_control.get("analysis_paused"):
                    break
                batch = refs[batch_start:batch_start + batch_size]
                for ref in batch:
                    current_control = job_store.control(self.job_id)
                    if current_control.get("stop_all_requested") or current_control.get("analysis_stop_requested") or current_control.get("analysis_paused"):
                        break
                    content_key = str(ref.get("content_key") or "")
                    subjects = self._build_subjects(
                        platform,
                        [ref["item"]],
                        ref.get("comments", []),
                        source_root,
                        include_media=collect_media,
                    )
                    for subject in subjects:
                        subject_key = content_key or content_identity(ref["item"], platform) or subject.note_id
                        if subject.note_id in analyzed_ids:
                            continue
                        if not self._should_analyze_subject(subject):
                            analyzed_ids.add(subject.note_id)
                            self._mark_subject_skipped(platform, subject_key, subject)
                            continue
                        analyzed_ids.add(subject.note_id)
                        job_store.log(self.job_id, f"继续分析：{subject.note_id}")
                        try:
                            claim = self.ingestion.claim_content_for_analysis(
                                self.job_id,
                                platform,
                                subject_key,
                                analyze_limit=max(0, analyze_limit),
                            )
                            if claim is False:
                                continue
                            self._begin_subject_audit()
                            result = self._analyze_subject(subject)
                            self._assert_authoritative_provider_healthy()
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
                        except Exception as exc:
                            self._record_subject_failure(platform, subject_key, subject, exc)
                            analyzed_ids.add(subject.note_id)
                            continue
            final_control = job_store.control(self.job_id)
            if final_control.get("stop_all_requested"):
                job_store.update(self.job_id, status="stopped", analysis_status="stopped", items=results)
                job_store.log(self.job_id, "继续分析已停止")
            elif final_control.get("analysis_paused"):
                job_store.update(self.job_id, status="analysis_paused", analysis_status="paused", items=results)
            elif final_control.get("analysis_stop_requested"):
                reset_count = self.ingestion.reset_analyzing_for_task(self.job_id)
                job_store.update(self.job_id, status="analysis_stopped", analysis_status="stopped", items=results)
                suffix = f"，{reset_count} 条处理中内容已回到待分析" if reset_count else ""
                job_store.log(self.job_id, f"继续分析已停止，可再次继续分析{suffix}")
            else:
                stats = self.ingestion.stats_for_task(self.job_id)
                analysis_status = (
                    "partial"
                    if stats.get("failed_analysis_count", 0) > 0
                    else "pending"
                    if stats.get("queued_analysis_count", 0) > 0 or stats.get("analyzing_count", 0) > 0
                    else "completed"
                )
                crawl_status = str((job_store.get(self.job_id) or job).get("crawl_status") or "pending")
                overall_status = (
                    "completed"
                    if crawl_status in {"completed", "skipped"}
                    else "crawl_paused"
                    if crawl_status in {"paused", "stopped"}
                    else "interrupted"
                    if crawl_status == "interrupted"
                    else "running"
                )
                job_store.update(
                    self.job_id,
                    status=overall_status,
                    crawl_status=crawl_status,
                    analysis_status=analysis_status,
                    items=results,
                )
                job_store.log(self.job_id, "继续分析完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", analysis_status="failed", error=str(exc))
            job_store.log(
                self.job_id,
                f"继续分析失败：{exc}",
                level="error",
                reason=str(exc),
                error_code="resume_analysis_failed",
                retryable=True,
                action="retry_resume_analysis",
            )

    def run_local_video(self, video_path: Path, title: str = "", desc: str = "") -> None:
        try:
            job_store.update(self.job_id, status="running")
            job_store.log(self.job_id, "开始本地视频审核任务")
            job = job_store.get(self.job_id) or {}
            self.audit_config_revision_id = str(job.get("current_audit_config_revision_id") or "")
            self.rule_snapshot = self._rule_snapshot_from_source(job)
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
            self._begin_subject_audit()
            result = self._analyze_subject(subject)
            result_path = self._write_result_json(subject.note_id, result)
            persisted = self._persist_audit_result(
                platform="local",
                content_key=subject.note_id,
                result=result,
                result_path=result_path,
            )
            job_store.update(
                self.job_id,
                status="completed",
                crawl_status="skipped",
                analysis_status="completed",
                items=[persisted or result],
            )
            job_store.log(self.job_id, "本地视频审核任务完成")
        except Exception as exc:
            job_store.update(self.job_id, status="failed", error=str(exc))
            job_store.log(
                self.job_id,
                f"本地视频审核任务失败：{exc}",
                level="error",
                reason=str(exc),
                error_code="local_video_audit_failed",
                retryable=False,
                action="inspect_input_and_retry",
            )

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
            model_text=str(
                (result.get("model_provenance") or {}).get("model")
                or settings.qwen_text_model
            ),
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

    @staticmethod
    def _rule_snapshot_from_source(source) -> dict:
        snapshot = source.get("rule_snapshot") if isinstance(source, dict) else getattr(source, "rule_snapshot", None)
        return snapshot if isinstance(snapshot, dict) else {}

    def _set_prompt_context(self, category: str | None, prompt_profile: dict | None = None) -> None:
        self.prompt_profile_snapshot = prompt_profile if isinstance(prompt_profile, dict) else {}
        self.prompt_set = get_prompt_set(category, prompt_profile=prompt_profile)
        job_store.log(
            self.job_id,
            f"使用审核 Prompt：{self.prompt_set.category} · {self.prompt_set.prompt_version}",
        )

    def _active_libraries(self) -> list[dict]:
        profile = getattr(self, "prompt_profile_snapshot", {}) or {}
        libraries = profile.get("libraries") if isinstance(profile, dict) else None
        if isinstance(libraries, list) and libraries:
            return [dict(item) for item in libraries if isinstance(item, dict)]

        library_ids = []
        if isinstance(profile, dict):
            library_ids.extend(str(item or "").strip() for item in profile.get("library_ids") or [])
        for rule in self._active_scoring_rules():
            library_id = str(rule.get("library_id") or "").strip()
            if library_id:
                library_ids.append(library_id)
        category = getattr(getattr(self, "prompt_set", None), "category", "")
        if category:
            library_ids.append(str(category))

        out = []
        seen = set()
        for library_id in library_ids:
            if not library_id or library_id in seen:
                continue
            seen.add(library_id)
            package = get_default_knowledge_package(library_id)
            if package:
                out.append(package)
        if out:
            return out

        audit_goal = str(profile.get("audit_goal") or category or "内容风险") if isinstance(profile, dict) else "内容风险"
        return [{
            "id": str(category or "custom"),
            "title": audit_goal,
            "version": str(profile.get("prompt_version") or profile.get("version") or "") if isinstance(profile, dict) else "",
            "audit_goal": audit_goal,
            "output_labels": [audit_goal],
            "risk_definition": {"included": [audit_goal], "excluded": []},
            "risk_patterns": {},
            "modality_guidance": {
                "text": f"关注与{audit_goal}相关的明确风险意图和上下文。",
                "ocr": f"关注画面文字中与{audit_goal}相关的明确风险线索。",
                "asr": f"关注语音转写中与{audit_goal}相关的明确风险线索。",
                "vision": f"关注画面中与{audit_goal}相关的明确风险对象和行为。",
                "comment": f"关注评论中与{audit_goal}相关的明确风险表达。",
            },
            "keywords": {},
            "evidence_rules": [],
            "exemption_rules": [],
        }]

    def _is_ruleset_v2(self) -> bool:
        snapshot = getattr(self, "rule_snapshot", {}) or {}
        try:
            return int(snapshot.get("schema_version") or 0) == 2
        except (TypeError, ValueError):
            return False

    def _stage_rule_ids(self, stage: str) -> set[str]:
        if not self._is_ruleset_v2():
            return set()
        routes = self.rule_snapshot.get("stage_routes") or {}
        values = routes.get(stage) if isinstance(routes, dict) else []
        return {str(value) for value in (values or []) if str(value).strip()}

    def _normalize_stage_rule_id(self, value, stage: str) -> str:
        rule_id = str(value or "").strip()
        if not self._is_ruleset_v2():
            return rule_id
        return rule_id if rule_id in self._stage_rule_ids(stage) else ""

    def _filter_stage_risk_items(self, values, stage: str) -> list[dict]:
        items = [dict(item) for item in (values or []) if isinstance(item, dict)]
        if not self._is_ruleset_v2():
            return items
        output = []
        for item in items:
            rule_id = self._normalize_stage_rule_id(
                item.get("rule_id") or item.get("id"),
                stage,
            )
            if not rule_id:
                raise FusionAuditContractError(
                    f"{stage} non-none result has an invalid rule_id"
                )
            item["rule_id"] = rule_id
            level_field = "severity" if stage == "image_evidence" else "risk_level"
            item[level_field] = self._strict_v2_risk_level(
                item.get(level_field),
                field=f"{stage}.{level_field}",
                allow_none=False,
            )
            matched_exemption_ids = self._normalize_matched_exemption_ids(
                rule_id,
                item.get("matched_exemption_ids"),
            )
            if matched_exemption_ids:
                continue
            item.pop("matched_exemption_ids", None)
            output.append(item)
        return output

    def _matched_stage_exemption_ids(self, values, stage: str) -> list[str]:
        if not self._is_ruleset_v2():
            return []
        output: list[str] = []
        for item in values or []:
            if not isinstance(item, dict):
                continue
            rule_id = self._normalize_stage_rule_id(
                item.get("rule_id") or item.get("id"),
                stage,
            )
            if not rule_id:
                continue
            for exemption_id in self._normalize_matched_exemption_ids(
                rule_id,
                item.get("matched_exemption_ids"),
            ):
                if exemption_id not in output:
                    output.append(exemption_id)
        return output

    def _normalize_matched_exemption_ids(self, rule_id: str, values) -> list[str]:
        if not self._is_ruleset_v2():
            return []
        if isinstance(values, str):
            values = [values]
        allowed = {
            str(item.get("exemption_id") or "")
            for item in self.rule_snapshot.get("general_exemptions") or []
            if isinstance(item, dict) and item.get("exemption_id")
        }
        for rule in self.rule_snapshot.get("decision_rules") or []:
            if not isinstance(rule, dict) or str(rule.get("rule_id") or "") != rule_id:
                continue
            allowed.update(
                str(item.get("exemption_id") or "")
                for item in rule.get("rule_exemptions") or []
                if isinstance(item, dict) and item.get("exemption_id")
            )
            break
        output = []
        for value in values or []:
            exemption_id = str(value or "").strip()
            if exemption_id in allowed and exemption_id not in output:
                output.append(exemption_id)
        return output

    def _active_library_policies(self, modalities: list[str]) -> list[dict]:
        policies = [
            compact_library_policy(library, modalities=modalities)
            for library in self._active_libraries()
        ]
        return [policy for policy in policies if policy.get("id") or policy.get("title")]

    def _library_policy_by_id(self, library_id: str) -> dict:
        target = str(library_id or "").strip()
        for policy in self._active_library_policies(["text", "ocr", "asr", "vision", "comment"]):
            if str(policy.get("id") or "").strip() == target:
                return policy
        return {}

    @staticmethod
    def _analysis_media_scope() -> str:
        scope = str(settings.analysis_media_scope or "all").strip().lower().replace("-", "_")
        if scope in {"image", "images", "image_only", "image_text", "image_text_only", "photo", "photos"}:
            return "image_text"
        return "all"

    def _should_analyze_subject(self, subject: AuditSubject) -> bool:
        if self._analysis_media_scope() != "image_text":
            return True
        has_image = bool(subject.local_image_paths or subject.image_urls)
        has_video = bool(subject.local_video_paths or subject.video_urls)
        return has_image and not has_video

    def _filter_subjects_for_analysis(
        self,
        platform: str,
        subjects: list[AuditSubject],
        refs_by_key: dict[str, dict],
    ) -> list[AuditSubject]:
        if self._analysis_media_scope() != "image_text":
            return subjects
        kept: list[AuditSubject] = []
        skipped = 0
        for subject in subjects:
            if self._should_analyze_subject(subject):
                kept.append(subject)
                continue
            skipped += 1
            self._mark_subject_skipped(platform, self._subject_content_key(subject, refs_by_key), subject)
        if skipped:
            job_store.log(self.job_id, f"分析范围过滤：跳过非图文内容 {skipped} 条")
        return kept

    def _mark_subject_skipped(self, platform: str, content_key: str, subject: AuditSubject) -> None:
        if content_key:
            self.ingestion.mark_task_content_status(
                self.job_id,
                platform,
                content_key,
                "skipped",
            )
        job_store.log(
            self.job_id,
            f"跳过非图文内容：{subject.note_id or content_key}（视频只入库，不进入本轮审核）",
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

    @staticmethod
    def _select_distinct_contents(
        contents: list[dict], platform: str, limit: int
    ) -> list[dict]:
        selected: list[dict] = []
        identities: set[str] = set()
        for content in contents:
            identity = content_identity(content, platform)
            if not identity or identity in identities:
                continue
            identities.add(identity)
            selected.append(content)
            if len(selected) >= limit:
                break
        return selected

    def _refs_by_content_key(self, refs: list[dict]) -> dict[str, dict]:
        refs_by_key: dict[str, dict] = {}
        for ref in refs:
            item = ref.get("item") or {}
            platform = str(ref.get("platform") or "")
            keys = {
                str(ref.get("content_key") or ""),
                self._content_id(item, platform),
                self._content_url(item, platform),
                content_identity(item, platform),
            }
            for key in keys:
                if key:
                    refs_by_key[key] = ref
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
        *,
        include_media: bool = True,
    ) -> list[AuditSubject]:
        comments_by_note: dict[str, list[dict]] = {}
        for comment in comments:
            note_id = self._content_id(comment, platform)
            comments_by_note.setdefault(note_id, []).append(comment)
        creators_by_key = self._creator_lookup(creators or [])

        subjects = []
        for item in contents:
            note_id = self._content_id(item, platform)
            if include_media:
                local_video_paths = self._valid_local_video_paths(
                    note_id,
                    self._find_local_media(
                        media_root,
                        platform,
                        note_id,
                        "videos",
                        VIDEO_EXTENSIONS,
                    ),
                )
                local_image_paths = self._find_local_media(
                    media_root,
                    platform,
                    note_id,
                    "images",
                    IMAGE_EXTENSIONS,
                )
                image_urls = (
                    [] if local_video_paths else self._image_urls(item, platform)
                )
                video_urls = (
                    [] if local_video_paths else self._video_urls(item, platform)
                )
            else:
                local_video_paths = []
                local_image_paths = []
                image_urls = []
                video_urls = []
            subjects.append(
                AuditSubject(
                    platform=platform,
                    note_id=note_id,
                    url=self._content_url(item, platform),
                    title=str(item.get("title", "") or ""),
                    desc=str(item.get("desc", "") or ""),
                    author=self._author_info(item, platform, creators_by_key),
                    image_urls=image_urls,
                    video_urls=video_urls,
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
            if self._is_douyin_image_note(item):
                return []
            return self._filter_video_urls(split_csv_urls(item.get("video_download_url")))
        if platform == "ks":
            return self._filter_video_urls(split_csv_urls(item.get("video_play_url")))
        return self._filter_video_urls(split_csv_urls(item.get("video_url")))

    def _is_douyin_image_note(self, item: dict) -> bool:
        aweme_type = str(item.get("aweme_type") or "").strip()
        return aweme_type == "68" and bool(str(item.get("note_download_url") or "").strip())

    def _filter_video_urls(self, urls: list[str]) -> list[str]:
        return [url for url in urls if not self._looks_like_audio_url(url)]

    def _looks_like_audio_url(self, url: str) -> bool:
        clean = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
        return any(clean.endswith(ext) for ext in AUDIO_URL_EXTENSIONS)

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

    def _valid_local_video_paths(self, note_id: str, paths: list[str]) -> list[str]:
        valid = []
        for raw_path in paths:
            path = Path(raw_path)
            if self._is_probably_video_file(path):
                valid.append(str(path))
                continue
            job_store.log(self.job_id, f"笔记 {note_id}：跳过非视频媒体文件 {path.name}")
        return valid

    def _is_probably_video_file(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                header = handle.read(64)
        except OSError:
            return False
        if not header or header.startswith(AUDIO_FILE_SIGNATURES):
            return False
        if b"ftyp" in header[:16]:
            return True
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            return True
        if header.startswith(b"RIFF") and b"AVI " in header[:16]:
            return True
        return False

    def _analyze_subject(self, subject: AuditSubject) -> dict:
        from backend.task_admission.resources import analysis_capacity
        with analysis_capacity(self._capacity_stop_requested) if settings.app_auth_mode == "required" else nullcontext():
            return self._analyze_subject_with_capacity(subject)

    def _capacity_stop_requested(self):
        execution = getattr(self, "_admission_execution", None)
        if execution:
            from backend.task_admission.execution import assert_execution
            assert_execution(self.job_id, execution)
        control = job_store.control(self.job_id)
        return bool(control.get("stop_all_requested") or control.get("analysis_stop_requested") or control.get("analysis_paused"))

    def _analyze_subject_with_capacity(self, subject: AuditSubject) -> dict:
        authoritative_m3 = bool(getattr(self, "authoritative_m3", False))
        note_dir = settings.outputs_dir / self.job_id / "assets" / subject.note_id
        started_at = perf_counter()
        job_store.log(
            self.job_id,
            f"笔记 {subject.note_id}：准备分析，本地图片 {len(subject.local_image_paths)}，"
            f"本地视频 {len(subject.local_video_paths)}，远程图片 {len(subject.image_urls)}，远程视频 {len(subject.video_urls)}",
        )

        self._current_audit_stage = "post_translation"
        self._translate_subject_texts(subject)
        self._validate_authoritative_subject_configuration(subject)

        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始图片分析")
        self._current_audit_stage = "image_audit"
        image_analyses = self._analyze_images(subject, note_dir / "images")
        if authoritative_m3 and (
            subject.local_image_paths or subject.image_urls
        ) and not image_analyses:
            raise AuditProviderCallError(
                "visual audit produced no image result for selected content"
            )
        job_store.log(self.job_id, f"笔记 {subject.note_id}：图片分析完成，共 {len(image_analyses)} 张")

        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始视频分析")
        self._current_audit_stage = "video_audit"
        video_results = self._analyze_videos(subject, note_dir / "videos")
        if authoritative_m3 and (
            subject.local_video_paths or subject.video_urls
        ) and not video_results:
            raise AuditProviderCallError(
                "visual audit produced no video result for selected content"
            )
        job_store.log(self.job_id, f"笔记 {subject.note_id}：视频分析完成，共 {len(video_results)} 个")

        media_summary = self._media_summary_for_comment_audit(image_analyses, video_results)
        self._current_audit_stage = "comment_audit"
        audited_comments = self._audit_comments(subject, media_summary)
        evidence_index = self._build_evidence_index(subject, image_analyses, video_results, audited_comments)
        evidence_index_path = self._write_evidence_index(subject.note_id, evidence_index)
        prompt = self._render_compact_fusion_prompt(subject, evidence_index, audited_comments)
        self._current_audit_stage = "fusion_audit"
        audit = self._run_fusion_audit(
            subject.note_id,
            prompt,
            contract_validator=(
                lambda value: self._validate_v2_fusion_contract(
                    self._recover_truncated_fusion_audit(value),
                    evidence_index,
                    visible_evidence_ids=self._fusion_visible_ids(prompt),
                )
            ) if self._is_ruleset_v2() else None,
        )
        audit = self._recover_truncated_fusion_audit(audit)
        audit["content_title"] = self._ensure_content_title(audit, subject, evidence_index)
        elapsed = perf_counter() - started_at
        job_store.log(self.job_id, f"笔记 {subject.note_id}：全部分析完成，总耗时 {elapsed:.1f}s")

        job_root = settings.outputs_dir / self.job_id
        evidence_items = self._normalize_evidence_items(
            audit,
            subject,
            evidence_index,
            comments=audited_comments,
        )
        existing_evidence_ids = {str(item.get("evidence_id") or "") for item in evidence_items}
        if not self._is_ruleset_v2():
            evidence_items.extend(
                item
                for item in self._comment_evidence_items(audited_comments)
                if str(item.get("evidence_id") or "") not in existing_evidence_ids
            )
        evidence_index["final_evidence_refs"] = [
            str(item.get("evidence_id") or "")
            for item in evidence_items
            if item.get("evidence_id")
        ]
        self._write_evidence_index(subject.note_id, evidence_index)
        rule_matches = self._normalize_rule_matches(audit)
        if not rule_matches and evidence_items:
            rule_matches = self._infer_rule_matches_from_evidence_items(evidence_items)
        all_evidence_ids = {
            str(item.get("evidence_id") or "")
            for item in evidence_items
            if item.get("evidence_id")
        }
        main_evidence_ids = {
            str(item.get("evidence_id") or "")
            for item in evidence_items
            if item.get("primary_modality") != "comment"
        }
        rule_matches = self._rule_matches_for_evidence_ids(rule_matches, all_evidence_ids)
        scoring = (
            {
                "score_breakdown": [],
                "category_scores": [],
                "risk_score": 0,
                "risk_level": "none",
                "decision": "pass",
            }
            if self._is_ruleset_v2()
            else self._score_rule_matches(rule_matches)
        )
        evidence_risk = self._risk_from_evidence_items(evidence_items)
        main_evidence_risk = self._risk_from_evidence_items([
            item for item in evidence_items if item.get("primary_modality") != "comment"
        ])
        comment_evidence_risk = self._risk_from_evidence_items([
            item for item in evidence_items if item.get("primary_modality") == "comment"
        ])
        suggested_decision = str(audit.get("decision_suggestion") or audit.get("decision") or "review")
        suggested_risk_level = str(audit.get("risk_level_suggestion") or audit.get("risk_level") or "unknown")
        primary_risk = str(audit.get("primary_risk") or "")
        suggested_categories = audit.get("categories") if isinstance(audit.get("categories"), list) else []
        category_labels = suggested_categories or self._category_labels_from_evidence_items(evidence_items) or ([primary_risk] if primary_risk else [])
        if self._is_ruleset_v2():
            risk_level = evidence_risk["risk_level"]
            risk_score = self._risk_score_for_level(risk_level)
            comment_dominant = (
                risk_level != "none"
                and self._risk_level_rank(comment_evidence_risk["risk_level"])
                > self._risk_level_rank(main_evidence_risk["risk_level"])
            )
            decision = (
                "review"
                if comment_dominant
                else self._decision_for_level(risk_level)
            )
            risk_basis = (
                "comment_evidence"
                if comment_dominant and not main_evidence_ids
                else "comment_evidence_dominant"
                if comment_dominant
                else "max_evidence_risk"
                if risk_level != "none"
                else "validated_no_risk"
            )
            category_scores = self._normalize_category_scores(
                [],
                category_labels,
                risk_score,
                risk_level,
            )
            score_breakdown = []
            if risk_level == "none":
                primary_risk = ""
                suggested_categories = []
                category_labels = []
                category_scores = []
        elif evidence_risk["risk_level"] != "none":
            risk_score = evidence_risk["risk_score"]
            risk_level = evidence_risk["risk_level"]
            comment_dominant = (
                self._risk_level_rank(comment_evidence_risk["risk_level"])
                > self._risk_level_rank(main_evidence_risk["risk_level"])
            )
            decision = "review" if comment_dominant else evidence_risk["decision"]
            risk_basis = "comment_evidence" if comment_dominant and not main_evidence_ids else (
                "comment_evidence_dominant" if comment_dominant else "max_evidence_risk"
            )
            category_scores = self._normalize_category_scores(
                [],
                category_labels,
                risk_score,
                risk_level,
            )
            score_breakdown = scoring["score_breakdown"]
        elif scoring["score_breakdown"]:
            risk_score = scoring["risk_score"]
            risk_level = scoring["risk_level"]
            decision = scoring["decision"]
            risk_basis = "rule_score"
            category_scores = scoring["category_scores"]
            score_breakdown = scoring["score_breakdown"]
        elif evidence_items and not main_evidence_ids:
            decision = "review"
            risk_level = comment_evidence_risk["risk_level"]
            risk_basis = "comment_evidence"
            risk_score = comment_evidence_risk["risk_score"]
            category_scores = self._normalize_category_scores(
                [],
                category_labels,
                risk_score,
                risk_level,
            )
            score_breakdown = []
        else:
            decision = suggested_decision
            risk_level = suggested_risk_level
            risk_basis = "model_suggestion"
            risk_score = self._normalize_risk_score(audit.get("risk_score"), risk_level)
            category_scores = self._normalize_category_scores(
                audit.get("category_scores"),
                suggested_categories,
                risk_score,
                risk_level,
            )
            score_breakdown = audit.get("score_breakdown") if isinstance(audit.get("score_breakdown"), list) else []
        risk_evidence = self._normalize_fusion_evidence(audit, evidence_items=evidence_items)
        if not primary_risk and category_scores:
            primary_risk = str(category_scores[0].get("category") or "")
        categories = suggested_categories
        if not categories and category_scores:
            categories = [str(item.get("category") or "") for item in category_scores if item.get("category")]
        risk_frames = self._collect_risk_frames(
            video_results,
            job_root,
            decision,
            risk_level,
            risk_evidence=risk_evidence,
            audit_summary=audit.get("summary", ""),
        )
        risk_images = (
            []
            if self._is_ruleset_v2() and risk_level == "none"
            else self._collect_risk_images(image_analyses, job_root)
        )
        has_risk = bool(risk_evidence) or bool(risk_frames) or bool(risk_images) or decision in ("review", "reject")

        result = {
            "note_id": subject.note_id,
            "url": subject.url,
            "title": subject.title,
            "title_zh": subject.title_zh,
            "desc": subject.desc,
            "desc_zh": subject.desc_zh,
            "author": subject.author,
            "prompt_category": self.prompt_set.category,
            "prompt_version": self.prompt_set.prompt_version,
            "content_title": audit.get("content_title", ""),
            "summary": audit.get("summary", ""),
            "decision": decision,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "primary_risk": primary_risk,
            "categories": categories,
            "category_scores": category_scores,
            "score_breakdown": score_breakdown,
            "risk_basis": risk_basis,
            "evidence_items": evidence_items,
            "rule_matches": rule_matches,
            "evidence": [] if self._is_ruleset_v2() else audit.get("evidence", []),
            "risk_evidence": risk_evidence,
            "risk_frames": risk_frames,
            "risk_images": risk_images,
            "has_risk": has_risk,
            "evidence_index": evidence_index,
            "evidence_index_path": str(evidence_index_path),
            "evidence_index_rel": self._to_job_rel(str(evidence_index_path), job_root),
            "image_analyses": image_analyses,
            "video_results": video_results,
            "comments": audited_comments,
            "comments_count": len(audited_comments),
            "comment_audit_stats": self._comment_audit_stats(audited_comments),
            "raw_audit": audit,
        }
        if getattr(self, "authoritative_m3", False):
            provenance = dict(audit.get("_provider_provenance") or {})
            completed_modalities = ["text"]
            vision_models: list[str] = []
            if image_analyses:
                completed_modalities.append("vision")
                vision_models.append(str(settings.qwen_image_audit_model or ""))
            if video_results:
                if "vision" not in completed_modalities:
                    completed_modalities.append("vision")
                vision_models.append(str(settings.qwen_contact_sheet_model or ""))
                if any(
                    str((item.get("transcript") or {}).get("completion_status") or "")
                    in {"completed", "no_speech"}
                    for item in video_results
                ):
                    completed_modalities.append("asr")
                elif any(
                    str((item.get("transcript") or {}).get("completion_status") or "")
                    == "no_audio_track"
                    for item in video_results
                ):
                    completed_modalities.append("no_audio_track")
            provenance["completed_modalities"] = completed_modalities
            provenance["vision_models"] = sorted(
                {model for model in vision_models if model}
            )
            result["model_provenance"] = provenance
        return result

    def _validate_authoritative_subject_configuration(
        self, subject: AuditSubject
    ) -> None:
        if not getattr(self, "authoritative_m3", False):
            return
        required_models: dict[str, str] = {}
        if subject.local_image_paths or subject.image_urls:
            required_models["QWEN_IMAGE_AUDIT_MODEL"] = (
                settings.qwen_image_audit_model
            )
        if subject.local_video_paths or subject.video_urls:
            required_models["QWEN_CONTACT_SHEET_MODEL"] = (
                settings.qwen_contact_sheet_model
            )
        if not required_models:
            return
        try:
            QwenClient.validate_authoritative_vision_configuration(
                required_models=required_models
            )
        except Exception as exc:
            raise AuditProviderUnavailableError(str(exc)) from exc

    @staticmethod
    def _validate_authoritative_asr_configuration() -> None:
        errors: list[str] = []
        if settings.use_remote_asr:
            if not settings.remote_asr_base_url:
                errors.append("REMOTE_ASR_BASE_URL")
        elif settings.asr_engine == "dolphin":
            if not str(settings.dolphin_model or "").strip():
                errors.append("DOLPHIN_MODEL")
        elif settings.asr_engine == "whisper":
            if not str(settings.whisper_model or "").strip():
                errors.append("WHISPER_MODEL")
        else:
            errors.append("ASR_ENGINE")
        if errors:
            raise AuditProviderUnavailableError(
                "authoritative ASR Provider configuration is incomplete: "
                + ", ".join(sorted(set(errors)))
            )

    @staticmethod
    def _validated_authoritative_transcript(value: object) -> dict:
        if not isinstance(value, dict):
            raise AuditProviderCallError("ASR Provider response is not an object")
        if str(value.get("error") or "").strip():
            raise AuditProviderCallError(
                f"ASR Provider returned an error: {value.get('error')}"
            )
        text = value.get("text")
        segments = value.get("segments")
        if not isinstance(text, str) or not isinstance(segments, list):
            raise AuditProviderCallError("ASR Provider response has invalid text/segments")
        if any(not isinstance(segment, dict) for segment in segments):
            raise AuditProviderCallError("ASR Provider response has invalid segments")
        provider = str(value.get("asr_engine") or value.get("provider") or "").strip()
        if not provider:
            raise AuditProviderCallError("ASR Provider response has no provider identity")
        transcript = dict(value)
        transcript["completion_status"] = (
            "completed" if text.strip() or segments else "no_speech"
        )
        return transcript

    @staticmethod
    def _validated_authoritative_visual_response(
        value: object, *, response_contract: str
    ) -> dict:
        if not isinstance(value, dict) or not value:
            raise AuditProviderCallError(
                "visual Provider response is empty or not an object"
            )
        if str(value.get("error") or "").strip():
            raise AuditProviderCallError(
                f"visual Provider returned an error: {value.get('error')}"
            )

        def require_string(container: dict, field: str) -> None:
            if field not in container or not isinstance(container[field], str):
                raise AuditProviderCallError(
                    f"visual Provider response has invalid {field}"
                )

        def require_string_list(container: dict, field: str) -> None:
            values = container.get(field)
            if not isinstance(values, list) or any(
                not isinstance(item, str) for item in values
            ):
                raise AuditProviderCallError(
                    f"visual Provider response has invalid {field}"
                )

        def validate_optional_common_fields(
            container: dict, *, allow_none_risk_level: bool = False
        ) -> None:
            for field in ("rule_id", "id"):
                if field in container and not isinstance(container[field], str):
                    raise AuditProviderCallError(
                        f"visual Provider response has invalid {field}"
                    )
            if "matched_exemption_ids" in container:
                require_string_list(container, "matched_exemption_ids")
            if "risk_level" in container:
                allowed_risk_levels = {"low", "medium", "high"}
                if allow_none_risk_level:
                    allowed_risk_levels.add("none")
                if container["risk_level"] not in allowed_risk_levels:
                    raise AuditProviderCallError(
                        "visual Provider response has invalid risk_level"
                    )

        def require_score(container: dict, field: str) -> None:
            score = container.get(field)
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0 <= score <= 100
            ):
                raise AuditProviderCallError(
                    f"visual Provider response has invalid {field}"
                )

        if response_contract == "image":
            require_string(value, "visual_summary")
            for optional_string in ("ocr_text", "benign_context", "safe_context"):
                if optional_string in value and not isinstance(
                    value[optional_string], str
                ):
                    raise AuditProviderCallError(
                        f"visual Provider response has invalid {optional_string}"
                    )
            risk_items = value.get("risk_items")
            if not isinstance(risk_items, list):
                raise AuditProviderCallError(
                    "visual Provider response has invalid risk_items"
                )
            for item in risk_items:
                if not isinstance(item, dict):
                    raise AuditProviderCallError(
                        "visual Provider response has a non-object risk item"
                    )
                for field in ("risk_type", "evidence", "reason", "severity"):
                    require_string(item, field)
                if item["severity"] not in {"low", "medium", "high"}:
                    raise AuditProviderCallError(
                        "visual Provider response has invalid severity"
                    )
                validate_optional_common_fields(item)
            return dict(value)

        if response_contract == "video_segment":
            require_string(value, "segment_summary")
            require_score(value, "segment_score")
            for field in ("risk_library_id", "risk_library_label"):
                require_string(value, field)
            if "segment_level" in value and value["segment_level"] not in {
                "none",
                "low",
                "medium",
                "high",
            }:
                raise AuditProviderCallError(
                    "visual Provider response has invalid segment_level"
                )
            for risk_field in ("visual_risks", "ocr_risks", "asr_risks"):
                risk_items = value.get(risk_field)
                if not isinstance(risk_items, list):
                    raise AuditProviderCallError(
                        f"visual Provider response has invalid {risk_field}"
                    )
                for item in risk_items:
                    if not isinstance(item, dict):
                        raise AuditProviderCallError(
                            f"visual Provider response has a non-object {risk_field} item"
                        )
                    for field in ("risk_type", "reason"):
                        require_string(item, field)
                    require_score(item, "score")
                    if risk_field in {"visual_risks", "ocr_risks"}:
                        require_string_list(item, "frame_ids")
                    if risk_field == "ocr_risks":
                        require_string(item, "ocr_chunk_id")
                    elif risk_field == "asr_risks":
                        require_string(item, "asr_chunk_id")
                    validate_optional_common_fields(
                        item, allow_none_risk_level=True
                    )
            return dict(value)

        raise AuditProviderCallError(
            f"unknown authoritative visual response contract: {response_contract}"
        )

    def _translate_subject_texts(self, subject: AuditSubject) -> None:
        translator = getattr(self, "translator", None)
        qwen = getattr(self, "qwen", None)
        if translator is None or qwen is None:
            return
        source_fields: dict[str, str] = {}
        for source_field, target_field, label in (
            ("title", "title_zh", "标题"),
            ("desc", "desc_zh", "正文"),
        ):
            source_text = str(getattr(subject, source_field, "") or "").strip()
            if not source_text:
                setattr(subject, target_field, "")
                continue
            try:
                should_translate = translator.should_translate(
                    source_text,
                    trust_language_label=False,
                )
            except TypeError:
                should_translate = translator.should_translate(source_text)
            if not should_translate:
                setattr(subject, target_field, "")
                continue
            source_fields[source_field] = source_text

        if not source_fields:
            return
        prompt = (
            "你是社交平台文本翻译器。将输入中的外文标题和正文分别翻译成通顺、忠实的简体中文。"
            "不要审核、总结、改写或解释；原文相同的字段译文也必须分别填写。"
            "只输出合法JSON，字段固定为 title_zh 和 desc_zh；没有对应输入时输出空字符串。\n"
            "输入JSON：\n"
            + json.dumps(source_fields, ensure_ascii=False, separators=(",", ":"))
        )
        try:
            translated = qwen.audit_text(prompt, max_tokens=512, enable_thinking=False)
        except Exception as exc:
            job_store.log(self.job_id, f"笔记 {subject.note_id}：标题/正文翻译失败：{exc}")
            return

        translated_by_source: dict[str, str] = {}
        for source_field, target_field, label in (
            ("title", "title_zh", "标题"),
            ("desc", "desc_zh", "正文"),
        ):
            source_text = source_fields.get(source_field, "")
            if not source_text:
                continue
            translated_text = str(translated.get(target_field) or "").strip()
            if not translated_text and source_text in translated_by_source:
                translated_text = translated_by_source[source_text]
            if translated_text:
                translated_by_source[source_text] = translated_text
            setattr(subject, target_field, translated_text)
            if translated_text:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：{label}翻译完成，译文长度={len(translated_text)}")
            else:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：{label}翻译漏项")

    def _ensure_content_title(self, audit: dict, subject: AuditSubject, evidence_index: dict) -> str:
        current = self._normalize_content_title(audit.get("content_title"), subject.note_id)
        if current:
            return current

        job_store.log(self.job_id, f"笔记 {subject.note_id}：融合结果缺少短标题，补充生成一次")
        media_summaries = [
            str(item.get("visual_summary") or item.get("benign_context") or "").strip()
            for item in evidence_index.get("image_units") or []
            if item.get("visual_summary") or item.get("benign_context")
        ]
        media_summaries.extend(
            str(item.get("segment_summary") or "").strip()
            for item in evidence_index.get("segment_reviews") or []
            if item.get("segment_summary")
        )
        payload = {
            "title": self._truncate_text(subject.title, 240),
            "title_zh": self._truncate_text(subject.title_zh, 240),
            "desc": self._truncate_text(subject.desc, 500),
            "desc_zh": self._truncate_text(subject.desc_zh, 500),
            "summary": self._truncate_text(audit.get("summary", ""), 240),
            "media_summaries": [self._truncate_text(value, 120) for value in media_summaries[:6]],
        }
        prompt = (
            "请根据输入内容生成一个8至18字的中文短标题，概括内容主题。"
            "标题不能为空，不写审核结论、风险等级、平台名或内容ID。"
            "只输出合法JSON：{\"content_title\":\"短标题\"}\n"
            "输入JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        try:
            generated = self.qwen.audit_text(prompt, max_tokens=64, enable_thinking=False)
            title = self._normalize_content_title(
                generated.get("content_title") or generated.get("title"),
                subject.note_id,
            )
            if title:
                job_store.log(self.job_id, f"笔记 {subject.note_id}：短标题补充生成完成：{title}")
                return title
            job_store.log(self.job_id, f"笔记 {subject.note_id}：短标题补充生成漏项，使用本地兜底")
        except Exception as exc:
            job_store.log(self.job_id, f"笔记 {subject.note_id}：短标题补充生成失败，使用本地兜底：{exc}")
        return self._fallback_content_title(audit, subject)

    @staticmethod
    def _normalize_content_title(value, note_id: str = "") -> str:
        text = " ".join(str(value or "").strip().strip('"“”').split())
        if not text or text == str(note_id or "").strip() or text.isdigit():
            return ""
        return text[:18]

    def _fallback_content_title(self, audit: dict, subject: AuditSubject) -> str:
        candidates = (
            audit.get("summary"),
            subject.title_zh,
            subject.desc_zh,
            subject.title,
            subject.desc,
        )
        for value in candidates:
            text = " ".join(str(value or "").strip().split())
            if not text:
                continue
            text = text.split("。", 1)[0].split("；", 1)[0].split("，", 1)[0].strip()
            for prefix in ("视频内容为", "视频为", "视频是", "内容为", "内容是", "帖子为", "帖子是"):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
                    break
            title = self._normalize_content_title(text, subject.note_id)
            if title:
                return title
        return "社交平台内容"

    def _to_job_rel(self, path_str: str | None, job_root: Path) -> str | None:
        if not path_str:
            return None
        try:
            return Path(path_str).resolve().relative_to(job_root.resolve()).as_posix()
        except (ValueError, OSError):
            return None

    def _normalize_fusion_evidence(self, audit: dict, evidence_items: list[dict] | None = None) -> list[dict]:
        out: list[dict] = []
        if evidence_items:
            for ev in evidence_items:
                if self._normalize_evidence_risk_level(
                    ev.get("evidence_risk_level") or ev.get("risk_level") or ev.get("severity")
                ) == "none":
                    continue
                source = str(ev.get("source") or "")
                modality = str(ev.get("primary_modality") or ev.get("modality") or "")
                if modality == "comment" or source.startswith("comment"):
                    kind = "comment"
                elif modality == "asr" or source.startswith("video_audio"):
                    kind = "audio"
                elif modality == "ocr":
                    kind = "ocr"
                elif modality == "vision" and (source.startswith("video_frame") or source.startswith("video:")):
                    kind = "frame_ref"
                elif modality == "vision" or source.startswith("image"):
                    kind = "image_ref"
                elif source.startswith("video_frame") or source.startswith("video:"):
                    kind = "frame_ref"
                else:
                    kind = "text"
                out.append({
                    "kind": kind,
                    "source": source,
                    "severity": ev.get("severity", ""),
                    "text": ev.get("text") or ev.get("ocr_text") or ev.get("transcript_text") or "",
                    "reason": ev.get("reason", ""),
                    "risk_library_id": ev.get("risk_library_id", ""),
                    "risk_library_label": ev.get("risk_library_label", ""),
                    "start": ev.get("start"),
                    "end": ev.get("end"),
                    "comment_id": ev.get("comment_id", ""),
                    "nickname": ev.get("nickname", ""),
                })
            return out
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

    def _validate_v2_fusion_contract(
        self, audit: dict, evidence_index: dict, *, visible_evidence_ids: set[str] | None = None,
    ) -> dict:
        if not isinstance(audit, dict):
            raise FusionAuditContractError("fusion response is not a JSON object")
        required_fields = {
            "schema_version",
            "content_title",
            "summary",
            "decision_suggestion",
            "risk_level_suggestion",
            "primary_risk",
            "categories",
            "evidence_items",
            "rule_matches",
        }
        missing_fields = sorted(required_fields - set(audit))
        if missing_fields:
            raise FusionAuditContractError(
                "fusion response is missing fields: " + ", ".join(missing_fields)
            )
        if audit.get("schema_version") != "audit_fusion_v4":
            raise FusionAuditContractError("fusion schema_version must be audit_fusion_v4")
        if not isinstance(audit.get("categories"), list):
            raise FusionAuditContractError("fusion categories must be an array")
        if not isinstance(audit.get("evidence_items"), list):
            raise FusionAuditContractError("fusion evidence_items must be an array")
        if not isinstance(audit.get("rule_matches"), list):
            raise FusionAuditContractError("fusion rule_matches must be an array")
        catalog_ids = {
            str(item.get("evidence_id") or "")
            for item in evidence_index.get("evidence_catalog") or []
            if isinstance(item, dict) and item.get("evidence_id")
        }
        selectable_ids = catalog_ids if visible_evidence_ids is None else catalog_ids & visible_evidence_ids
        comment_decisions = {
            str(item["evidence_id"]): item
            for item in evidence_index.get("evidence_catalog") or []
            if isinstance(item, dict) and str(item.get("evidence_id", "")).startswith("comment:")
            and item.get("rule_id") and item.get("evidence_risk_level") in {"low", "medium", "high"}
        }
        # Completed comment judgments are authoritative input to fusion. Treat
        # any model representation of them as an ID-only selection, ignore
        # returned fields/rule matches, and restore the saved judgment below.
        # Non-comment evidence remains subject to the strict fusion contract.
        audit = {**audit, "evidence_items": [dict(x) if isinstance(x, dict) else x for x in audit["evidence_items"]],
                 "rule_matches": [dict(x) if isinstance(x, dict) else x for x in audit["rule_matches"]]}
        contract_warnings: list[dict] = []
        non_comment_evidence: list[object] = []
        for item in audit["evidence_items"]:
            if not isinstance(item, dict):
                non_comment_evidence.append(item)
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            original = comment_decisions.get(evidence_id)
            if original is None:
                non_comment_evidence.append(item)
                continue
            if evidence_id not in selectable_ids:
                # Keep the normal invalid-reference path for model-selected
                # comments that were outside this prompt's visible boundary.
                non_comment_evidence.append(item)
                continue
            returned_fields = sorted(set(item) - {"evidence_id"})
            field_sources = {
                "rule_id": "rule_id",
                "evidence_risk_level": "evidence_risk_level",
                "reason": "reason",
                "risk_score": "risk_score",
                "risk_level": "evidence_risk_level",
                "risk_basis": "reason",
            }
            mutated_fields = sorted(
                key
                for key, source_key in field_sources.items()
                if key in item and item[key] != original.get(source_key, "")
            )
            if item.get("matched_exemption_ids"):
                mutated_fields.append("matched_exemption_ids")
            if returned_fields:
                contract_warnings.append({
                    "code": "FUSION_COMPLETED_COMMENT_FIELDS_IGNORED",
                    "evidence_id": evidence_id,
                    "source": "evidence_item",
                    "fields": returned_fields,
                    "mutated_fields": sorted(set(mutated_fields)),
                })
        audit["evidence_items"] = non_comment_evidence
        allowed_rule_ids = self._stage_rule_ids("fusion_audit")
        legal_matches: list[dict] = []
        exempted_evidence_ids: set[str] = set()
        applied_exemption_ids: list[str] = []
        for raw_match in audit.get("rule_matches") or []:
            if not isinstance(raw_match, dict):
                raise FusionAuditContractError("fusion rule_match is not an object")
            raw_evidence_ids = raw_match.get("evidence_ids")
            if not isinstance(raw_evidence_ids, list):
                raise FusionAuditContractError("fusion evidence_ids must be an array")
            rule_id = str(raw_match.get("rule_id") or "").strip()
            evidence_ids = []
            ignored_comment_ids = False
            for value in raw_evidence_ids:
                evidence_id = str(value or "").strip()
                if evidence_id not in selectable_ids:
                    raise FusionAuditContractError(
                        "fusion rule_match references an invalid evidence_id",
                        invalid_evidence_ids=[evidence_id],
                    )
                original = comment_decisions.get(evidence_id)
                if original is not None:
                    ignored_comment_ids = True
                    mutated_fields = []
                    if rule_id != original["rule_id"]:
                        mutated_fields.append("rule_id")
                    if raw_match.get("matched_exemption_ids"):
                        mutated_fields.append("matched_exemption_ids")
                    contract_warnings.append({
                        "code": "FUSION_COMPLETED_COMMENT_RULE_MATCH_IGNORED",
                        "evidence_id": evidence_id,
                        "source": "rule_match",
                        "fields": sorted(set(raw_match) - {"evidence_ids"}),
                        "mutated_fields": mutated_fields,
                    })
                    continue
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
            if not evidence_ids:
                if ignored_comment_ids:
                    continue
                raise FusionAuditContractError("fusion rule_match has no evidence_ids")
            if rule_id not in allowed_rule_ids:
                raise FusionAuditContractError("fusion rule_match has an invalid rule_id")
            matched_exemption_ids = self._normalize_matched_exemption_ids(
                rule_id,
                raw_match.get("matched_exemption_ids"),
            )
            if matched_exemption_ids:
                exempted_evidence_ids.update(evidence_ids)
                for exemption_id in matched_exemption_ids:
                    if exemption_id not in applied_exemption_ids:
                        applied_exemption_ids.append(exemption_id)
                continue
            legal_matches.append({
                **raw_match,
                "rule_id": rule_id,
                "evidence_ids": evidence_ids,
            })

        match_by_evidence_id: dict[str, dict] = {}
        for match in legal_matches:
            for evidence_id in match["evidence_ids"]:
                match_by_evidence_id.setdefault(evidence_id, match)

        legal_evidence: list[dict] = []
        for raw_item in audit.get("evidence_items") or []:
            if not isinstance(raw_item, dict):
                raise FusionAuditContractError("fusion evidence_item is not an object")
            evidence_id = str(raw_item.get("evidence_id") or "").strip()
            if evidence_id not in selectable_ids:
                raise FusionAuditContractError(
                    "fusion evidence_item references an invalid evidence_id",
                    invalid_evidence_ids=[evidence_id],
                )
            if evidence_id in exempted_evidence_ids:
                continue
            match = match_by_evidence_id.get(evidence_id)
            if match is None:
                raise FusionAuditContractError(
                    "fusion evidence_item has no valid rule/evidence closure"
                )
            risk_level = self._strict_v2_risk_level(
                raw_item.get("evidence_risk_level"),
                field="fusion.evidence_risk_level",
                allow_none=False,
            )
            legal_evidence.append({
                **raw_item,
                "evidence_id": evidence_id,
                "rule_id": match["rule_id"],
                "evidence_risk_level": risk_level,
            })

        # Representative selection never deletes saved comment judgments. These
        # additions come from the full stored catalog, not from model references.
        selected_ids = {item["evidence_id"] for item in legal_evidence}
        for evidence_id, original in comment_decisions.items():
            if evidence_id not in selected_ids:
                if original["rule_id"] not in allowed_rule_ids:
                    raise FusionAuditContractError("completed comment has no valid fusion rule")
                legal_evidence.append({key: original.get(key, "") for key in (
                    "evidence_id", "rule_id", "evidence_risk_level", "reason",
                )})
                legal_matches.append({"rule_id": original["rule_id"], "evidence_ids": [evidence_id]})
        retained_ids = {item["evidence_id"] for item in legal_evidence}
        legal_matches = [
            {**match, "evidence_ids": [value for value in match["evidence_ids"] if value in retained_ids]}
            for match in legal_matches
        ]
        legal_matches = [match for match in legal_matches if match["evidence_ids"]]
        decision = str(audit.get("decision_suggestion") or "").strip().lower()
        risk_level = str(audit.get("risk_level_suggestion") or "").strip().lower()
        if decision not in {"pass", "review", "reject"} or risk_level not in {
            "none",
            "low",
            "medium",
            "high",
        }:
            raise FusionAuditContractError("fusion decision/risk enum is invalid")
        declares_pass = decision == "pass" and risk_level == "none"
        declares_risk = decision in {"review", "reject"} and risk_level in {
            "low",
            "medium",
            "high",
        }
        if not declares_pass and not declares_risk:
            raise FusionAuditContractError("fusion decision and risk level are inconsistent")
        if declares_pass and legal_evidence:
            raise FusionAuditContractError("pass/none retained risk evidence")
        if comment_decisions and self._risk_level_rank(risk_level) < max(
            self._risk_level_rank(item["evidence_risk_level"]) for item in comment_decisions.values()
        ):
            raise FusionAuditContractError("fusion cannot understate completed comment risk")
        if declares_risk and not legal_evidence:
            if not applied_exemption_ids:
                raise FusionAuditContractError(
                    "non-pass fusion result has no valid rule/evidence closure"
                )
            decision = "pass"
            risk_level = "none"

        cleaned = {
            **audit,
            "decision_suggestion": decision,
            "risk_level_suggestion": risk_level,
            "evidence_items": legal_evidence,
            "rule_matches": legal_matches,
        }
        if applied_exemption_ids:
            cleaned["matched_exemption_ids"] = applied_exemption_ids
        if contract_warnings:
            cleaned["_fusion_contract_warnings"] = contract_warnings
        for key in ("evidence", "score_breakdown", "category_scores"):
            cleaned.pop(key, None)
        if decision == "pass":
            cleaned["primary_risk"] = ""
            cleaned["categories"] = []
            cleaned["risk_score"] = 0
        return cleaned

    def _normalize_evidence_items(
        self,
        audit: dict,
        subject: AuditSubject,
        evidence_index: dict,
        *,
        comments: list[dict] | None = None,
    ) -> list[dict]:
        raw_items = audit.get("evidence_items") if isinstance(audit.get("evidence_items"), list) else []
        if raw_items:
            items = [item for item in raw_items if isinstance(item, dict)]
        else:
            items = self._legacy_evidence_items(audit)
        comments_by_id = {
            str(comment.get("comment_id") or comment.get("id") or ""): comment
            for comment in (comments if comments is not None else subject.comments)
            if comment.get("comment_id") or comment.get("id")
        }
        catalog_by_id = {
            str(item.get("evidence_id") or ""): item
            for item in evidence_index.get("evidence_catalog") or []
            if item.get("evidence_id")
        }
        normalized: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for index, item in enumerate(items, start=1):
            initial_modality = self._primary_modality_for_item(item)
            evidence_id = str(item.get("evidence_id") or item.get("id") or f"ev_{initial_modality}_{index:03d}")
            catalog_item = catalog_by_id.get(evidence_id)
            if catalog_by_id and not catalog_item:
                continue
            enriched = {**(catalog_item or {}), **item}
            source = str(enriched.get("source") or "").strip()
            primary_modality = self._primary_modality_for_item(enriched)
            enriched["evidence_id"] = evidence_id
            enriched["id"] = evidence_id
            enriched["primary_modality"] = primary_modality
            enriched["modality"] = primary_modality
            enriched["source"] = source or self._default_source_for_modality(primary_modality)
            if self._is_ruleset_v2():
                rule_id = self._normalize_stage_rule_id(
                    enriched.get("rule_id"),
                    "fusion_audit",
                )
                if rule_id:
                    enriched["rule_id"] = rule_id
                else:
                    continue
                matched_exemption_ids = self._normalize_matched_exemption_ids(
                    rule_id,
                    enriched.get("matched_exemption_ids"),
                )
                if matched_exemption_ids:
                    continue
                enriched.pop("matched_exemption_ids", None)
            self._fill_original_evidence(enriched, subject, comments_by_id, evidence_index)
            enriched["evidence_risk_level"] = self._normalize_evidence_risk_level(
                enriched.get("evidence_risk_level")
                or enriched.get("risk_level")
                or enriched.get("severity")
            )
            if enriched["evidence_risk_level"] == "none":
                continue
            signature = (
                primary_modality,
                str(enriched.get("source") or ""),
                self._compact_signature(enriched.get("text") or enriched.get("ocr_text") or enriched.get("visual_context") or ""),
            )
            if signature in seen:
                continue
            seen.add(signature)
            normalized.append(enriched)
        return normalized

    def _risk_from_evidence_items(self, evidence_items: list[dict]) -> dict:
        max_level = "none"
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            level = self._normalize_evidence_risk_level(
                item.get("evidence_risk_level")
                or item.get("risk_level")
                or item.get("severity")
            )
            if self._risk_level_rank(level) > self._risk_level_rank(max_level):
                max_level = level
        return {
            "risk_level": max_level,
            "risk_score": self._risk_score_for_level(max_level),
            "decision": self._decision_for_level(max_level),
        }

    def _category_labels_from_evidence_items(self, evidence_items: list[dict]) -> list[str]:
        labels = []
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            label = str(
                item.get("risk_library_label")
                or item.get("risk_type")
                or item.get("risk_library_id")
                or ""
            ).strip()
            if label and label not in labels:
                labels.append(label)
        return labels

    @staticmethod
    def _normalize_evidence_risk_level(value) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "high": "high",
            "高危": "high",
            "medium": "medium",
            "中危": "medium",
            "mid": "medium",
            "low": "low",
            "低危": "low",
            "待复核": "low",
            "review": "low",
            "none": "none",
            "pass": "none",
            "无风险": "none",
            "未命中": "none",
        }
        return aliases.get(text, "none")

    @staticmethod
    def _strict_v2_risk_level(
        value,
        *,
        field: str,
        allow_none: bool,
    ) -> str:
        level = str(value or "").strip().lower()
        allowed = {"low", "medium", "high"}
        if allow_none:
            allowed.add("none")
        if level not in allowed:
            raise FusionAuditContractError(f"{field} is missing or invalid")
        return level

    @staticmethod
    def _risk_level_rank(level: str) -> int:
        return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(str(level or ""), 0)

    @staticmethod
    def _risk_score_for_level(level: str) -> int:
        return {"high": 80, "medium": 60, "low": 40, "none": 0}.get(str(level or ""), 0)

    @staticmethod
    def _decision_for_level(level: str) -> str:
        if level == "high":
            return "reject"
        if level in {"medium", "low"}:
            return "review"
        return "pass"

    def _text_inference_options(self, stage: str) -> dict:
        """Use the verified task snapshot; historical jobs retain their defaults."""
        if stage == "comment_audit":
            options = {"max_tokens": settings.comment_audit_max_tokens,
                       "model": settings.qwen_text_model, "enable_thinking": False}
        elif stage == "fusion_audit":
            options = {"max_tokens": settings.fusion_max_tokens, "enable_thinking": False,
                       "request_timeout": max(1, settings.fusion_request_timeout)}
        else:
            raise ValueError("unsupported text inference stage")
        profile = getattr(self, "prompt_profile_snapshot", {}) or {}
        overrides = profile.get("inference_settings", {}).get(stage, {})
        for key in ("model", "max_tokens", "enable_thinking", "request_timeout"):
            if key in overrides:
                options[key] = overrides[key]
        return options

    def _run_fusion_audit(self, note_id: str, prompt: str, *, contract_validator=None) -> dict:
        authoritative_m3 = bool(getattr(self, "authoritative_m3", False))
        retries = min(1, max(0, settings.fusion_timeout_retries))
        attempts = retries + 1
        request_prompt = prompt
        inference_options = self._text_inference_options("fusion_audit")
        timeout = inference_options["request_timeout"]
        for attempt in range(1, attempts + 1):
            job_store.log(
                self.job_id,
                f"笔记 {note_id}：融合模型调用 {attempt}/{attempts} 开始，"
                f"prompt_chars={len(request_prompt)}，max_tokens={inference_options['max_tokens']}，"
                f"thinking={'on' if inference_options['enable_thinking'] else 'off'}，timeout={timeout}s",
            )
            started_at = perf_counter()
            try:
                audit = self.qwen.audit_text(
                    request_prompt,
                    **inference_options,
                )
            except Exception as exc:
                elapsed = perf_counter() - started_at
                if not self._is_timeout_error(exc):
                    job_store.log(
                        self.job_id,
                        f"笔记 {note_id}：融合模型调用 {attempt}/{attempts} 失败，"
                        f"耗时={elapsed:.1f}s，error={exc}",
                    )
                    if authoritative_m3:
                        raise AuditProviderCallError(
                            "text Provider request failed"
                        ) from exc
                    raise
                if attempt < attempts and not isinstance(exc, QwenTimeoutError):
                    job_store.log(
                        self.job_id,
                        f"笔记 {note_id}：融合模型调用 {attempt}/{attempts} 超时，"
                        f"耗时={elapsed:.1f}s，将重试一次",
                    )
                    continue
                job_store.log(
                    self.job_id,
                    f"笔记 {note_id}：融合模型调用 {attempt}/{attempts} 超时，"
                    f"耗时={elapsed:.1f}s，重试次数已用尽",
                )
                if authoritative_m3:
                    raise AuditProviderCallError(
                        "text Provider request timed out"
                    ) from exc
                raise FusionAuditTimeoutError(
                    f"连续 {2 if isinstance(exc, QwenTimeoutError) else attempts} 次调用超时，单次上限 {timeout}s"
                ) from exc

            try:
                if contract_validator is not None:
                    audit = contract_validator(audit)
            except FusionAuditContractError as exc:
                elapsed = perf_counter() - started_at
                diagnostic_dir = settings.outputs_dir / self.job_id / "assets" / note_id / "fusion_failures"
                diagnostic_dir.mkdir(parents=True, exist_ok=True)
                try:
                    valid_ids = sorted(self._fusion_visible_ids(prompt))
                except (ValueError, IndexError, TypeError):
                    valid_ids = []
                capture = getattr(self.qwen, "last_raw_response", None)
                raw_response = capture() if callable(capture) else None
                diagnostic = {
                    "attempt": attempt, "error": str(exc),
                    "invalid_evidence_ids": exc.invalid_evidence_ids,
                    "valid_evidence_ids": valid_ids,
                    "request_prompt": request_prompt,
                    "raw_provider_response": raw_response,
                    "parsed_response": audit,
                }
                # Exclusive names retain earlier failures when a post is replayed.
                with (diagnostic_dir / f"attempt-{attempt}-{time_ns()}.json").open("x", encoding="utf-8") as handle:
                    json.dump(diagnostic, handle, ensure_ascii=False, indent=2)
                job_store.log(
                    self.job_id,
                    f"笔记 {note_id}：融合模型调用 {attempt}/{attempts} contract-invalid，"
                    f"耗时={elapsed:.1f}s，error={exc}",
                )
                if attempt < attempts:
                    previous_output = (
                        {
                            key: audit[key]
                            for key in (
                                "schema_version", "content_title", "summary",
                                "decision_suggestion", "risk_level_suggestion",
                                "primary_risk", "categories", "evidence_items",
                                "rule_matches",
                            )
                            if key in audit
                        }
                        if isinstance(audit, dict)
                        else audit
                    )
                    request_prompt = prompt + "\n上次输出未通过程序校验，请仅纠正合同错误并返回完整 JSON：\n" + json.dumps({
                        "error": str(exc),
                        "invalid_evidence_ids": exc.invalid_evidence_ids,
                        "allowed_evidence_ids": valid_ids,
                        "comment_evidence_contract": (
                            "comment: 开头的证据属于已完成审核；evidence_items 中只能保留 evidence_id，"
                            "不要为评论输出 rule_matches、等级、规则、依据或豁免。"
                        ),
                        "non_comment_evidence_contract": (
                            "image:/ocr:/asr: 等非评论证据必须保留 evidence_id、"
                            "evidence_risk_level（low/medium/high）和 reason，并在 rule_matches 中"
                            "使用允许的 rule_id 建立闭环；保留上次已经正确的非评论字段。"
                        ),
                        "previous_output": previous_output,
                    }, ensure_ascii=False)
                    continue
                if authoritative_m3:
                    raise AuditProviderCallError(
                        "text Provider response violated the audit contract"
                    ) from exc
                raise

            contract_warnings = (
                audit.get("_fusion_contract_warnings")
                if isinstance(audit, dict)
                and isinstance(audit.get("_fusion_contract_warnings"), list)
                else []
            )
            if contract_warnings:
                warning_ids = sorted({
                    str(item.get("evidence_id") or "")
                    for item in contract_warnings
                    if isinstance(item, dict) and item.get("evidence_id")
                })
                job_store.log(
                    self.job_id,
                    f"笔记 {note_id}：已忽略融合模型对已完成评论审核的重复字段，"
                    f"warnings={len(contract_warnings)}，evidence_ids={warning_ids}",
                    stage="analysis",
                    level="warning",
                )
            elapsed = perf_counter() - started_at
            llm_meta = (
                audit.get("_llm_meta")
                if isinstance(audit, dict) and isinstance(audit.get("_llm_meta"), dict)
                else {}
            )
            provider = str(
                llm_meta.get("provider")
                or (
                    "remote_openai_compatible"
                    if settings.use_remote_llm
                    else "dashscope_openai_compatible"
                )
            )
            if authoritative_m3:
                audit["_provider_provenance"] = {
                    "provider": provider,
                    "model": str(llm_meta.get("model") or settings.qwen_text_model),
                    "prompt_sha256": hashlib.sha256(request_prompt.encode("utf-8")).hexdigest(),
                    "prompt_version": self.prompt_set.prompt_version,
                }
            job_store.log(
                self.job_id,
                f"笔记 {note_id}：融合模型调用 {attempt}/{attempts} 完成，"
                f"耗时={elapsed:.1f}s，finish_reason={llm_meta.get('finish_reason', 'unknown')}，"
                f"input_tokens={llm_meta.get('prompt_tokens', 'unknown')}，"
                f"output_tokens={llm_meta.get('completion_tokens', 'unknown')}，"
                f"total_tokens={llm_meta.get('total_tokens', 'unknown')}",
            )
            return audit

        raise FusionAuditTimeoutError("融合模型调用未执行")

    @staticmethod
    def _is_timeout_error(exc: BaseException) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, (TimeoutError, requests.exceptions.Timeout)):
                return True
            message = str(current).lower()
            if "timed out" in message or "timeout" in message:
                return True
            current = current.__cause__ or current.__context__
        return False

    def _recover_truncated_fusion_audit(self, audit: dict) -> dict:
        if not isinstance(audit, dict):
            return {}
        raw_response = str(audit.get("raw_response") or "")
        if not raw_response or audit.get("evidence_items") or audit.get("rule_matches"):
            return audit
        recovered = dict(audit)
        recovered["schema_version"] = self._extract_json_string_field(raw_response, "schema_version") or "audit_fusion_v3"
        for key in (
            "content_title",
            "summary",
            "decision_suggestion",
            "risk_level_suggestion",
            "primary_risk",
        ):
            value = self._extract_json_string_field(raw_response, key)
            if value:
                recovered[key] = value
        categories = self._extract_json_array_field(raw_response, "categories")
        if isinstance(categories, list):
            recovered["categories"] = categories
        evidence_items = self._extract_json_array_field(raw_response, "evidence_items")
        if isinstance(evidence_items, list):
            recovered["evidence_items"] = [item for item in evidence_items if isinstance(item, dict)]
        rule_matches = self._extract_json_array_field(raw_response, "rule_matches")
        if isinstance(rule_matches, list):
            recovered["rule_matches"] = [item for item in rule_matches if isinstance(item, dict)]
        return recovered

    @staticmethod
    def _extract_json_string_field(text: str, key: str) -> str:
        marker = f'"{key}"'
        start = text.find(marker)
        if start < 0:
            return ""
        colon = text.find(":", start + len(marker))
        if colon < 0:
            return ""
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(text[colon + 1:].lstrip())
        except json.JSONDecodeError:
            return ""
        return str(value) if isinstance(value, str) else ""

    @staticmethod
    def _extract_json_array_field(text: str, key: str) -> list | None:
        marker = f'"{key}"'
        start = text.find(marker)
        if start < 0:
            return None
        colon = text.find(":", start + len(marker))
        if colon < 0:
            return None
        array_start = text.find("[", colon)
        if array_start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for pos in range(array_start, len(text)):
            ch = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[array_start:pos + 1])
                    except json.JSONDecodeError:
                        return None
                    return value if isinstance(value, list) else None
        return None

    def _legacy_evidence_items(self, audit: dict) -> list[dict]:
        items: list[dict] = []
        for index, ev in enumerate(audit.get("evidence", []) or [], start=1):
            if not isinstance(ev, dict):
                continue
            source = str(ev.get("source") or "")
            modality = self._modality_from_source(source)
            items.append({
                "evidence_id": f"ev_{modality}_{index:03d}",
                "primary_modality": modality,
                "source": source,
                "text": ev.get("text", ""),
                "reason": ev.get("reason", ""),
                "severity": ev.get("severity", ""),
                "start": ev.get("start"),
                "end": ev.get("end"),
            })
        return items

    @staticmethod
    def _primary_modality_for_item(item: dict) -> str:
        value = str(item.get("primary_modality") or item.get("modality") or "").lower().strip()
        source = str(item.get("source") or "").lower()
        visual_elements = item.get("visual_elements") if isinstance(item.get("visual_elements"), list) else []
        has_ocr_text = bool(str(item.get("ocr_text") or item.get("ocr_text_zh") or "").strip())
        has_textual_image_evidence = (
            (source.startswith("image") or source.startswith("video_frame") or source.startswith("video:"))
            and bool(str(item.get("text") or "").strip())
            and not visual_elements
        )
        if value == "vision" and (has_ocr_text or has_textual_image_evidence):
            return "ocr"
        if value in {"text", "ocr", "asr", "vision", "comment"}:
            return value
        if has_ocr_text:
            return "ocr"
        return AuditPipeline._modality_from_source(str(item.get("source") or ""))

    @staticmethod
    def _modality_from_source(source: str) -> str:
        text = str(source or "").lower()
        if text.startswith("comment"):
            return "comment"
        if text.startswith("video_audio") or text.startswith("audio"):
            return "asr"
        if text.startswith("image") or text.startswith("video_frame") or text.startswith("video:"):
            return "vision"
        if text.startswith("title") or text.startswith("desc"):
            return "text"
        return "text"

    @staticmethod
    def _default_source_for_modality(modality: str) -> str:
        return {
            "text": "title",
            "ocr": "image",
            "asr": "video_audio",
            "comment": "comment",
            "vision": "image",
        }.get(modality, "title")

    def _fill_original_evidence(
        self,
        item: dict,
        subject: AuditSubject,
        comments_by_id: dict[str, dict],
        evidence_index: dict,
    ) -> None:
        source = str(item.get("source") or "")
        if source == "title":
            item.setdefault("source_label", "标题")
            item["text"] = subject.title
            if subject.title_zh:
                item["translation_zh"] = subject.title_zh
        elif source == "desc":
            item.setdefault("source_label", "正文")
            item["text"] = subject.desc
            if subject.desc_zh:
                item["translation_zh"] = subject.desc_zh
        elif source.startswith("comment"):
            comment_id = str(item.get("comment_id") or "").strip()
            if not comment_id and ":" in source:
                comment_id = source.split(":", 1)[1].strip()
            comment = comments_by_id.get(comment_id) or {}
            if comment_id:
                item["comment_id"] = comment_id
            if comment:
                item["nickname"] = str(comment.get("nickname") or item.get("nickname") or "")
                item["text"] = str(comment.get("content") or item.get("text") or "")
            item.setdefault("source_label", "评论")
        elif source.startswith("image"):
            image = self._match_image_unit(evidence_index, source)
            if image:
                item.setdefault("source_label", f"图片 {image.get('index', '')}".strip())
                item.setdefault("asset_rel", image.get("asset_rel") or "")
                if image.get("ocr_text") or image.get("ocr_text_zh"):
                    item.setdefault("ocr_text", image.get("ocr_text") or "")
                    item.setdefault("ocr_text_zh", image.get("ocr_text_zh") or "")
            if item.get("primary_modality") == "ocr":
                item["text"] = item.get("ocr_text_zh") or item.get("ocr_text") or item.get("text") or ""
        elif source.startswith("video_frame") or source.startswith("video:"):
            frame = self._match_frame_unit(evidence_index, source)
            if frame:
                item.setdefault("asset_rel", frame.get("asset_rel") or "")
                item.setdefault("frame_id", frame.get("frame_id") or "")
                item.setdefault("timestamp", frame.get("timestamp"))
                item.setdefault("ocr_text", frame.get("ocr_text") or "")
                item.setdefault("ocr_text_zh", frame.get("ocr_text_zh") or "")
            if item.get("primary_modality") == "ocr":
                item["text"] = item.get("ocr_text_zh") or item.get("ocr_text") or item.get("text") or ""
        if item.get("primary_modality") == "ocr" and item.get("asset_rel"):
            supporting = item.get("supporting_modalities")
            if not isinstance(supporting, list):
                supporting = []
            if "vision" not in supporting:
                supporting.append("vision")
            item["supporting_modalities"] = supporting

    @staticmethod
    def _match_image_unit(evidence_index: dict, source: str) -> dict:
        for image in evidence_index.get("image_units") or []:
            if str(image.get("source") or "") == source or str(image.get("evidence_id") or "") == source:
                return image
        return {}

    @staticmethod
    def _match_frame_unit(evidence_index: dict, source: str) -> dict:
        for frame in evidence_index.get("timeline_frames") or []:
            if source in {str(frame.get("source") or ""), str(frame.get("video_frame_source") or "")}:
                return frame
        timestamp = AuditPipeline._source_timestamp(source)
        if timestamp is None:
            return {}
        for frame in evidence_index.get("timeline_frames") or []:
            try:
                if abs(float(frame.get("timestamp")) - timestamp) < 0.25:
                    return frame
            except (TypeError, ValueError):
                continue
        return {}

    @staticmethod
    def _source_timestamp(source: str) -> float | None:
        if ":" not in source:
            return None
        tail = source.rsplit(":", 1)[-1]
        if "-" in tail:
            tail = tail.split("-", 1)[0]
        try:
            return float(tail)
        except ValueError:
            return None

    @staticmethod
    def _compact_signature(value) -> str:
        return "".join(str(value or "").split()).lower()[:120]

    def _normalize_rule_matches(self, audit: dict) -> list[dict]:
        values = audit.get("rule_matches") if isinstance(audit.get("rule_matches"), list) else []
        out: list[dict] = []
        allowed_rule_ids = self._stage_rule_ids("fusion_audit")
        for item in values:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or item.get("id") or "").strip()
            if not rule_id:
                continue
            if self._is_ruleset_v2() and rule_id not in allowed_rule_ids:
                continue
            if self._normalize_matched_exemption_ids(
                rule_id,
                item.get("matched_exemption_ids"),
            ):
                continue
            evidence_ids = item.get("evidence_ids") if isinstance(item.get("evidence_ids"), list) else []
            out.append({
                "rule_id": rule_id,
                "rule_name": str(item.get("rule_name") or item.get("rule") or ""),
                "modality": str(item.get("modality") or item.get("source") or ""),
                "evidence_ids": [str(value) for value in evidence_ids if str(value).strip()],
                "confidence": str(item.get("confidence") or ""),
                "features": item.get("features") if isinstance(item.get("features"), list) else [],
            })
        return out

    def _infer_rule_matches_from_evidence_items(self, evidence_items: list[dict]) -> list[dict]:
        if self._is_ruleset_v2():
            allowed_rule_ids = self._stage_rule_ids("fusion_audit")
            matches = []
            for item in evidence_items:
                if not isinstance(item, dict):
                    continue
                rule_id = str(item.get("rule_id") or "").strip()
                evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
                if rule_id not in allowed_rule_ids or not evidence_id:
                    continue
                matches.append({
                    "rule_id": rule_id,
                    "rule_name": "",
                    "modality": str(item.get("primary_modality") or item.get("modality") or ""),
                    "evidence_ids": [evidence_id],
                    "confidence": str(item.get("confidence") or ""),
                    "features": item.get("features") if isinstance(item.get("features"), list) else [],
                })
            return matches
        rules = self._active_scoring_rules()
        if not rules:
            return []
        by_source: dict[str, list[dict]] = {}
        for rule in rules:
            source = str(rule.get("source") or "").strip()
            if source:
                by_source.setdefault(source, []).append(rule)
        matches: list[dict] = []
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            modality = str(item.get("primary_modality") or item.get("modality") or "").strip()
            evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
            if not modality or not evidence_id:
                continue
            candidates = by_source.get(modality) or []
            if not candidates:
                continue
            library_id = str(item.get("risk_library_id") or item.get("library_id") or "").strip()
            selected = None
            if library_id:
                selected = next(
                    (
                        rule for rule in candidates
                        if str(rule.get("library_id") or "") == library_id
                    ),
                    None,
                )
            selected = selected or candidates[0]
            matches.append({
                "rule_id": str(selected.get("id") or selected.get("rule_id") or ""),
                "rule_name": str(selected.get("label") or selected.get("rule") or ""),
                "modality": modality,
                "evidence_ids": [evidence_id],
                "confidence": str(item.get("confidence") or ""),
                "features": item.get("features") if isinstance(item.get("features"), list) else [],
            })
        return [match for match in matches if match.get("rule_id")]

    def _score_rule_matches(self, rule_matches: list[dict]) -> dict:
        rules = self._active_scoring_rules()
        rule_by_id = {str(rule.get("id") or rule.get("rule_id") or ""): rule for rule in rules}
        thresholds = self._active_thresholds()
        merged: dict[str, dict] = {}
        for match in rule_matches:
            rule_id = str(match.get("rule_id") or "")
            rule = rule_by_id.get(rule_id)
            if not rule:
                continue
            row = merged.setdefault(rule_id, {
                "rule_id": rule_id,
                "rule": str(rule.get("label") or rule.get("rule") or rule_id),
                "category": str(rule.get("category") or rule.get("library_id") or ""),
                "library_id": str(rule.get("library_id") or ""),
                "modality": str(rule.get("source") or match.get("modality") or ""),
                "source": str(rule.get("source") or match.get("modality") or ""),
                "score": self._normalize_risk_score(rule.get("score"), "none"),
                "score_policy": "once_per_rule",
                "evidence_ids": [],
            })
            for evidence_id in match.get("evidence_ids") or []:
                if evidence_id not in row["evidence_ids"]:
                    row["evidence_ids"].append(evidence_id)
        score_breakdown = list(merged.values())
        category_totals: dict[str, int] = {}
        for row in score_breakdown:
            category = row["category"] or row["library_id"] or "风险类别"
            category_totals[category] = min(100, category_totals.get(category, 0) + int(row["score"] or 0))
        category_scores = [
            {
                "category": category,
                "score": score,
                "level": self._level_from_score(score, thresholds),
            }
            for category, score in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
        ]
        risk_score = max(category_totals.values(), default=0)
        risk_level = self._level_from_score(risk_score, thresholds)
        decision = "reject" if risk_level == "high" else ("review" if risk_level in {"medium", "low"} else "pass")
        return {
            "score_breakdown": score_breakdown,
            "category_scores": category_scores,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "decision": decision,
        }

    def _active_scoring_rules(self) -> list[dict]:
        rules = self.rule_snapshot.get("scoring_rules") if isinstance(self.rule_snapshot, dict) else []
        return [rule for rule in (rules or []) if isinstance(rule, dict)]

    @staticmethod
    def _rule_matches_for_evidence_ids(rule_matches: list[dict], evidence_ids: set[str]) -> list[dict]:
        if not evidence_ids:
            return []
        out = []
        for match in rule_matches:
            matched_ids = [
                str(value)
                for value in match.get("evidence_ids") or []
                if str(value) in evidence_ids
            ]
            if matched_ids:
                out.append({**match, "evidence_ids": matched_ids})
        return out

    def _active_thresholds(self) -> dict:
        incoming = self.rule_snapshot.get("thresholds") if isinstance(self.rule_snapshot, dict) else {}
        thresholds = dict(DEFAULT_THRESHOLDS)
        if isinstance(incoming, dict):
            for key in thresholds:
                try:
                    thresholds[key] = int(incoming.get(key, thresholds[key]))
                except (TypeError, ValueError):
                    pass
        return thresholds

    @staticmethod
    def _level_from_score(score: int, thresholds: dict) -> str:
        if score >= int(thresholds.get("high", 80)):
            return "high"
        if score >= int(thresholds.get("medium", 60)):
            return "medium"
        if score >= int(thresholds.get("review", 40)):
            return "low"
        return "none"

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
            timeline_by_id = {
                str(frame.get("frame_id") or ""): frame
                for frame in video.get("timeline_frames") or video.get("frames") or []
            }
            for segment in video.get("segment_reviews") or []:
                analysis = segment.get("analysis") or {}
                for risk in analysis.get("visual_risks") or []:
                    for frame_id in risk.get("frame_ids") or []:
                        frame = timeline_by_id.get(str(frame_id)) or {}
                        reason = (
                            self._matching_frame_reason(frame, frame_reasons)
                            or risk.get("reason", "")
                        )
                        frames_out.append({
                            "video_index": video.get("index", video_idx),
                            "frame_index": frame.get("frame_index", frame.get("index")),
                            "frame_id": frame_id,
                            "timestamp": frame.get("timestamp"),
                            "frame_number": frame.get("frame_number"),
                            "severity": risk.get("risk_level", ""),
                            "risk_score": risk.get("score", 0),
                            "risk_library_id": risk.get("risk_library_id", ""),
                            "risk_library_label": risk.get("risk_library_label", ""),
                            "risk_type": risk.get("risk_type", ""),
                            "reason": reason,
                            "analysis_result": reason,
                            "visual_summary": analysis.get("segment_summary", ""),
                            "ocr_text": frame.get("ocr_text", ""),
                            "ocr_text_zh": frame.get("ocr_text_zh", ""),
                            "asset_rel": frame.get("asset_rel") or self._to_job_rel(frame.get("path"), job_root),
                            "review_sheet_rel": segment.get("asset_rel"),
                            "segment_id": segment.get("segment_id"),
                        })
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
                "risk_library_id": top.get("risk_library_id", ""),
                "risk_library_label": top.get("risk_library_label", ""),
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

    def _build_evidence_index(
        self,
        subject: AuditSubject,
        image_analyses: list[dict],
        video_results: list[dict],
        comments: list[dict],
    ) -> dict:
        thresholds = self._active_thresholds()
        review_threshold = int(thresholds.get("review", 40))
        image_units: list[dict] = []
        video_units: list[dict] = []
        timeline_frames: list[dict] = []
        review_sheets: list[dict] = []
        segment_reviews: list[dict] = []
        ocr_chunks: list[dict] = []
        asr_chunks: list[dict] = []
        asr_segments: list[dict] = []
        ocr_items: list[dict] = []
        evidence_catalog: list[dict] = []

        if subject.title.strip():
            evidence_catalog.append({
                "evidence_id": "text:title",
                "source": "title",
                "primary_modality": "text",
                "text": self._truncate_text(subject.title, 240),
                "translation_zh": self._truncate_text(subject.title_zh, 240),
                "risk_score": 0,
                "evidence_risk_level": "none",
            })
        if subject.desc.strip():
            evidence_catalog.append({
                "evidence_id": "text:desc",
                "source": "desc",
                "primary_modality": "text",
                "text": self._truncate_text(subject.desc, 500),
                "translation_zh": self._truncate_text(subject.desc_zh, 500),
                "risk_score": 0,
                "evidence_risk_level": "none",
            })

        for image_index, image in enumerate(image_analyses, start=1):
            source = f"image:{image_index}"
            unit = {
                "evidence_id": image.get("evidence_id") or source,
                "index": image.get("index", image_index - 1),
                "source": source,
                "asset_rel": image.get("asset_rel") or self._to_job_rel(
                    image.get("local_path"),
                    settings.outputs_dir / self.job_id,
                ),
                "url": image.get("url", ""),
                "ocr_text": image.get("ocr_text", ""),
                "ocr_text_zh": image.get("ocr_text_zh", ""),
                "ocr_engine": image.get("ocr_engine", ""),
                "ocr_language": image.get("ocr_language", ""),
                "ocr_confidence": image.get("ocr_confidence", 0.0),
                "visual_summary": image.get("visual_summary", ""),
                "benign_context": image.get("benign_context", ""),
                "risk_items": self._compact_risk_items(
                    image.get("risk_items") or [],
                    application_stage="image_evidence",
                ),
                "error": image.get("error", ""),
            }
            image_units.append(unit)
            for risk_index, risk in enumerate(unit["risk_items"], start=1):
                if self._is_ruleset_v2():
                    risk_level = self._strict_v2_risk_level(
                        risk.get("severity"),
                        field="image_evidence.severity",
                        allow_none=False,
                    )
                    score = self._risk_score_for_level(risk_level)
                else:
                    score = self._normalize_risk_score(
                        risk.get("score"), risk.get("severity") or "low"
                    )
                    risk_level = self._level_from_score(score, thresholds)
                catalog_item = {
                    "evidence_id": f"{source}/risk:{risk_index}",
                    "source": source,
                    "primary_modality": "vision",
                    "asset_rel": unit["asset_rel"],
                    "visual_summary": unit["visual_summary"],
                    "risk_library_id": risk.get("risk_library_id", ""),
                    "risk_library_label": risk.get("risk_library_label", ""),
                    "risk_type": risk.get("risk_type", ""),
                    "reason": risk.get("reason") or risk.get("evidence", ""),
                    "risk_score": score,
                    "evidence_risk_level": risk_level,
                }
                if self._is_ruleset_v2():
                    catalog_item["rule_id"] = risk.get("rule_id", "")
                evidence_catalog.append(catalog_item)

        for video_offset, video in enumerate(video_results, start=1):
            video_ref = f"video:{int(video.get('index', video_offset - 1) or 0) + 1}"
            transcript = video.get("transcript") or {}
            frame_by_id: dict[str, dict] = {}
            compact_frames = []
            for frame in video.get("timeline_frames") or video.get("frames") or []:
                frame_id = str(frame.get("frame_id") or "")
                frame_ref = f"{video_ref}/frame:{frame_id}"
                item = {
                    "source": frame_ref,
                    "video_frame_source": f"video_frame:{round(float(frame.get('timestamp') or 0.0), 2)}",
                    "video_source": video_ref,
                    "frame_id": frame_id,
                    "timestamp": frame.get("timestamp"),
                    "frame_number": frame.get("frame_number"),
                    "asset_rel": frame.get("asset_rel") or self._to_job_rel(
                        frame.get("path"),
                        settings.outputs_dir / self.job_id,
                    ),
                    "ocr_text": frame.get("ocr_text", ""),
                    "ocr_text_zh": frame.get("ocr_text_zh", ""),
                    "ocr_engine": frame.get("ocr_engine", ""),
                    "ocr_language": frame.get("ocr_language", ""),
                    "ocr_confidence": frame.get("ocr_confidence", 0.0),
                }
                frame_by_id[frame_id] = item
                compact_frames.append(item)
                timeline_frames.append(item)
                if item["ocr_text"] or item["ocr_text_zh"]:
                    ocr_items.append({
                        "source": frame_ref,
                        "timestamp": item["timestamp"],
                        "text": item["ocr_text"],
                        "text_zh": item["ocr_text_zh"],
                        "language": item["ocr_language"],
                        "confidence": item["ocr_confidence"],
                        "engine": item["ocr_engine"],
                        "asset_rel": item["asset_rel"],
                        "frame_id": frame_id,
                    })

            for segment_number, seg in enumerate(transcript.get("segments") or [], start=1):
                text = self._truncate_text(seg.get("text", ""), 500)
                text_zh = self._truncate_text(seg.get("translation_zh", ""), 600)
                if not text and not text_zh:
                    continue
                asr_segments.append({
                    "source": f"{video_ref}/asr-raw:{segment_number}",
                    "audio_source": f"video_audio:{self._time_range(seg.get('start'), seg.get('end')).replace('s', '')}",
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "text": text,
                    "source_text_dolphin": self._truncate_text(seg.get("source_text_dolphin") or text, 500),
                    "source_text_mms": self._truncate_text(seg.get("source_text_mms", ""), 500),
                    "translation_zh": text_zh,
                    "language": transcript.get("language", ""),
                    "confidence": seg.get("translation_confidence", ""),
                })

            video_review_sheets = video.get("review_sheets") or []
            for sheet in video_review_sheets:
                stored_sheet = {**sheet, "source": sheet.get("segment_id"), "video_source": video_ref}
                review_sheets.append(stored_sheet)
                for chunk in sheet.get("ocr_chunks") or []:
                    ocr_chunks.append({**chunk, "video_source": video_ref})
                for chunk in sheet.get("asr_chunks") or []:
                    asr_chunks.append({**chunk, "video_source": video_ref})

            video_segment_reviews = []
            for segment in video.get("segment_reviews") or []:
                analysis = segment.get("analysis") or {}
                compact_segment = {
                    "source": segment.get("segment_id"),
                    "video_source": video_ref,
                    "segment_id": segment.get("segment_id"),
                    "index": segment.get("index"),
                    "asset_rel": segment.get("asset_rel"),
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "frame_ids": segment.get("frame_ids") or [],
                    "segment_summary": analysis.get("segment_summary", ""),
                    "segment_score": analysis.get("segment_score", 0),
                    "segment_level": analysis.get("segment_level", "none"),
                    "visual_risks": analysis.get("visual_risks") or [],
                    "ocr_risks": analysis.get("ocr_risks") or [],
                    "asr_risks": analysis.get("asr_risks") or [],
                    "library_reviews": segment.get("library_reviews") or [],
                }
                if self._is_ruleset_v2():
                    compact_segment["matched_exemption_ids"] = (
                        analysis.get("matched_exemption_ids") or []
                    )
                segment_reviews.append(compact_segment)
                video_segment_reviews.append(compact_segment)

                for risk_index, risk in enumerate(compact_segment["visual_risks"], start=1):
                    frame_ids = risk.get("frame_ids") or []
                    frame = frame_by_id.get(str(frame_ids[0])) if frame_ids else {}
                    risk_library_id = str(risk.get("risk_library_id") or "").strip()
                    library_prefix = f"{risk_library_id}/" if risk_library_id else ""
                    evidence_catalog.append({
                        "evidence_id": f"{segment.get('segment_id')}/{library_prefix}visual-risk:{risk_index}",
                        "source": (frame or {}).get("source") or segment.get("segment_id"),
                        "primary_modality": "vision",
                        "frame_ids": frame_ids,
                        "frame_id": (frame or {}).get("frame_id", ""),
                        "frame_number": (frame or {}).get("frame_number"),
                        "timestamp": (frame or {}).get("timestamp"),
                        "asset_rel": (frame or {}).get("asset_rel", ""),
                        "risk_library_id": risk_library_id,
                        "risk_library_label": risk.get("risk_library_label", ""),
                        "risk_type": risk.get("risk_type", ""),
                        "reason": risk.get("reason", ""),
                        "risk_score": risk.get("score", 0),
                        "evidence_risk_level": risk.get("risk_level", "none"),
                    })
                ocr_by_id = {str(item.get("ocr_chunk_id") or ""): item for item in segment.get("ocr_chunks") or []}
                for risk_index, risk in enumerate(compact_segment["ocr_risks"], start=1):
                    chunk = ocr_by_id.get(str(risk.get("ocr_chunk_id") or "")) or {}
                    frame_ids = risk.get("frame_ids") or []
                    frame = frame_by_id.get(str(frame_ids[0])) if frame_ids else {}
                    risk_library_id = str(risk.get("risk_library_id") or "").strip()
                    library_prefix = f"{risk_library_id}/" if risk_library_id else ""
                    source_text = "\n".join(
                        item.get("source_text", "") for item in chunk.get("items") or [] if item.get("source_text")
                    )
                    translation_zh = "\n".join(
                        item.get("translation_zh", "") for item in chunk.get("items") or [] if item.get("translation_zh")
                    )
                    evidence_catalog.append({
                        "evidence_id": f"{segment.get('segment_id')}/{library_prefix}ocr-risk:{risk_index}",
                        "source": (frame or {}).get("source") or segment.get("segment_id"),
                        "primary_modality": "ocr",
                        "ocr_chunk_id": risk.get("ocr_chunk_id"),
                        "frame_ids": frame_ids,
                        "frame_id": (frame or {}).get("frame_id", ""),
                        "frame_number": (frame or {}).get("frame_number"),
                        "timestamp": (frame or {}).get("timestamp"),
                        "asset_rel": (frame or {}).get("asset_rel", ""),
                        "ocr_text": source_text,
                        "ocr_text_zh": translation_zh,
                        "ocr_context": chunk.get("items") or [],
                        "start": chunk.get("start"),
                        "end": chunk.get("end"),
                        "risk_library_id": risk_library_id,
                        "risk_library_label": risk.get("risk_library_label", ""),
                        "risk_type": risk.get("risk_type", ""),
                        "reason": risk.get("reason", ""),
                        "risk_score": risk.get("score", 0),
                        "evidence_risk_level": risk.get("risk_level", "none"),
                    })
                asr_by_id = {str(item.get("asr_chunk_id") or ""): item for item in segment.get("asr_chunks") or []}
                for risk_index, risk in enumerate(compact_segment["asr_risks"], start=1):
                    chunk = asr_by_id.get(str(risk.get("asr_chunk_id") or "")) or {}
                    risk_library_id = str(risk.get("risk_library_id") or "").strip()
                    library_prefix = f"{risk_library_id}/" if risk_library_id else ""
                    evidence_catalog.append({
                        "evidence_id": f"{segment.get('segment_id')}/{library_prefix}asr-risk:{risk_index}",
                        "source": f"video_audio:{self._time_range(chunk.get('start'), chunk.get('end')).replace('s', '')}",
                        "video_source": video_ref,
                        "primary_modality": "asr",
                        "asr_chunk_id": risk.get("asr_chunk_id"),
                        "start": chunk.get("start"),
                        "end": chunk.get("end"),
                        "source_text_dolphin": chunk.get("source_text_dolphin", ""),
                        "source_text_mms": chunk.get("source_text_mms", ""),
                        "translation_zh": chunk.get("translation_zh", ""),
                        "asr_consistency": chunk.get("consistency", ""),
                        "risk_library_id": risk_library_id,
                        "risk_library_label": risk.get("risk_library_label", ""),
                        "risk_type": risk.get("risk_type", ""),
                        "reason": risk.get("reason", ""),
                        "risk_score": risk.get("score", 0),
                        "evidence_risk_level": risk.get("risk_level", "none"),
                    })

            video_units.append({
                "source": video_ref,
                "asset_rel": self._to_job_rel(video.get("local_path"), settings.outputs_dir / self.job_id),
                "timeline_frame_count": len(compact_frames),
                "review_sheet_count": len(video_review_sheets),
                "segment_review_count": len(video_segment_reviews),
                "duration": video.get("duration", 0.0),
                "transcript_summary": self._truncate_text(transcript.get("text_zh") or transcript.get("text") or "", 420),
                "asr_raw_rel": transcript.get("asr_raw_rel"),
                "segment_reviews": video_segment_reviews,
            })

        comment_units = []
        for comment in comments:
            comment_id = str(comment.get("comment_id") or comment.get("id") or "")
            unit = {
                "source": f"comment:{comment_id}",
                "comment_id": comment_id,
                "nickname": comment.get("nickname", ""),
                "text": comment.get("source_text") or comment.get("content") or "",
                "translation_zh": comment.get("translation_zh", ""),
                "risk_score": comment.get("risk_score"),
                "risk_level": comment.get("risk_level", ""),
                "risk_library_id": comment.get("risk_library_id", ""),
                "risk_library_label": comment.get("risk_library_label", ""),
                "secondary_library_ids": comment.get("secondary_library_ids") or [],
                "risk_type": comment.get("risk_type", ""),
                "risk_basis": comment.get("risk_basis", ""),
                "exemption_basis": comment.get("exemption_basis", ""),
                "evidence_quote": comment.get("evidence_quote", ""),
                "audit_status": comment.get("audit_status", "failed"),
            }
            if self._is_ruleset_v2():
                unit["rule_id"] = comment.get("rule_id", "")
            comment_units.append(unit)
            if unit["audit_status"] == "completed" and (
                int(unit.get("risk_score") or 0) > 0
                if self._is_ruleset_v2()
                else int(unit.get("risk_score") or 0) >= review_threshold
            ):
                catalog_item = {
                    "evidence_id": f"comment:{comment_id}",
                    "source": f"comment:{comment_id}",
                    "primary_modality": "comment",
                    "comment_id": comment_id,
                    "nickname": unit["nickname"],
                    "text": unit["text"],
                    "translation_zh": unit["translation_zh"],
                    "risk_library_id": unit["risk_library_id"],
                    "risk_library_label": unit["risk_library_label"],
                    "secondary_library_ids": unit["secondary_library_ids"],
                    "risk_type": unit["risk_type"],
                    "reason": unit["risk_basis"],
                    "exemption_basis": unit["exemption_basis"],
                    "risk_score": unit["risk_score"],
                    "evidence_risk_level": unit["risk_level"],
                }
                if self._is_ruleset_v2():
                    catalog_item["rule_id"] = unit["rule_id"]
                evidence_catalog.append(catalog_item)

        return {
            "text_context": {
                "title": subject.title,
                "title_zh": subject.title_zh,
                "desc": subject.desc,
                "desc_zh": subject.desc_zh,
                "comments_count": len(comments),
            },
            "image_units": image_units,
            "video_units": video_units,
            "timeline_frames": timeline_frames,
            "review_sheets": review_sheets,
            "segment_reviews": segment_reviews,
            "ocr_chunks": ocr_chunks,
            "asr_chunks": asr_chunks,
            "asr_segments": asr_segments,
            "asr_raw": [
                {"source": video.get("source"), "asr_raw_rel": (video.get("transcript") or {}).get("asr_raw_rel")}
                for video in video_results
                if (video.get("transcript") or {}).get("asr_raw_rel")
            ],
            "ocr_items": ocr_items,
            "comment_units": comment_units,
            "evidence_catalog": evidence_catalog,
            "final_evidence_refs": [],
            "moment_sheets": [],
            "moments": [],
            "precise_sheets": [],
        }

    def _build_legacy_evidence_index(self, subject: AuditSubject, image_analyses: list[dict], video_results: list[dict]) -> dict:
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
                "risk_items": self._compact_risk_items(
                    image.get("risk_items") or [],
                    application_stage="image_evidence",
                ),
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
                    "summary": analysis.get("summary", ""),
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
            "video_text_context": {
                "ocr_global_text": self._global_ocr_text_for_prompt(evidence_index),
                "asr_global_text": self._global_asr_text_for_prompt(evidence_index),
            },
            "video_visual_context": self._video_visual_context_for_prompt(evidence_index),
            "candidate_context": self._candidate_context_for_prompt(evidence_index),
        }
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        limit = settings.fusion_frame_evidence_max_chars
        if limit > 0 and len(text) > limit:
            compact["candidate_context"] = compact["candidate_context"][: max(1, VIDEO_CANDIDATE_CONTEXT_MAX_ITEMS // 2)]
            text_context = compact["video_text_context"]
            text_context["ocr_global_text"] = self._truncate_text(
                text_context.get("ocr_global_text", ""),
                max(400, VIDEO_TEXT_CONTEXT_MAX_CHARS // 2),
            )
            text_context["asr_global_text"] = self._truncate_text(
                text_context.get("asr_global_text", ""),
                max(400, VIDEO_TEXT_CONTEXT_MAX_CHARS // 2),
            )
            compact["video_visual_context"] = self._truncate_text(
                compact.get("video_visual_context", ""),
                max(300, VIDEO_VISUAL_CONTEXT_MAX_CHARS // 2),
            )
            text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        return text

    def _global_ocr_text_for_prompt(self, evidence_index: dict) -> str:
        texts = []
        for item in evidence_index.get("ocr_items") or []:
            text = self._preferred_text(item)
            if text:
                texts.append(text)
        return self._compact_text_sequence(texts, VIDEO_TEXT_CONTEXT_MAX_CHARS)

    def _global_asr_text_for_prompt(self, evidence_index: dict) -> str:
        texts = []
        for item in evidence_index.get("asr_segments") or []:
            text = self._preferred_text(item)
            if text:
                texts.append(text)
        return self._compact_text_sequence(texts, VIDEO_TEXT_CONTEXT_MAX_CHARS)

    def _video_visual_context_for_prompt(self, evidence_index: dict) -> str:
        summaries = []
        moments = sorted(
            evidence_index.get("moments") or [],
            key=lambda item: float(item.get("start") or 0.0),
        )
        for moment in moments:
            summary = (
                str(moment.get("summary") or "").strip()
                or str(moment.get("reason") or "").strip()
                or str(moment.get("safe_context") or "").strip()
            )
            if not summary:
                continue
            source = str(moment.get("source") or "").strip()
            prefix = f"{source}：" if source else ""
            summaries.append(prefix + summary)
        return self._compact_text_sequence(summaries, VIDEO_VISUAL_CONTEXT_MAX_CHARS, separator="；")

    def _candidate_context_for_prompt(self, evidence_index: dict) -> list[dict]:
        out: list[dict] = []
        moments_by_source = {
            str(moment.get("source") or ""): moment
            for moment in evidence_index.get("moments") or []
            if moment.get("source")
        }
        covered_moments: set[str] = set()
        for precise in evidence_index.get("precise_sheets") or []:
            if len(out) >= VIDEO_CANDIDATE_CONTEXT_MAX_ITEMS:
                break
            candidate_frame_id = str(precise.get("candidate_frame_id") or "").strip()
            moment_id = str(precise.get("moment_id") or "").strip()
            moment = moments_by_source.get(moment_id) or {}
            center_source = self._frame_source_for_candidate(
                evidence_index,
                candidate_frame_id,
                precise.get("center_timestamp"),
            )
            out.append({
                "candidate_id": str(precise.get("source") or f"{moment_id}:{candidate_frame_id}" or len(out) + 1),
                "moment": moment_id,
                "center_source": center_source,
                "visual": {
                    "summary": self._truncate_text(precise.get("visual_summary", ""), 140),
                    "safe": self._truncate_text(precise.get("safe_context", ""), 120),
                    "risks": self._compact_precise_risks(precise.get("risk_items") or [], center_source),
                },
                "ocr": self._candidate_ocr_for_prompt(evidence_index, [candidate_frame_id]),
                "asr": self._candidate_asr_for_prompt(evidence_index, moment),
            })
            if moment_id:
                covered_moments.add(moment_id)

        for moment in evidence_index.get("moments") or []:
            if len(out) >= VIDEO_CANDIDATE_CONTEXT_MAX_ITEMS:
                break
            moment_id = str(moment.get("source") or "").strip()
            if moment_id in covered_moments or moment.get("status") != "suspicious":
                continue
            frame_ids = [str(value) for value in (moment.get("candidate_frame_ids") or []) if str(value).strip()]
            center_source = self._frame_source_for_candidate(evidence_index, frame_ids[0] if frame_ids else "", None)
            out.append({
                "candidate_id": f"{moment_id}:coarse" if moment_id else f"coarse:{len(out) + 1}",
                "moment": moment_id,
                "center_source": center_source,
                "visual": {
                    "summary": self._truncate_text(moment.get("reason", ""), 140),
                    "safe": "",
                    "risks": [],
                },
                "ocr": self._candidate_ocr_for_prompt(evidence_index, frame_ids),
                "asr": self._candidate_asr_for_prompt(evidence_index, moment),
            })
        return out

    def _compact_precise_risks(self, risk_items: list[dict], fallback_source: str) -> list[dict]:
        out = []
        for item in risk_items[:VIDEO_CANDIDATE_TEXT_MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            timestamp = item.get("timestamp")
            source = fallback_source
            try:
                source = f"video_frame:{float(timestamp):.2f}"
            except (TypeError, ValueError):
                pass
            out.append({
                "source": source,
                "level": str(item.get("severity") or "low"),
                "type": self._truncate_text(item.get("risk_type", ""), 60),
                "text": self._truncate_text(item.get("evidence", ""), 100),
                "reason": self._truncate_text(item.get("reason", ""), 100),
            })
        return out

    def _candidate_ocr_for_prompt(self, evidence_index: dict, frame_ids: list[str]) -> list[dict]:
        targets = {str(frame_id) for frame_id in frame_ids if str(frame_id).strip()}
        if not targets:
            return []
        frame_sources = self._frame_sources_by_id(evidence_index)
        out = []
        for item in evidence_index.get("ocr_items") or []:
            source = str(item.get("source") or "")
            frame_id = source.rsplit(":", 1)[-1] if ":" in source else ""
            if frame_id not in targets:
                continue
            text = self._preferred_text(item)
            if not text:
                continue
            out.append({
                "source": frame_sources.get(frame_id) or self._source_from_timestamp(item.get("timestamp")),
                "text": self._truncate_text(text, settings.fusion_ocr_text_max_chars),
            })
            if len(out) >= VIDEO_CANDIDATE_TEXT_MAX_ITEMS:
                break
        return out

    def _candidate_asr_for_prompt(self, evidence_index: dict, moment: dict) -> list[dict]:
        if not moment:
            return []
        try:
            range_start = float(moment.get("start") or 0.0)
            range_end = float(moment.get("end") if moment.get("end") is not None else range_start)
        except (TypeError, ValueError):
            return []
        out = []
        for seg in evidence_index.get("asr_segments") or []:
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") if seg.get("end") is not None else start)
            except (TypeError, ValueError):
                continue
            if end < range_start or start > range_end:
                continue
            text = self._preferred_text(seg)
            if not text:
                continue
            out.append({
                "source": str(seg.get("audio_source") or f"video_audio:{start:.1f}-{end:.1f}"),
                "text": self._truncate_text(text, 220),
            })
            if len(out) >= VIDEO_CANDIDATE_TEXT_MAX_ITEMS:
                break
        return out

    def _frame_source_for_candidate(self, evidence_index: dict, frame_id: str, fallback_timestamp) -> str:
        frame_sources = self._frame_sources_by_id(evidence_index)
        if frame_id and frame_sources.get(frame_id):
            return frame_sources[frame_id]
        return self._source_from_timestamp(fallback_timestamp)

    @staticmethod
    def _frame_sources_by_id(evidence_index: dict) -> dict[str, str]:
        values = {}
        for frame in evidence_index.get("timeline_frames") or []:
            frame_id = str(frame.get("frame_id") or "").strip()
            source = str(frame.get("video_frame_source") or "").strip()
            if frame_id and source:
                values[frame_id] = source
        return values

    @staticmethod
    def _source_from_timestamp(timestamp) -> str:
        try:
            return f"video_frame:{float(timestamp):.2f}"
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _preferred_text(item: dict) -> str:
        return str(
            item.get("translation_zh")
            or item.get("text_zh")
            or item.get("text")
            or ""
        ).strip()

    def _compact_text_sequence(self, values: list[str], max_chars: int, separator: str = " | ") -> str:
        clean = []
        last = ""
        for value in values:
            text = " ".join(str(value or "").split())
            if not text or text == last:
                continue
            clean.append(text)
            last = text
        if not clean:
            return ""
        combined = separator.join(clean)
        if max_chars <= 0 or len(combined) <= max_chars:
            return combined
        marker = " … "
        half = max(1, (max_chars - len(marker)) // 2)
        head = separator.join(clean[: max(1, len(clean) // 2)])
        tail = separator.join(clean[max(1, len(clean) // 2):])
        head = head[:half].rstrip(separator)
        tail = tail[-half:].lstrip(separator)
        return f"{head}{marker}{tail}"

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
        limit = max(0, settings.fusion_max_comments)
        compact: list[dict] = []
        for comment in comments[:limit]:
            text = self._truncate_text(comment.get("content", ""), settings.fusion_comment_max_chars)
            if not text:
                continue
            comment_id = comment.get("comment_id") or comment.get("id") or ""
            compact.append({
                "comment_id": str(comment_id or ""),
                "nickname": str(comment.get("nickname") or ""),
                "text": text,
            })
        if not compact:
            return "（无评论）"
        payload = {
            "comments": compact,
            "comment_count": len(comments),
            "included_count": len(compact),
        }
        if len(comments) > limit:
            payload["truncated"] = True
            payload["limit"] = limit
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _media_summary_for_comment_audit(self, image_analyses: list[dict], video_results: list[dict]) -> str:
        values: list[str] = []
        for image in image_analyses:
            summary = self._truncate_text(image.get("visual_summary", ""), 100)
            risks = [
                self._truncate_text(item.get("risk_type") or item.get("reason", ""), 40)
                for item in image.get("risk_items") or []
                if isinstance(item, dict)
            ]
            if summary or risks:
                values.append(f"图片：{summary}" + (f"；风险线索：{'、'.join(risks[:3])}" if risks else ""))
        for video in video_results:
            for segment in video.get("segment_reviews") or []:
                analysis = segment.get("analysis") or {}
                summary = self._truncate_text(analysis.get("segment_summary", ""), 100)
                score = self._normalize_risk_score(analysis.get("segment_score"), "none")
                if summary:
                    values.append(f"视频{segment.get('index', '')}段：{summary}（{score}分）")
        return self._truncate_text("\n".join(values) or "未提取到明确媒体摘要", 1200)

    def _audit_comments(self, subject: AuditSubject, media_summary: str) -> list[dict]:
        if not subject.comments:
            return []
        job_store.log(self.job_id, f"笔记 {subject.note_id}：开始逐条评论审核，共 {len(subject.comments)} 条")

        def prepare(index_and_comment: tuple[int, dict]) -> dict:
            index, raw = index_and_comment
            comment = dict(raw)
            comment_id = str(comment.get("comment_id") or comment.get("id") or f"{subject.note_id}:comment:{index + 1}")
            source_text = str(comment.get("content") or comment.get("text") or "").strip()
            comment["comment_id"] = comment_id
            comment["content"] = source_text
            comment["source_text"] = source_text
            existing_translation = str(comment.get("translation_zh") or "").strip()
            if existing_translation:
                comment["translation_zh"] = existing_translation
                comment["translation_status"] = "completed"
                comment["translation_required"] = False
                return comment
            language = str(comment.get("language") or comment.get("lang") or "")
            translation_required = self.translator.should_translate(source_text, language)
            comment["translation_required"] = translation_required
            comment["translation_zh"] = ""
            comment["translation_status"] = "pending" if translation_required else "not_needed"
            return comment

        prepared = [prepare(item) for item in enumerate(subject.comments)]
        translation_required_count = sum(bool(item.get("translation_required")) for item in prepared)
        if translation_required_count:
            job_store.log(
                self.job_id,
                f"笔记 {subject.note_id}：评论审核将由同一次模型调用完成中文翻译，"
                f"需翻译={translation_required_count}",
            )

        audited_by_id: dict[str, dict] = {}
        comments_for_model = []
        for comment in prepared:
            comment_id = str(comment.get("comment_id") or "")
            if str(comment.get("source_text") or "").strip():
                comments_for_model.append(comment)
                continue
            audited_by_id[comment_id] = self._empty_comment_audit_result()

        empty_count = len(prepared) - len(comments_for_model)
        if empty_count:
            job_store.log(
                self.job_id,
                f"笔记 {subject.note_id}：{empty_count} 条空评论已本地完成审核，不调用模型",
            )

        batch_size = max(1, settings.comment_audit_batch_size)
        if self._is_ruleset_v2():
            batches, oversized = self._pack_v2_comment_audit_batches(
                subject,
                media_summary,
                comments_for_model,
                max_batch_size=min(20, batch_size),
            )
            audited_by_id.update(oversized)
        else:
            batches = [
                comments_for_model[offset : offset + batch_size]
                for offset in range(0, len(comments_for_model), batch_size)
            ]

        def audit_batch(index_and_batch: tuple[int, list[dict]]) -> dict[str, dict]:
            batch_index, batch = index_and_batch
            return self._audit_comment_batch_with_fallback(
                subject,
                media_summary,
                batch,
                batch_label=f"{batch_index}/{len(batches)}",
            )

        batch_workers = max(1, min(4, settings.comment_audit_concurrency, len(batches)))
        indexed_batches = list(enumerate(batches, start=1))
        if batch_workers <= 1:
            batch_results = [audit_batch(item) for item in indexed_batches]
        else:
            with ThreadPoolExecutor(max_workers=batch_workers) as executor:
                batch_results = list(executor.map(audit_batch, indexed_batches))
        for result in batch_results:
            audited_by_id.update(result)

        output = []
        for comment in prepared:
            comment_id = str(comment.get("comment_id") or "")
            audit = audited_by_id.get(comment_id) or {
                "audit_status": "failed",
                "audit_error": "comment result missing after retries",
            }
            merged = {**comment, **audit}
            merged.pop("translation_required", None)
            output.append(merged)
        output.sort(
            key=lambda item: (
                item.get("audit_status") == "completed",
                int(item.get("risk_score") or -1),
            ),
            reverse=True,
        )
        stats = self._comment_audit_stats(output)
        job_store.log(
            self.job_id,
            f"笔记 {subject.note_id}：逐条评论审核完成，成功={stats['completed']}，失败={stats['failed']}，"
            f"达到复核阈值={stats['review_count']}，需翻译={stats['translation_required']}，"
            f"翻译成功={stats['translation_completed']}，翻译失败={stats['translation_failed']}",
            level="info" if stats["failed"] == 0 else "warning",
            reason="" if stats["failed"] == 0 else "部分评论审核或翻译未完成",
            error_code="" if stats["failed"] == 0 else "comment_audit_partial",
            retryable=False if stats["failed"] else None,
            action="exclude_failed_comments" if stats["failed"] else "",
        )
        return output

    def _pack_v2_comment_audit_batches(
        self,
        subject: AuditSubject,
        media_summary: str,
        comments: list[dict],
        *,
        max_batch_size: int,
    ) -> tuple[list[list[dict]], dict[str, dict]]:
        batches: list[list[dict]] = []
        oversized: dict[str, dict] = {}
        current: list[dict] = []
        batch_limit = max(1, min(20, max_batch_size))

        for comment in comments:
            if len(current) >= batch_limit:
                batches.append(current)
                current = []

            candidate = [*current, comment]
            try:
                self._render_comment_audit_prompt(subject, media_summary, candidate)
            except FusionAuditContractError as exc:
                if current:
                    batches.append(current)
                    current = []
                    try:
                        self._render_comment_audit_prompt(
                            subject,
                            media_summary,
                            [comment],
                        )
                    except FusionAuditContractError as single_exc:
                        oversized.update(
                            self._oversized_comment_audit_result(comment, single_exc)
                        )
                    else:
                        current = [comment]
                else:
                    oversized.update(
                        self._oversized_comment_audit_result(comment, exc)
                    )
            else:
                current = candidate

        if current:
            batches.append(current)
        return batches, oversized

    def _oversized_comment_audit_result(
        self,
        comment: dict,
        error: Exception,
    ) -> dict[str, dict]:
        comment_id = str(comment.get("comment_id") or "")
        result = {
            "audit_status": "failed",
            "audit_error": self._truncate_text(
                f"single comment cannot fit V2 prompt budget: {error}",
                180,
            ),
        }
        if comment.get("translation_required") and not comment.get("translation_zh"):
            result.update({
                "translation_status": "failed",
                "translation_error": "comment was not sent because its prompt exceeded the budget",
            })
        job_store.log(
            self.job_id,
            f"评论 {comment_id} 在保留 source_text/translation_zh 各最多300字后仍超过 "
            "V2 Prompt 总预算，已仅标记该条审核失败",
            level="error",
            reason="单条评论超过审核提示词预算",
            error_code="comment_prompt_budget_exceeded",
            retryable=False,
            action="exclude_comment",
        )
        return {comment_id: result}

    def _audit_comment_batch_with_fallback(
        self,
        subject: AuditSubject,
        media_summary: str,
        comments: list[dict],
        *,
        batch_label: str = "1/1",
    ) -> dict[str, dict]:
        if not comments:
            return {}
        normalized: dict[str, dict] = {}
        pending = list(comments)
        error = "model omitted comment result"
        errors_by_id: dict[str, str] = {}

        for attempt in (1, 2):
            round_requested = list(pending)
            request_batches = (
                [round_requested]
                if attempt == 1
                else [round_requested[offset : offset + 10] for offset in range(0, len(round_requested), 10)]
            )
            if attempt == 2 and len(request_batches) > 1:
                job_store.log(
                    self.job_id,
                    f"笔记 {subject.note_id}：评论审核批次 {batch_label} 补偿轮次拆分为"
                    f"{len(request_batches)} 个子批次，每批最多10条",
                )
            round_completed: dict[str, dict] = {}
            for sub_batch_index, requested in enumerate(request_batches, start=1):
                started_at = perf_counter()
                response_diagnostic = ""
                raw = {}
                sub_batch_label = (
                    f"，子批次={sub_batch_index}/{len(request_batches)}"
                    if len(request_batches) > 1
                    else ""
                )
                inference_options = self._text_inference_options("comment_audit")
                job_store.log(
                    self.job_id,
                    f"笔记 {subject.note_id}：评论审核批次 {batch_label} 模型调用 {attempt}/2"
                    f"{sub_batch_label} 开始，thinking={'on' if inference_options['enable_thinking'] else 'off'}，评论={len(requested)}",
                )
                try:
                    prompt = self._render_comment_audit_prompt(subject, media_summary, requested)
                    raw = self.qwen.audit_text(
                        prompt,
                        **inference_options,
                    )
                    raw_response = str(raw.get("raw_response") or "")
                    llm_meta = raw.get("_llm_meta") if isinstance(raw.get("_llm_meta"), dict) else {}
                    if llm_meta:
                        response_diagnostic = (
                            f"，finish_reason={llm_meta.get('finish_reason', 'unknown')}"
                            f"，input_tokens={llm_meta.get('prompt_tokens', 'unknown')}"
                            f"，output_tokens={llm_meta.get('completion_tokens', 'unknown')}"
                        )
                    if raw_response:
                        current = {}
                        error = (
                            "comment audit returned incomplete or invalid JSON "
                            f"(response_chars={len(raw_response)}, "
                            f"finish_reason={llm_meta.get('finish_reason', 'unknown')}, "
                            f"completion_tokens={llm_meta.get('completion_tokens', 'unknown')})"
                        )
                        response_diagnostic += f"，JSON解析失败，响应字符={len(raw_response)}"
                    else:
                        decoded = self._decode_comment_ids(raw, requested)
                        if self._is_ruleset_v2():
                            decoded = self._decode_comment_rule_codes(
                                decoded,
                                self._comment_rule_code_mapping(),
                            )
                        current = self._normalize_comment_audit_results(decoded, requested)
                        error = "model omitted comment result"
                except QwenTimeoutError as exc:
                    # The client already used the one allowed timeout retry.
                    # Do not turn this into another split-batch compensation round.
                    job_store.log(
                        self.job_id,
                        "评论审核请求连续两次超时，停止重试",
                        level="error",
                        reason="评论审核请求超时且重试已耗尽",
                        error_code="comment_audit_timeout",
                        retryable=False,
                        action="stop_comment_audit",
                    )
                    raise AuditProviderCallError("text Provider request timed out after one retry") from exc
                except QwenProviderError as exc:
                    raise AuditProviderCallError("comment Provider request failed") from exc
                except Exception as exc:
                    current = {}
                    error = str(exc)

                normalized.update(current)
                round_completed.update(current)
                for comment in requested:
                    comment_id = str(comment.get("comment_id") or "")
                    if comment_id not in current:
                        errors_by_id[comment_id] = error
                missing_count = sum(
                    str(comment.get("comment_id") or "") not in current
                    for comment in requested
                )
                if missing_count:
                    self._write_comment_id_failure(subject, requested, raw, attempt, batch_label)
                elapsed = perf_counter() - started_at
                job_store.log(
                    self.job_id,
                    f"笔记 {subject.note_id}：评论审核批次 {batch_label} 模型调用 {attempt}/2"
                    f"{sub_batch_label} 完成，返回有效={len(current)}，漏项={missing_count}，"
                    f"耗时={elapsed:.1f}s{response_diagnostic}",
                )

            pending = [
                comment
                for comment in round_requested
                if str(comment.get("comment_id") or "") not in round_completed
            ]
            if not pending:
                break
            if attempt == 1:
                job_store.log(
                    self.job_id,
                    f"笔记 {subject.note_id}：评论审核批次 {batch_label} 首次结果漏项={len(pending)}，"
                    "仅补偿重试一次",
                    level="warning",
                    reason="模型首次结果缺少部分评论",
                    error_code="comment_audit_incomplete",
                    retryable=True,
                    action="retry_missing_comments",
                )

        for comment in pending:
            comment_id = str(comment.get("comment_id") or "")
            normalized[comment_id] = {
                "audit_status": "failed",
                "audit_error": self._truncate_text(
                    f"{errors_by_id.get(comment_id, error)} after one retry",
                    180,
                ),
            }
            if comment.get("translation_required") and not comment.get("translation_zh"):
                normalized[comment_id].update({
                    "translation_status": "failed",
                    "translation_error": "comment translation missing after one retry",
                })
        if pending:
            job_store.log(
                self.job_id,
                f"笔记 {subject.note_id}：评论审核批次 {batch_label} 补偿重试后仍漏项={len(pending)}，"
                "已标记审核失败，不再继续重试",
                level="error",
                reason="补偿重试后仍缺少评论审核结果",
                error_code="comment_audit_retry_exhausted",
                retryable=False,
                action="mark_missing_comments_failed",
            )
        return normalized

    @staticmethod
    def _comment_id_instructions() -> str:
        return ("\n评论编号约束：输入 comment_id 是本次请求内短编号 C01、C02 等。"
                "输出 id 只能逐字选择本次给定编号，每个编号恰好一次；不得输出平台长 ID、"
                "猜测编号或按输出顺序省略编号。补试请求的编号以该次输入为准。\n")

    def _comment_rule_code_mapping(self) -> dict[str, str]:
        """Bind short model-facing codes to this request's frozen comment rules."""
        routes = self.rule_snapshot.get("stage_routes") or {}
        rule_ids = list(dict.fromkeys(routes.get("comment_audit") or []))
        return {f"CR{index:02d}": rule_id for index, rule_id in enumerate(rule_ids, start=1)}

    @staticmethod
    def _decode_comment_rule_codes(raw: dict, mapping: dict[str, str]) -> dict:
        """Restore stable rule IDs after validating model-facing comment codes."""
        decoded = dict(raw)
        rows = raw.get("comments") or raw.get("results") or raw.get("comment_results") or []
        if not isinstance(rows, list):
            decoded["comments"] = []
            return decoded
        restored_rows = []
        for original in rows:
            if not isinstance(original, dict):
                continue
            item = dict(original)
            code = str(item.get("rule_id") or item.get("rid") or "").strip()
            if code:
                if code not in mapping:
                    raise FusionAuditContractError(
                        f"comment_audit has an unknown rule code: {code!r}"
                    )
                item["rule_id"] = mapping[code]
                if "rid" in item:
                    item["rid"] = mapping[code]
            restored_rows.append(item)
        decoded["comments"] = restored_rows
        return decoded

    @staticmethod
    def _decode_comment_ids(raw: dict, comments: list[dict]) -> dict:
        mapping = {f"C{index:02d}": str(c["comment_id"]) for index, c in enumerate(comments, 1)}
        rows = raw.get("comments") or raw.get("results") or raw.get("comment_results") or []
        if not isinstance(rows, list):
            return {"comments": []}
        counts: dict[str, int] = {}
        for row in rows:
            if isinstance(row, dict):
                alias = str(row.get("id") or row.get("comment_id") or "")
                counts[alias] = counts.get(alias, 0) + 1
        decoded = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            alias = str(row.get("id") or row.get("comment_id") or "")
            if alias not in mapping or counts[alias] != 1:
                continue
            if row.get("id") and row.get("comment_id") and row["id"] != row["comment_id"]:
                continue
            restored = dict(row, id=mapping[alias])
            if "comment_id" in restored:
                restored["comment_id"] = mapping[alias]
            decoded.append(restored)
        return {"comments": decoded}

    def _write_comment_id_failure(self, subject, comments, raw, attempt, batch_label):
        folder = settings.outputs_dir / self.job_id / "comment_audit_failures"
        folder.mkdir(parents=True, exist_ok=True)
        record = {"note_id": subject.note_id, "batch": batch_label, "attempt": attempt,
                  "mapping": {f"C{i:02d}": c["comment_id"] for i, c in enumerate(comments, 1)},
                  "rule_code_mapping": (
                      self._comment_rule_code_mapping()
                      if self._is_ruleset_v2()
                      else {}
                  ),
                  "response": raw}
        (folder / f"{subject.note_id}-{time_ns()}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _empty_comment_audit_result() -> dict:
        return {
            "audit_status": "completed",
            "audit_source": "local_empty",
            "risk_score": 0,
            "risk_level": "none",
            "risk_library_id": "",
            "risk_library_label": "",
            "secondary_library_ids": [],
            "risk_type": "",
            "risk_basis": "无有效评论文本",
            "exemption_basis": "评论内容为空",
            "evidence_quote": "",
        }

    def _render_comment_audit_prompt(
        self,
        subject: AuditSubject,
        media_summary: str,
        comments: list[dict],
    ) -> str:
        def comment_text(value) -> str:
            if self._is_ruleset_v2():
                return self._comment_prompt_text(value)
            return self._truncate_text(value, 300)

        payload = {
            "post_context": {
                "title": self._truncate_text(subject.title, 180),
                "desc": self._truncate_text(subject.desc, 360),
                "media_type": "video" if subject.local_video_paths or subject.video_urls else "image",
                "media_summary": media_summary,
            },
            "audit_policy": self._compact_audit_policy(),
            "library_policies": self._active_library_policies(["comment"]),
            "comments": [
                {
                    "comment_id": f"C{index:02d}",
                    "source_text": comment_text(comment.get("source_text", "")),
                    "translation_required": bool(comment.get("translation_required")),
                    **(
                        {"translation_zh": comment_text(comment.get("translation_zh", ""))}
                        if comment.get("translation_zh")
                        else {}
                    ),
                }
                for index, comment in enumerate(comments, 1)
            ],
        }
        comment_template = str(
            (getattr(self, "prompt_profile_snapshot", {}) or {}).get(
                "comment_prompt_template"
            )
            or ""
        )
        if self._is_ruleset_v2() and comment_template:
            mapping = self._comment_rule_code_mapping()
            reverse = {value: key for key, value in mapping.items()}
            fixed_prompt = comment_template + self._comment_id_instructions()
            if reverse:
                pattern = (
                    r"(?<![A-Za-z0-9_.:-])(?:"
                    + "|".join(re.escape(value) for value in sorted(reverse, key=len, reverse=True))
                    + r")(?![A-Za-z0-9_.:-])"
                )
                fixed_prompt = re.sub(
                    pattern,
                    lambda match: reverse[match.group()],
                    fixed_prompt,
                )
            fixed_prompt = fixed_prompt.replace("stable rule_id", "本次短编号，例如CR01")
            fixed_prompt += (
                "\n规则编号协议 comment-rule-codes-v1：每个风险项的 rule_id 只能从下列短编号中准确选择，"
                "禁止自造、拼接、猜测或输出完整规则ID。必须满足该编号对应规则的必要条件；编号正确不代表风险成立，"
                "无明确风险时对应评论的 risk_level 应为 none 且不要填写规则编号。"
                "comment_id 使用 C01、C02 等评论编号，不能与规则编号混用。\n"
                + json.dumps({"allowed_rule_codes": list(mapping)}, ensure_ascii=False)
            )
            return self._render_v2_json_prompt(
                fixed_prompt,
                payload,
                limit=settings.comment_audit_prompt_max_chars,
                kind="comment",
            )
        return (
            "你是评论区逐条审核器。必须结合帖子标题、正文和媒体摘要，独立判断每一条评论，"
            "不要把多条评论聚成一个结论；一条评论可以独立触发召回。"
            "只能按 library_policies 中的风险库判断，引用、反讽、批判、否定、举报和正常讨论应在豁免依据中体现。\n"
            "仇恨歧视边界：以当前评论为主要依据；帖子上下文仅用于解析明确指代和确认内容中明确呈现的受保护身份，"
            "不得依据账号 IP/发布地、使用语言、昵称、服饰等弱线索或其他评论推断身份。区分身份攻击与行为评价："
            "对具体行为、事件或个人表现的批评，不因当事人的身份背景自动构成仇恨歧视；但对身份已明确的受保护群体"
            "或其成员实施强侮辱、非人化、性羞辱、排斥或威胁，仍应召回。\n"
            "语义与等级：只依据原文或可靠译文判断，不得补写原文没有的因果、谣言、排斥或煽动意图。"
            "疑问、转述或个人观察不自动违规，也不自动豁免，应按其实际表达判断。身份指向、翻译、因果或意图不确定时"
            "最多按低风险召回；高风险须有明确非人化、恶性谣言、暴力、驱逐或权利剥夺等强证据。"
            "仅因身份并置、疑问或观察而低风险召回时，t/rb 应写身份关联存疑或因果不明，不得标为恶性谣言或煽动。"
            "t/rb 必须与 q 及可靠译文一致，不得使用证据无法支持的结论。\n"
            "只输出合法 JSON，使用稀疏字段：\n"
            "{\"comments\":[{\"id\":\"原comment_id\",\"s\":0,\"zh\":\"仅需翻译时\"},"
            "{\"id\":\"原comment_id\",\"s\":80,\"lib\":\"主风险库id\","
            "\"sec\":[\"次风险库id\"],\"t\":\"短类别\",\"rb\":\"违规依据\","
            "\"eb\":\"仅有真实豁免时\",\"q\":\"最短关键原句\",\"zh\":\"仅需翻译时\"}]}\n"
            "字段含义：id=comment_id，s=score 0-100，lib=primary risk_library_id，sec=secondary_library_ids，"
            "t=risk_type，rb=risk_basis，eb=exemption_basis，q=evidence_quote，zh=translation_zh。\n"
            "要求：每个输入 comment_id 必须且只能输出一次。s=0 时只输出 id、s，"
            "translation_required=true 时再加 zh；不要输出 lib/sec/t/rb/eb/q。"
            "s>0 时必须输出 id/s/lib/t/rb/q；sec 仅在确有次风险库时输出，eb 仅在确有豁免或降级语境时输出。"
            "rb 不超过18字，eb 不超过14字；q 只能摘自 source_text，禁止改写，并保持最短。"
            "translation_required=true 时必须输出准确、自然、完整的 zh；"
            "translation_required=false 时省略 zh。不要输出空字符串、空数组或占位依据。\n"
            + self._comment_id_instructions()
            + "输入 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    @staticmethod
    def _comment_prompt_text(value) -> str:
        return str(value or "").strip()[:300]

    def _render_v2_json_prompt(
        self,
        fixed_prompt: str,
        payload: dict,
        *,
        limit: int,
        kind: str,
    ) -> str:
        separator = "\n输入 JSON：\n"
        fixed_size = len(fixed_prompt) + len(separator)
        if limit > 0 and fixed_size > limit:
            raise FusionAuditContractError(
                f"{kind} fixed prompt exceeds runtime budget: {fixed_size}/{limit}"
            )
        value = json.loads(json.dumps(payload, ensure_ascii=False))

        def render() -> str:
            if kind == "fusion":
                # Recheck after each budget reduction: the shortened catalog is
                # the only set of IDs the model can legally select.
                visible_ids = {item.get("evidence_id") for item in value.get("evidence_catalog") or []}
                value["top_comments"] = [
                    item for item in value.get("top_comments") or []
                    if item.get("evidence_id") in visible_ids
                ]
            return fixed_prompt + separator + json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        prompt = render()
        if limit <= 0 or len(prompt) <= limit:
            return prompt
        original_size = len(prompt)
        if kind == "comment":
            post = value.get("post_context") or {}
            for text_limit in (220, 140, 90, 50, 24):
                post["title"] = self._truncate_text(post.get("title", ""), text_limit)
                post["desc"] = self._truncate_text(post.get("desc", ""), text_limit * 2)
                post["media_summary"] = self._truncate_text(
                    post.get("media_summary", ""), text_limit * 2
                )
                prompt = render()
                if len(prompt) <= limit:
                    break
        elif kind == "fusion":
            for keep_comments, keep_media, evidence_text, keep_evidence in (
                (10, 8, 120, 120),
                (5, 4, 80, 80),
                (2, 2, 48, 50),
                (0, 0, 24, 30),
            ):
                value["top_comments"] = (value.get("top_comments") or [])[:keep_comments]
                value["media_summaries"] = (value.get("media_summaries") or [])[:keep_media]
                compact_catalog = []
                for item in (value.get("evidence_catalog") or [])[:keep_evidence]:
                    compact = dict(item)
                    for key in (
                        "text",
                        "ocr_text_zh",
                        "translation_zh",
                        "visual_summary",
                        "reason",
                    ):
                        if compact.get(key):
                            compact[key] = self._truncate_text(compact[key], evidence_text)
                    compact_catalog.append(compact)
                value["evidence_catalog"] = compact_catalog
                post = value.get("post") or {}
                post["title"] = self._truncate_text(post.get("title", ""), evidence_text * 2)
                post["title_zh"] = self._truncate_text(post.get("title_zh", ""), evidence_text * 2)
                post["desc"] = self._truncate_text(post.get("desc", ""), evidence_text * 4)
                post["desc_zh"] = self._truncate_text(post.get("desc_zh", ""), evidence_text * 4)
                prompt = render()
                if len(prompt) <= limit:
                    break
        elif kind == "frame":
            text_keys = {
                "text",
                "text_zh",
                "source_text",
                "source_text_dolphin",
                "source_text_mms",
                "translation_zh",
                "summary",
                "description",
                "audit_goal",
            }

            def trim_frame_text(node, text_limit: int):
                if isinstance(node, list):
                    return [trim_frame_text(item, text_limit) for item in node]
                if not isinstance(node, dict):
                    return node
                return {
                    key: (
                        self._truncate_text(item, text_limit)
                        if key in text_keys and isinstance(item, str)
                        else trim_frame_text(item, text_limit)
                    )
                    for key, item in node.items()
                }

            for keep_chunks, text_limit in ((16, 180), (12, 120), (8, 80), (4, 48)):
                value["title"] = self._truncate_text(value.get("title", ""), text_limit)
                value["desc"] = self._truncate_text(value.get("desc", ""), text_limit * 2)
                value["ocr_chunks"] = trim_frame_text(
                    (value.get("ocr_chunks") or [])[:keep_chunks],
                    text_limit,
                )
                value["asr_chunks"] = trim_frame_text(
                    (value.get("asr_chunks") or [])[:keep_chunks],
                    text_limit,
                )
                prompt = render()
                if len(prompt) <= limit:
                    break
        if len(prompt) > limit:
            raise FusionAuditContractError(
                f"{kind} dynamic input cannot fit prompt budget: {len(prompt)}/{limit}"
            )
        job_store.log(
            self.job_id,
            f"{kind} Prompt 超过总长度上限，已裁剪动态输入："
            f"{original_size} -> {len(prompt)} / {limit} 字符",
        )
        return prompt

    def _normalize_comment_audit_results(self, raw: dict, comments: list[dict]) -> dict[str, dict]:
        valid = {
            str(comment.get("comment_id") or ""): comment
            for comment in comments
            if comment.get("comment_id")
        }
        rows = raw.get("comments") or raw.get("results") or raw.get("comment_results") or []
        if not isinstance(rows, list):
            return {}
        thresholds = self._active_thresholds()
        output: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            comment_id = str(row.get("id") or row.get("comment_id") or "")
            source = valid.get(comment_id)
            if not source or comment_id in output:
                continue
            try:
                raw_score = row["s"] if "s" in row else row["score"]
                float(raw_score)
            except (KeyError, TypeError, ValueError):
                continue
            score = self._normalize_risk_score(raw_score, "none")
            v2_risk_level = "none"
            if self._is_ruleset_v2():
                try:
                    v2_risk_level = self._strict_v2_risk_level(
                        row.get("risk_level"),
                        field="comment_audit.risk_level",
                        allow_none=True,
                    )
                except FusionAuditContractError:
                    continue
            rule_id = self._normalize_stage_rule_id(
                row.get("rule_id") or row.get("rid"),
                "comment_audit",
            )
            if self._is_ruleset_v2() and v2_risk_level != "none" and not rule_id:
                continue
            matched_exemption_ids = self._normalize_matched_exemption_ids(
                rule_id,
                row.get("matched_exemption_ids"),
            )
            if self._is_ruleset_v2():
                if matched_exemption_ids:
                    v2_risk_level = "none"
                score = self._risk_score_for_level(v2_risk_level)
            elif matched_exemption_ids:
                score = 0
            quote = self._truncate_text(row.get("q") or row.get("evidence_quote", ""), 80)
            source_text = str(source.get("source_text") or "")
            if quote and quote not in source_text:
                quote = ""
            existing_translation = str(source.get("translation_zh") or "").strip()
            translation_zh = self._truncate_text(
                row.get("zh") or row.get("translation_zh") or existing_translation,
                300,
            )
            translation_required = bool(source.get("translation_required"))
            if translation_required and not translation_zh:
                continue
            risk_library_id = self._normalize_comment_library_id(row.get("lib") or row.get("risk_library_id"), score)
            policy = self._library_policy_by_id(risk_library_id)
            risk_library_label = self._truncate_text(
                policy.get("title")
                or row.get("risk_library_label")
                or risk_library_id,
                50,
            )
            if not risk_library_id:
                risk_library_label = ""
            try:
                risk_type = self._normalize_risk_type(row.get("t") or row.get("risk_type", ""), 50)
            except FusionAuditContractError:
                continue
            risk_basis = self._truncate_text(row.get("rb") or row.get("risk_basis", ""), 30)
            if self._is_ruleset_v2() and v2_risk_level != "none" and (
                not risk_library_id or not risk_type or not risk_basis or not quote
            ):
                continue
            secondary_library_ids = self._normalize_secondary_library_ids(
                row.get("sec") if "sec" in row else row.get("secondary_library_ids"),
                primary=risk_library_id,
            )
            normalized = {
                "audit_status": "completed",
                "risk_score": score,
                "risk_level": (
                    v2_risk_level
                    if self._is_ruleset_v2()
                    else self._level_from_score(score, thresholds)
                ),
                "risk_library_id": risk_library_id,
                "risk_library_label": risk_library_label,
                "secondary_library_ids": secondary_library_ids,
                "risk_type": risk_type,
                "risk_basis": risk_basis,
                "exemption_basis": self._truncate_text(row.get("eb") or row.get("exemption_basis", ""), 30),
                "evidence_quote": quote,
                "translation_zh": translation_zh,
                "translation_status": "completed" if translation_zh else "not_needed",
            }
            if self._is_ruleset_v2():
                normalized["rule_id"] = rule_id
                normalized["matched_exemption_ids"] = matched_exemption_ids
            if self._is_ruleset_v2() and score == 0:
                normalized.update({
                    "risk_library_id": "",
                    "risk_library_label": "",
                    "secondary_library_ids": [],
                    "risk_type": "",
                    "rule_id": "",
                    "risk_basis": "",
                    "evidence_quote": "",
                })
            output[comment_id] = normalized
        return output

    def _normalize_comment_library_id(self, value, score: int) -> str:
        if int(score or 0) <= 0:
            return ""
        target = str(value or "").strip()
        if not target:
            return ""
        valid = {
            str(policy.get("id") or "").strip()
            for policy in self._active_library_policies(["comment"])
            if policy.get("id")
        }
        return target if target in valid else ""

    def _normalize_secondary_library_ids(self, value, *, primary: str = "") -> list[str]:
        values = value if isinstance(value, list) else ([value] if value not in (None, "", [], {}) else [])
        valid = {
            str(policy.get("id") or "").strip()
            for policy in self._active_library_policies(["comment"])
            if policy.get("id")
        }
        out = []
        for item in values:
            library_id = str(item or "").strip()
            if library_id and library_id != primary and library_id in valid and library_id not in out:
                out.append(library_id)
        return out[:4]

    def _comment_audit_stats(self, comments: list[dict]) -> dict:
        thresholds = self._active_thresholds()
        review_threshold = int(thresholds.get("review", 40))
        completed = [item for item in comments if item.get("audit_status") == "completed"]
        rule_counts: dict[str, int] = {}
        for item in completed:
            rule_id = str(item.get("rule_id") or "")
            if int(item.get("risk_score") or 0) > 0 and rule_id:
                rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1
        return {
            "total": len(comments),
            "completed": len(completed),
            "failed": len(comments) - len(completed),
            "translation_required": sum(
                item.get("translation_status") in {"completed", "failed"}
                for item in comments
                if item.get("translation_zh") or item.get("translation_error")
            ),
            "translation_completed": sum(item.get("translation_status") == "completed" for item in comments),
            "translation_failed": sum(item.get("translation_status") == "failed" for item in comments),
            "review_count": sum(int(item.get("risk_score") or 0) >= review_threshold for item in completed),
            "high_count": sum(item.get("risk_level") == "high" for item in completed),
            "max_score": max((int(item.get("risk_score") or 0) for item in completed), default=0),
            "level_counts": {level: sum(item.get("risk_level") == level for item in completed)
                             for level in ("none", "low", "medium", "high")},
            "rule_counts": rule_counts,
        }

    def _compact_audit_policy(self) -> dict:
        if self._is_ruleset_v2():
            return {
                "category": getattr(self.prompt_set, "category", "")
                if hasattr(self, "prompt_set")
                else "",
            }
        return {
            "category": getattr(self.prompt_set, "category", "") if hasattr(self, "prompt_set") else "",
            "thresholds": self._active_thresholds(),
            "rules": [
                {
                    "rule_id": rule.get("id") or rule.get("rule_id"),
                    "label": rule.get("label") or rule.get("rule"),
                    "category": rule.get("category") or rule.get("library_id"),
                    "source": rule.get("source"),
                    "score": rule.get("score"),
                }
                for rule in self._active_scoring_rules()[:40]
            ],
        }

    def _comment_evidence_items(self, comments: list[dict]) -> list[dict]:
        review_threshold = int(self._active_thresholds().get("review", 40))
        out = []
        for comment in comments:
            if comment.get("audit_status") != "completed":
                continue
            score = int(comment.get("risk_score") or 0)
            if (
                score <= 0
                if self._is_ruleset_v2()
                else score < review_threshold
            ):
                continue
            comment_id = str(comment.get("comment_id") or "")
            item = {
                "evidence_id": f"comment:{comment_id}",
                "id": f"comment:{comment_id}",
                "primary_modality": "comment",
                "modality": "comment",
                "source": f"comment:{comment_id}",
                "source_label": "评论",
                "comment_id": comment_id,
                "nickname": comment.get("nickname", ""),
                "text": comment.get("source_text") or comment.get("content") or "",
                "translation_zh": comment.get("translation_zh", ""),
                "risk_library_id": comment.get("risk_library_id", ""),
                "risk_library_label": comment.get("risk_library_label", ""),
                "secondary_library_ids": comment.get("secondary_library_ids") or [],
                "risk_type": comment.get("risk_type", ""),
                "reason": comment.get("risk_basis", ""),
                "exemption_basis": comment.get("exemption_basis", ""),
                "evidence_quote": comment.get("evidence_quote", ""),
                "risk_score": score,
                "evidence_risk_level": comment.get("risk_level", "none"),
            }
            if self._is_ruleset_v2():
                item["rule_id"] = comment.get("rule_id", "")
            out.append(item)
        return out

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
                risk_items = self._compact_risk_items(
                    frame.get("risk_items") or [],
                    application_stage="video_frame_evidence",
                )
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

    def _compact_risk_items(
        self,
        risk_items: list[dict],
        *,
        application_stage: str = "",
    ) -> list[dict]:
        compact: list[dict] = []
        values = (
            self._filter_stage_risk_items(risk_items, application_stage)
            if application_stage
            else [item for item in risk_items if isinstance(item, dict)]
        )
        ordered = sorted(values, key=lambda item: _severity_rank(item.get("severity")), reverse=True)
        for item in ordered[: max(0, settings.fusion_frame_max_risk_items)]:
            row = {
                "severity": item.get("severity", ""),
                "risk_library_id": self._truncate_text(item.get("risk_library_id", ""), 40),
                "risk_library_label": self._truncate_text(item.get("risk_library_label", ""), 40),
                "risk_type": self._normalize_risk_type(item.get("risk_type", ""), 80),
                "evidence": self._truncate_text(item.get("evidence", ""), 140),
                "reason": self._truncate_text(item.get("reason", ""), 160),
            }
            rule_id = self._truncate_text(item.get("rule_id", ""), 120)
            if self._is_ruleset_v2() and rule_id:
                row["rule_id"] = rule_id
            compact.append(row)
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

    def _normalize_risk_type(self, value, legacy_max_chars: int) -> str:
        profile = getattr(self, "prompt_profile_snapshot", {}) or {}
        content_driven = self._is_ruleset_v2() and any(
            isinstance(library, dict) and library.get("ruleset_content_driven") is True
            for library in profile.get("libraries") or []
        )
        if not content_driven:
            return self._truncate_text(value, legacy_max_chars)
        # Category identities must never pass through display normalization.
        if not isinstance(value, str) or len(value) > 100:
            raise FusionAuditContractError("risk_type must be a category identity of at most 100 characters")
        return value

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
        extra_instruction = (
            "\n\n新视频证据结构说明：视频关键帧分析字段现在是紧凑 JSON，包含 "
            "video_text_context（全局 OCR/ASR 主旨文本）、video_visual_context（按 moment summary 拼接的整体画面理解）"
            "和 candidate_context（局部粗筛/精筛候选）。"
            "最终 evidence.source 可引用 image:<index>、video:<v>/moment:<m>、"
            "video_frame:<timestamp>、video_audio:<start-end>、comment:<id>。"
            "candidate_context 是局部候选，天然偏风险；最终判断必须优先结合标题、正文、"
            "video_text_context 和 video_visual_context 的整体主旨重新校准。"
            "如果只有 Moment 粗审怀疑、没有 Precise Sheet 明确证据，不要直接 reject/high，"
            "优先输出 review 或 low，并说明需要复核。"
            "若整体语境是揭秘、科普、反诈/反赌、违法犯罪警示、不能做或被骗曝光，"
            "且没有联系方式、群入口、二维码、平台入口、收益承诺、招募带做或接单交易路径，"
            "不要仅凭候选 OCR/ASR 中的黑话或流程词判 high/reject。"
        )
        prompt = self._compose_fusion_prompt(replacements, extra_instruction)
        limit = settings.fusion_prompt_max_chars
        if limit > 0 and len(prompt) > limit:
            original_len = len(prompt)
            prompt = self._trim_fusion_prompt_to_limit(replacements, extra_instruction, limit)
            job_store.log(
                self.job_id,
                f"融合 Prompt 超过总长度上限，已压缩：{original_len} -> {len(prompt)} / {limit} 字符",
            )
        return prompt

    @staticmethod
    def _fusion_visible_ids(prompt: str) -> set[str]:
        payload = json.loads(prompt.split("\n输入 JSON：\n", 1)[1])
        return {str(item["evidence_id"]) for item in payload.get("evidence_catalog") or [] if item.get("evidence_id")}

    @staticmethod
    def _representative_risk_comments(comments: list[dict], limit: int) -> list[dict]:
        ordered = sorted(comments, key=lambda item: int(item.get("risk_score") or 0), reverse=True)
        selected, remainder, groups = [], [], set()
        for item in ordered:
            group = (item.get("risk_level"), item.get("rule_id"))
            if group not in groups:
                groups.add(group)
                selected.append(item)
            else:
                remainder.append(item)
        return (selected + remainder)[:max(0, limit)]

    def _render_compact_fusion_prompt(
        self,
        subject: AuditSubject,
        evidence_index: dict,
        comments: list[dict],
    ) -> str:
        catalog = evidence_index.get("evidence_catalog") or []
        compact_catalog = []
        for item in catalog:
            compact_catalog.append({
                key: item.get(key)
                for key in (
                    "evidence_id",
                    "source",
                    "primary_modality",
                    "risk_library_id",
                    "risk_library_label",
                    "secondary_library_ids",
                    "rule_id",
                    "risk_type",
                    "reason",
                    "risk_score",
                    "evidence_risk_level",
                    "comment_id",
                    "text",
                    "ocr_text_zh",
                    "translation_zh",
                    "visual_summary",
                    "start",
                    "end",
                )
                if item.get(key) not in (None, "", [], {})
            })
        media_summaries = [
            {
                "source": item.get("source"),
                "summary": self._truncate_text(item.get("visual_summary", ""), 120),
                "benign_context": self._truncate_text(item.get("benign_context", ""), 100),
            }
            for item in evidence_index.get("image_units") or []
            if item.get("visual_summary") or item.get("benign_context")
        ]
        media_summaries.extend({
            "source": item.get("source"),
            "summary": self._truncate_text(item.get("segment_summary", ""), 120),
            "score": item.get("segment_score", 0),
        } for item in evidence_index.get("segment_reviews") or [])
        catalog_ids = {entry.get("evidence_id") for entry in catalog}
        completed_comments = [
            item for item in comments if item.get("audit_status") == "completed"
            and (
                not self._is_ruleset_v2()
                or (
                    int(item.get("risk_score") or 0) > 0
                    and f"comment:{item.get('comment_id')}" in catalog_ids
                )
            )
        ]
        top_comments = sorted(
            completed_comments,
            key=lambda item: int(item.get("risk_score") or 0),
            reverse=True,
        )[: max(0, settings.comment_fusion_top_k)]
        if self._is_ruleset_v2():
            top_comments = self._representative_risk_comments(completed_comments, settings.comment_fusion_top_k)
            representative_ids = {f"comment:{item.get('comment_id')}" for item in top_comments}
            comment_catalog = {item["evidence_id"]: item for item in compact_catalog
                               if item.get("evidence_id") in representative_ids}
            # Only representative risk comments enter either copy of the prompt.
            # The complete evidence index on disk is not shortened.
            compact_catalog = [comment_catalog[f"comment:{item.get('comment_id')}"] for item in top_comments] + [
                item for item in compact_catalog if not str(item.get("evidence_id", "")).startswith("comment:")
            ]
        scoring_rules = [
            {
                "rule_id": rule.get("id") or rule.get("rule_id"),
                "label": rule.get("label") or rule.get("rule"),
                "category": rule.get("category") or rule.get("library_id"),
                "score": rule.get("score"),
            }
            for rule in self._active_scoring_rules()
        ]
        payload = {
            "post": {
                "title": self._truncate_text(subject.title, 240),
                "title_zh": self._truncate_text(subject.title_zh, 240),
                "desc": self._truncate_text(subject.desc, 600),
                "desc_zh": self._truncate_text(subject.desc_zh, 600),
            },
            "audit_policy": self._compact_audit_policy(),
            "media_summaries": media_summaries,
            "evidence_catalog": compact_catalog,
            "comment_stats": self._comment_audit_stats(comments),
            "top_comments": [
                {
                    "evidence_id": f"comment:{item.get('comment_id')}",
                    "comment_id": item.get("comment_id"),
                    "score": item.get("risk_score"),
                    "risk_library_id": item.get("risk_library_id", ""),
                    "risk_library_label": item.get("risk_library_label", ""),
                    "secondary_library_ids": item.get("secondary_library_ids") or [],
                    **(
                        {"rule_id": item.get("rule_id", "")}
                        if self._is_ruleset_v2()
                        else {}
                    ),
                    "risk_type": item.get("risk_type", ""),
                    "risk_basis": item.get("risk_basis", ""),
                    "exemption_basis": item.get("exemption_basis", ""),
                    "text": self._truncate_text(item.get("source_text") or item.get("content", ""), 180),
                    "translation_zh": self._truncate_text(item.get("translation_zh", ""), 180),
                }
                for item in top_comments
            ],
            "scoring_rules": scoring_rules,
        }
        fusion_template = str(
            (getattr(self, "prompt_profile_snapshot", {}) or {}).get(
                "fusion_prompt_template"
            )
            or ""
        )
        if self._is_ruleset_v2() and fusion_template:
            payload["scoring_rules"] = []
            payload["comment_fusion_contract"] = {
                "version": "risk-only-final-comments-v1",
                "instructions": (
                    "评论判断已完成，融合只归纳和选择代表证据，不复审或新增评论风险。"
                    "不得改变已保存的评论等级、rule_id、依据或豁免；需要改判须另走显式复审。"
                    "评论 evidence_items 仅返回 {evidence_id:目录ID}，不写等级或reason；"
                    "评论 rule_matches 省略，程序从已保存结果恢复全部风险评论的判断与依据。"
                    "如提供这些字段必须与原值完全相同。上述评论专用协议优先于通用输出示例。"
                    "comment_stats 是全量初审统计，包含未入选代表样本的评论；失败不算pass。"
                    "summary 不得将样本数量当全量，也不得否认已存在的风险评论。"
                    "整帖建议等级不低于评论初审最高等级；评论风险只建议review，不归责主帖作者。"
                    "其他模态继续结合风险证据与必要背景判断引用、反驳等语境。"
                ),
            }
            return self._render_v2_json_prompt(
                fusion_template,
                payload,
                limit=settings.fusion_prompt_max_chars,
                kind="fusion",
            )
        return (
            "你是全帖审核融合器。只校准已有证据在跨分段和跨模态语境中的含义，不重新分析原始媒体。"
            "重点识别引用、反讽、批判、否定、新闻、科普和风险提示等豁免语境。"
            "评论是独立证据：单条中高风险评论可以触发整帖召回复核，但必须说明风险来源为评论区，"
            "不得把第三方评论直接归责为作者主帖风险；若作者迎合、引导、置顶或主帖证据形成闭环，再说明作者/主帖相关性。\n"
            "post 中 title_zh/desc_zh 是标题和正文的中文译文，应优先用于理解外文语义。"
            "content_title 必须生成8至18字中文短标题，即使原始标题为空也要根据摘要和媒体主旨生成，"
            "不得返回内容ID、审核结论或风险等级。\n"
            "只输出合法 JSON：\n"
            "{\"schema_version\":\"audit_fusion_v4\",\"content_title\":\"\",\"summary\":\"\","
            "\"decision_suggestion\":\"pass|review|reject\",\"risk_level_suggestion\":\"none|low|medium|high\","
            "\"primary_risk\":\"\",\"categories\":[],\"evidence_items\":[{\"evidence_id\":\"目录ID\","
            "\"evidence_risk_level\":\"low|medium|high\",\"reason\":\"短原因\"}],"
            "\"rule_matches\":[{\"rule_id\":\"规则ID\",\"evidence_ids\":[\"目录ID\"]}]}\n"
            "硬约束：evidence_items 和 rule_matches 只能选择 evidence_catalog 中存在的 evidence_id；"
            "evidence_items 只输出 evidence_risk_level 为 low、medium、high 的风险证据，"
            "安全或豁免上下文写入 summary，不得作为 none 证据输出；pass/none 时 evidence_items 和 rule_matches 必须为空数组。"
            "不要复制原文，不要创造新证据。只选择真正支撑风险结论的有限证据。summary 不超过80字，reason 不超过45字。\n"
            + "输入 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _compose_fusion_prompt(self, replacements: dict[str, str], extra_instruction: str) -> str:
        prompt = self.prompt_set.fusion_prompt_template
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        return prompt + extra_instruction

    def _trim_fusion_prompt_to_limit(
        self,
        replacements: dict[str, str],
        extra_instruction: str,
        limit: int,
    ) -> str:
        values = dict(replacements)

        def render() -> str:
            return self._compose_fusion_prompt(values, extra_instruction)

        prompt = render()
        if len(prompt) <= limit:
            return prompt

        values["{comments}"] = self._compact_comments_prompt_block(values.get("{comments}", ""), max_comments=24, max_chars=160)
        prompt = render()
        if len(prompt) <= limit:
            return prompt

        values["{image_analyses}"] = self._compact_image_analyses_prompt_block(
            values.get("{image_analyses}", ""),
            max_images=settings.max_images_per_note,
            text_chars=220,
            summary_chars=160,
            risk_limit=2,
        )
        prompt = render()
        if len(prompt) <= limit:
            return prompt

        values["{comments}"] = self._compact_comments_prompt_block(values.get("{comments}", ""), max_comments=12, max_chars=120)
        values["{image_analyses}"] = self._compact_image_analyses_prompt_block(
            values.get("{image_analyses}", ""),
            max_images=min(2, settings.max_images_per_note),
            text_chars=140,
            summary_chars=100,
            risk_limit=1,
        )
        prompt = render()
        if len(prompt) <= limit:
            return prompt

        values["{frame_analyses}"] = self._compact_video_context_prompt_block(
            values.get("{frame_analyses}", ""),
            candidate_limit=max(2, VIDEO_CANDIDATE_CONTEXT_MAX_ITEMS // 2),
            text_chars=1200,
            visual_chars=700,
            local_text_limit=2,
            risk_limit=2,
        )
        prompt = render()
        if len(prompt) <= limit:
            return prompt

        values["{frame_analyses}"] = self._compact_video_context_prompt_block(
            values.get("{frame_analyses}", ""),
            candidate_limit=2,
            text_chars=700,
            visual_chars=400,
            local_text_limit=1,
            risk_limit=1,
        )
        values["{desc}"] = self._truncate_prompt_block(values.get("{desc}", ""), 1200, "正文")
        prompt = render()
        if len(prompt) <= limit:
            return prompt

        minimums = {
            "{frame_analyses}": 1200,
            "{image_analyses}": 600,
            "{comments}": 500,
            "{desc}": 500,
            "{title}": 160,
        }
        while len(prompt) > limit:
            reducible = [
                (token, len(values.get(token, "")))
                for token, minimum in minimums.items()
                if len(values.get(token, "")) > minimum
            ]
            if not reducible:
                break
            token, current_len = max(reducible, key=lambda item: item[1])
            next_len = max(minimums[token], current_len // 2)
            values[token] = self._truncate_prompt_block(values.get(token, ""), next_len, token.strip("{}"))
            prompt = render()

        if len(prompt) <= limit:
            return prompt
        return prompt[: max(0, limit - 80)] + f"\n…（融合 Prompt 仍超过总长度上限，已硬截断到 {limit} 字符）"

    def _compact_comments_prompt_block(self, value: str, *, max_comments: int, max_chars: int) -> str:
        text = str(value or "").strip()
        if not text or text == "（无评论）":
            return text or "（无评论）"
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return self._truncate_prompt_block(text, max_comments * max_chars + 300, "评论")
        comments = payload.get("comments") if isinstance(payload, dict) else None
        if not isinstance(comments, list):
            return self._truncate_prompt_block(text, max_comments * max_chars + 300, "评论")
        compact = []
        for comment in comments[: max(0, max_comments)]:
            if not isinstance(comment, dict):
                continue
            comment_text = self._truncate_text(comment.get("text", ""), max_chars)
            if not comment_text:
                continue
            compact.append({
                "comment_id": str(comment.get("comment_id") or ""),
                "text": comment_text,
            })
        return json.dumps({
            "comments": compact,
            "comment_count": payload.get("comment_count", len(comments)),
            "included_count": len(compact),
            "truncated_for_prompt": True,
        }, ensure_ascii=False, separators=(",", ":"))

    def _compact_image_analyses_prompt_block(
        self,
        value: str,
        *,
        max_images: int,
        text_chars: int,
        summary_chars: int,
        risk_limit: int,
    ) -> str:
        text = str(value or "").strip()
        if not text:
            return "[]"
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return self._truncate_prompt_block(text, max(800, max_images * (text_chars + summary_chars + 220)), "图片分析")
        if isinstance(payload, dict) and isinstance(payload.get("images"), list):
            images = payload.get("images") or []
            image_count = payload.get("image_count", len(images))
        elif isinstance(payload, list):
            images = payload
            image_count = len(images)
        else:
            return self._truncate_prompt_block(text, max(800, max_images * (text_chars + summary_chars + 220)), "图片分析")
        if not isinstance(images, list):
            return self._truncate_prompt_block(text, max(800, max_images * (text_chars + summary_chars + 220)), "图片分析")
        compact = []
        for image in images[: max(0, max_images)]:
            if not isinstance(image, dict):
                continue
            item = {
                "source": image.get("source") or image.get("evidence_id") or f"image:{image.get('index', len(compact))}",
                "index": image.get("index"),
            }
            summary = self._truncate_text(image.get("visual_summary", ""), summary_chars)
            if summary:
                item["visual_summary"] = summary
            safe = self._truncate_text(image.get("benign_context", ""), summary_chars)
            if safe:
                item["benign_context"] = safe
            ocr_text = self._truncate_text(image.get("ocr_text_zh") or image.get("ocr_text") or "", text_chars)
            if ocr_text:
                item["ocr_text"] = ocr_text
            risks = self._compact_risk_items(
                (image.get("risk_items") or [])[: max(0, risk_limit)],
                application_stage="image_evidence",
            )
            if risks:
                item["risk_items"] = risks
            error = self._truncate_text(image.get("error", ""), 120)
            if error:
                item["error"] = error
            compact.append(item)
        return json.dumps({
            "images": compact,
            "image_count": image_count,
            "included_count": len(compact),
            "truncated_for_prompt": True,
        }, ensure_ascii=False, separators=(",", ":"))

    def _compact_video_context_prompt_block(
        self,
        value: str,
        *,
        candidate_limit: int,
        text_chars: int,
        visual_chars: int,
        local_text_limit: int,
        risk_limit: int,
    ) -> str:
        text = str(value or "").strip()
        if not text:
            return "{}"
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return self._truncate_prompt_block(text, max(1200, text_chars * 2 + visual_chars), "视频证据")
        if not isinstance(payload, dict):
            return self._truncate_prompt_block(text, max(1200, text_chars * 2 + visual_chars), "视频证据")
        text_context = payload.get("video_text_context") if isinstance(payload.get("video_text_context"), dict) else {}
        compact = {
            "video_text_context": {
                "ocr_global_text": self._truncate_text(text_context.get("ocr_global_text", ""), text_chars),
                "asr_global_text": self._truncate_text(text_context.get("asr_global_text", ""), text_chars),
            },
            "video_visual_context": self._truncate_text(payload.get("video_visual_context", ""), visual_chars),
            "candidate_context": [],
            "truncated_for_prompt": True,
        }
        for candidate in (payload.get("candidate_context") or [])[: max(0, candidate_limit)]:
            if not isinstance(candidate, dict):
                continue
            visual = candidate.get("visual") if isinstance(candidate.get("visual"), dict) else {}
            compact_candidate = {
                "candidate_id": candidate.get("candidate_id", ""),
                "moment": candidate.get("moment", ""),
                "center_source": candidate.get("center_source", ""),
                "visual": {
                    "summary": self._truncate_text(visual.get("summary", ""), 100),
                    "safe": self._truncate_text(visual.get("safe", ""), 100),
                    "risks": self._compact_candidate_risks(visual.get("risks") or [], risk_limit),
                },
                "ocr": self._compact_source_text_items(candidate.get("ocr") or [], local_text_limit, 100),
                "asr": self._compact_source_text_items(candidate.get("asr") or [], local_text_limit, 140),
            }
            compact["candidate_context"].append(compact_candidate)
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    def _compact_candidate_risks(self, risks: list[dict], limit: int) -> list[dict]:
        compact = []
        for risk in risks[: max(0, limit)]:
            if not isinstance(risk, dict):
                continue
            compact.append({
                "source": risk.get("source", ""),
                "level": risk.get("level", ""),
                "type": self._truncate_text(risk.get("type", ""), 50),
                "text": self._truncate_text(risk.get("text", ""), 80),
                "reason": self._truncate_text(risk.get("reason", ""), 80),
            })
        return compact

    def _compact_source_text_items(self, items: list[dict], limit: int, max_chars: int) -> list[dict]:
        compact = []
        for item in items[: max(0, limit)]:
            if not isinstance(item, dict):
                continue
            text = self._truncate_text(item.get("text", ""), max_chars)
            if not text:
                continue
            compact.append({
                "source": item.get("source", ""),
                "text": text,
            })
        return compact

    def _truncate_prompt_block(self, value: str, max_chars: int, label: str) -> str:
        text = str(value or "")
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        suffix = f"\n…（{label}过长，已为控制融合 Prompt 截断，原始长度 {len(text)} 字）"
        if max_chars <= len(suffix) + 20:
            return text[:max_chars]
        return text[: max_chars - len(suffix)] + suffix

    def _analyze_images(self, subject: AuditSubject, image_dir: Path) -> list[dict]:
        authoritative_m3 = bool(getattr(self, "authoritative_m3", False))
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
                analysis = self.qwen.analyze_image(
                    analysis_path,
                    self.prompt_set.image_prompt,
                    model=settings.qwen_image_audit_model,
                )
                analysis = (
                    self._validated_authoritative_visual_response(
                        analysis, response_contract="image"
                    )
                    if authoritative_m3
                    else dict(analysis or {})
                )
                raw_risk_items = analysis.get("risk_items")
                analysis["risk_items"] = self._filter_stage_risk_items(
                    raw_risk_items,
                    "image_evidence",
                )
                matched_exemption_ids = self._matched_stage_exemption_ids(
                    raw_risk_items,
                    "image_evidence",
                )
                if matched_exemption_ids:
                    analysis["matched_exemption_ids"] = matched_exemption_ids
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
                if authoritative_m3:
                    if isinstance(exc, AuditProviderCallError):
                        raise
                    raise AuditProviderCallError(
                        f"visual Provider failed for local image: {exc}"
                    ) from exc
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
                analysis = self.qwen.analyze_image(
                    image_source,
                    self.prompt_set.image_prompt,
                    model=settings.qwen_image_audit_model,
                )
                analysis = (
                    self._validated_authoritative_visual_response(
                        analysis, response_contract="image"
                    )
                    if authoritative_m3
                    else dict(analysis or {})
                )
                raw_risk_items = analysis.get("risk_items")
                analysis["risk_items"] = self._filter_stage_risk_items(
                    raw_risk_items,
                    "image_evidence",
                )
                matched_exemption_ids = self._matched_stage_exemption_ids(
                    raw_risk_items,
                    "image_evidence",
                )
                if matched_exemption_ids:
                    analysis["matched_exemption_ids"] = matched_exemption_ids
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
                if authoritative_m3:
                    if isinstance(exc, AuditProviderCallError):
                        raise
                    raise AuditProviderCallError(
                        f"visual Provider failed for remote image: {exc}"
                    ) from exc
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
                continue

            if not self._is_probably_video_file(local):
                job_store.log(self.job_id, f"笔记 {subject.note_id}：远程媒体不是可播放视频，已跳过 {local.name}")
                try:
                    local.unlink()
                except OSError:
                    pass
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
        authoritative_m3 = bool(getattr(self, "authoritative_m3", False))
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
                "max_frames": settings.video_ocr_max_frames,
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
                if authoritative_m3:
                    self._validate_authoritative_asr_configuration()
                transcript = self.audio.transcribe(audio_path)
                if authoritative_m3:
                    transcript = self._validated_authoritative_transcript(transcript)
                transcript = self._translate_transcript_if_needed(
                    transcript,
                    video_label,
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
                extract_status = str(
                    getattr(self.audio, "last_extract_status", "") or ""
                )
                if authoritative_m3 and extract_status == "no_audio_track":
                    transcript = {
                        "text": "",
                        "segments": [],
                        "completion_status": "no_audio_track",
                    }
                    job_store.log(
                        self.job_id,
                        f"{video_label}：媒体已确定无音轨，跳过 ASR",
                    )
                elif authoritative_m3:
                    detail = (
                        getattr(self.audio, "last_extract_error", "")
                        or "audio extraction did not prove success"
                    )
                    raise AuditProviderCallError(
                        f"audio extraction failed: {detail}"
                    )
                else:
                    detail = getattr(self.audio, "last_extract_error", "") or "audio extraction failed"
                    errors.append(f"audio extraction failed: {detail}")
                    job_store.log(self.job_id, f"{video_label}：音频抽取失败：{detail}，跳过转写")
        except Exception as exc:
            if authoritative_m3:
                if isinstance(
                    exc, (AuditProviderUnavailableError, AuditProviderCallError)
                ):
                    raise
                raise AuditProviderCallError(
                    f"audio extraction or ASR failed: {exc}"
                ) from exc
            errors.append(f"audio/transcribe failed: {exc}")
            job_store.log(self.job_id, f"{video_label}：音频/转写失败：{exc}")

        try:
            job_store.log(
                self.job_id,
                f"{video_label}：开始抽取 timeline frames，最多 {settings.video_review_max_frames} 帧，"
                f"scene={settings.video_scene_threshold}，fps-floor={settings.video_fps_floor_seconds}s",
            )
            frame_infos = self.frames.extract_timeline_frames(
                video_path=video_path,
                output_dir=video_dir / f"frames_{index:02d}",
                max_frames=settings.video_review_max_frames,
            )
            job_store.log(self.job_id, f"{video_label}：timeline frames 抽取完成，共 {len(frame_infos)} 帧")
            ocr_started = perf_counter()
            frame_ocr_results = self._scan_timeline_ocr_batch(index, video_label, frame_infos)
            for frame_idx, frame in enumerate(frame_infos, start=1):
                frame_ts = float(frame.get("timestamp") or 0.0)
                frame_ocr = frame_ocr_results.get(frame_idx) or {}
                if frame_ocr:
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
            metrics["candidate_frames"] = len(frame_infos)
            metrics["sampled_frames"] = len(frame_ocr_results)
            metrics["skipped_frames"] = max(0, len(frame_infos) - len(frame_ocr_results))
            job_store.log(
                self.job_id,
                f"{video_label}：timeline OCR 完成，sampled={metrics.get('sampled_frames', 0)}/{metrics.get('candidate_frames', 0)}，"
                f"ocr_calls={metrics.get('ocr_calls', 0)}，"
                f"translation_calls={metrics.get('translation_calls', 0)}，errors={metrics.get('errors', 0)}，"
                f"耗时={perf_counter() - ocr_started:.1f}s",
            )

            _, _, duration = self.frames.video_meta(video_path)
            sheet_size = max(1, settings.video_review_sheet_frames)
            chunks = [timeline_frames[:sheet_size]]
            if len(timeline_frames) > sheet_size:
                chunks.append(timeline_frames[sheet_size : settings.video_review_max_frames])
            chunks = [chunk for chunk in chunks if chunk]
            boundary_ts = (
                float(timeline_frames[sheet_size - 1].get("timestamp") or 0.0)
                if len(timeline_frames) > sheet_size
                else duration
            )
            duration = max(duration, max((float(frame.get("timestamp") or 0.0) for frame in timeline_frames), default=0.0))
            review_jobs: list[dict] = []
            review_sheets_created: list[dict] = []
            frames_by_segment: dict[str, list[dict]] = {}
            library_policies = self._active_library_policies(["vision", "ocr", "asr"])
            for segment_idx, chunk in enumerate(chunks, start=1):
                segment_id = f"video:{index + 1}/segment:{segment_idx}"
                sheet_path = video_dir / f"review_sheets_{index:02d}" / f"segment_{segment_idx:02d}.jpg"
                self.frames.create_contact_sheet(chunk, sheet_path, columns=4, rows=4)
                main_start = 0.0 if segment_idx == 1 else boundary_ts
                main_end = boundary_ts if segment_idx == 1 and len(chunks) > 1 else duration
                overlap = max(0.0, settings.video_review_asr_overlap_seconds)
                asr_start = max(0.0, main_start - (overlap if segment_idx > 1 else 0.0))
                asr_end = min(duration, main_end + (overlap if segment_idx == 1 and len(chunks) > 1 else 0.0))
                ocr_chunks = self._build_ocr_context_chunks(chunk, segment_id)
                asr_chunks = self._build_asr_context_chunks(transcript, asr_start, asr_end, segment_id)
                sheet = {
                    "segment_id": segment_id,
                    "index": segment_idx,
                    "path": str(sheet_path),
                    "asset_rel": self._to_job_rel(str(sheet_path), settings.outputs_dir / self.job_id),
                    "frame_ids": [frame.get("frame_id") for frame in chunk],
                    "start": main_start,
                    "end": main_end,
                    "boundary_timestamp": boundary_ts if len(chunks) > 1 else None,
                    "ocr_chunks": ocr_chunks,
                    "asr_chunks": asr_chunks,
                    "library_ids": [policy.get("id") for policy in library_policies if policy.get("id")],
                }
                review_sheets_created.append(sheet)
                frames_by_segment[segment_id] = chunk
                for library_policy in library_policies:
                    prompt = self._render_review_sheet_prompt(
                        video_index=index,
                        segment=sheet,
                        frames=chunk,
                        title=title,
                        desc=desc,
                        library_policy=library_policy,
                    )
                    review_jobs.append({
                        "sheet": sheet,
                        "sheet_path": sheet_path,
                        "prompt": prompt,
                        "frames": chunk,
                        "library_policy": library_policy,
                    })

            workers = max(1, min(settings.video_review_concurrency, len(review_jobs) or 1))
            if review_jobs:
                job_store.log(
                    self.job_id,
                    f"{video_label}：开始按库 Sheet 审核，并发={workers}，"
                    f"sheet={len(review_sheets_created)}，风险库={len(library_policies)}，任务={len(review_jobs)}",
                )

            def analyze_sheet(job: dict) -> dict:
                sheet = job["sheet"]
                segment_idx = int(sheet["index"])
                library_policy = job.get("library_policy") or {}
                library_label = library_policy.get("title") or library_policy.get("id") or "默认风险库"
                mode = "remote_vlm" if settings.use_remote_vlm else ("dashscope_api" if self.qwen.enabled else "mock")
                job_store.log(self.job_id, f"{video_label}：Sheet {segment_idx} · {library_label} 审核开始，mode={mode}")
                vlm_started = perf_counter()
                analysis = self._audit_review_sheet(job, authoritative_m3=authoritative_m3)
                job_store.log(
                    self.job_id,
                    f"{video_label}：Sheet {segment_idx} · {library_label} 审核完成，"
                    f"score={analysis.get('segment_score')}，risks="
                    f"{sum(len(analysis.get(key) or []) for key in ('visual_risks', 'ocr_risks', 'asr_risks'))}，"
                    f"耗时={perf_counter() - vlm_started:.1f}s",
                )
                return {**job, "analysis": analysis}

            if workers <= 1 or len(review_jobs) <= 1:
                analyzed_sheets = [analyze_sheet(job) for job in review_jobs]
            else:
                analyzed_sheets = []
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(analyze_sheet, job): job for job in review_jobs}
                    for future in as_completed(futures):
                        analyzed_sheets.append(future.result())

            moment_sheets.extend(review_sheets_created)
            reviewed_by_segment: dict[str, list[dict]] = {}
            for reviewed in analyzed_sheets:
                segment_id = str((reviewed.get("sheet") or {}).get("segment_id") or "")
                if segment_id:
                    reviewed_by_segment.setdefault(segment_id, []).append(reviewed)
            for sheet in review_sheets_created:
                segment_id = str(sheet.get("segment_id") or "")
                reviewed_items = sorted(
                    reviewed_by_segment.get(segment_id) or [],
                    key=lambda item: str((item.get("library_policy") or {}).get("id") or ""),
                )
                analyses = [item.get("analysis") or {} for item in reviewed_items]
                merged_analysis = self._merge_segment_reviews(analyses)
                chunk = frames_by_segment.get(segment_id) or []
                moments.append({
                    **sheet,
                    "analysis": merged_analysis,
                    "library_reviews": [
                        {
                            "risk_library_id": analysis.get("risk_library_id", ""),
                            "risk_library_label": analysis.get("risk_library_label", ""),
                            "segment_score": analysis.get("segment_score", 0),
                            "segment_level": analysis.get("segment_level", "none"),
                            "segment_summary": analysis.get("segment_summary", ""),
                            **(
                                {
                                    "matched_exemption_ids": analysis.get(
                                        "matched_exemption_ids"
                                    )
                                    or []
                                }
                                if self._is_ruleset_v2()
                                else {}
                            ),
                        }
                        for analysis in analyses
                    ],
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

        except Exception as exc:
            if authoritative_m3:
                if isinstance(exc, AuditProviderCallError):
                    raise
                raise AuditProviderCallError(
                    f"visual Provider or frame analysis failed: {exc}"
                ) from exc
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
            "review_sheets": moment_sheets,
            "segment_reviews": moments,
            "moment_sheets": [],
            "moments": [],
            "precise_sheets": [],
            "frames": timeline_frames,
            "duration": duration if 'duration' in locals() else 0.0,
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
        ocr_frames = self._select_timeline_ocr_frames(frame_infos, settings.video_ocr_max_frames)
        if len(ocr_frames) < len(frame_infos):
            job_store.log(
                self.job_id,
                f"{video_label}：OCR timeline frames sampled {len(ocr_frames)}/{len(frame_infos)}",
            )
        workers = max(1, settings.ocr_concurrency) if settings.ocr_enabled else 1

        def scan(sample_idx: int, frame_idx: int, frame: dict) -> tuple[int, dict]:
            job_store.log(
                self.job_id,
                f"{video_label}：OCR timeline frame {sample_idx}/{len(ocr_frames)} "
                f"(source {frame_idx}/{len(frame_infos)})",
            )
            frame_ts = float(frame.get("timestamp") or 0.0)
            result = self.ocr.scan_image(
                Path(frame["path"]),
                timestamp=frame_ts,
                frame_num=frame.get("frame_number"),
                state_id=f"video_{video_index + 1}_frame_{frame_idx:04d}",
                log=lambda message: job_store.log(self.job_id, f"{video_label}：{message}"),
            )
            return frame_idx, result

        if workers <= 1 or len(ocr_frames) <= 1:
            return dict(
                scan(sample_idx, frame_idx, frame)
                for sample_idx, (frame_idx, frame) in enumerate(ocr_frames, start=1)
            )

        results: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(scan, sample_idx, frame_idx, frame): frame_idx
                for sample_idx, (frame_idx, frame) in enumerate(ocr_frames, start=1)
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
    def _select_timeline_ocr_frames(frame_infos: list[dict], max_frames: int) -> list[tuple[int, dict]]:
        indexed = list(enumerate(frame_infos, start=1))
        if max_frames <= 0 or len(indexed) <= max_frames:
            return indexed
        if max_frames == 1:
            return [indexed[len(indexed) // 2]]

        last_index = len(indexed) - 1
        positions: list[int] = []
        seen: set[int] = set()
        for sample_idx in range(max_frames):
            position = round(sample_idx * last_index / (max_frames - 1))
            if position not in seen:
                positions.append(position)
                seen.add(position)

        if len(positions) < max_frames:
            for position in range(len(indexed)):
                if position in seen:
                    continue
                positions.append(position)
                seen.add(position)
                if len(positions) >= max_frames:
                    break
            positions.sort()

        return [indexed[position] for position in positions[:max_frames]]

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

    def _build_ocr_context_chunks(self, frames: list[dict], segment_id: str) -> list[dict]:
        if not frames:
            return []
        group_count = max(1, (len(frames) + 4) // 5)
        base_size, remainder = divmod(len(frames), group_count)
        sizes = [base_size + (1 if idx < remainder else 0) for idx in range(group_count)]
        chunks: list[dict] = []
        offset = 0
        for chunk_index, size in enumerate(sizes, start=1):
            chunk_frames = frames[offset : offset + size]
            offset += size
            items: list[dict] = []
            for frame in chunk_frames:
                frame_id = str(frame.get("frame_id") or "")
                source_text = self._truncate_text(frame.get("ocr_text", ""), settings.fusion_ocr_text_max_chars)
                translation_zh = self._truncate_text(frame.get("ocr_text_zh", ""), settings.fusion_ocr_text_max_chars)
                timestamp = round(float(frame.get("timestamp") or 0.0), 3)
                duplicate_key = (" ".join(source_text.split()), " ".join(translation_zh.split()))
                previous = items[-1] if items else None
                if previous and duplicate_key != ("", "") and duplicate_key == previous.get("duplicate_key"):
                    previous["frame_ids"].append(frame_id)
                    previous["end"] = timestamp
                    continue
                items.append({
                    "frame_ids": [frame_id],
                    "start": timestamp,
                    "end": timestamp,
                    "source_text": source_text,
                    "translation_zh": translation_zh,
                    "duplicate_key": duplicate_key,
                })
            for item in items:
                item.pop("duplicate_key", None)
            chunks.append({
                "ocr_chunk_id": f"{segment_id}/ocr:{chunk_index}",
                "frame_ids": [str(frame.get("frame_id") or "") for frame in chunk_frames],
                "start": round(float(chunk_frames[0].get("timestamp") or 0.0), 3),
                "end": round(float(chunk_frames[-1].get("timestamp") or 0.0), 3),
                "items": items,
            })
        return chunks

    def _build_asr_context_chunks(
        self,
        transcript: dict,
        start_ts: float,
        end_ts: float,
        segment_id: str,
    ) -> list[dict]:
        source_segments: list[dict] = []
        for source_index, seg in enumerate(transcript.get("segments") or [], start=1):
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") if seg.get("end") is not None else start)
            except (TypeError, ValueError):
                continue
            if end < start_ts or start > end_ts:
                continue
            dolphin_text = self._truncate_text(seg.get("source_text_dolphin") or seg.get("text", ""), 500)
            mms_text = self._truncate_text(seg.get("source_text_mms", ""), 500)
            translation_zh = self._truncate_text(seg.get("translation_zh", ""), 600)
            if not dolphin_text and not mms_text and not translation_zh:
                continue
            source_segments.append({
                "source_segment_id": f"asr_raw:{source_index}",
                "start": start,
                "end": max(start, end),
                "source_text_dolphin": dolphin_text,
                "source_text_mms": mms_text,
                "translation_zh": translation_zh,
                "consistency": self._truncate_text(
                    seg.get("translation_notes") or seg.get("translation_confidence") or "",
                    100,
                ),
            })

        if not source_segments and not transcript.get("segments"):
            fallback_text = self._truncate_text(transcript.get("text", ""), 1000)
            fallback_zh = self._truncate_text(transcript.get("text_zh", ""), 1200)
            if not fallback_text and not fallback_zh:
                return []
            source_segments = [{
                "source_segment_id": "asr_raw:1",
                "start": start_ts,
                "end": end_ts,
                "source_text_dolphin": fallback_text,
                "source_text_mms": self._truncate_text((transcript.get("mms") or {}).get("text", ""), 1000),
                "translation_zh": fallback_zh,
                "consistency": self._truncate_text((transcript.get("translation") or {}).get("asr_consistency", ""), 100),
            }]
        if not source_segments:
            return []

        total_chars = sum(
            len(item["source_text_dolphin"]) + len(item["translation_zh"])
            for item in source_segments
        )
        configured_max = max(1, min(4, settings.video_review_asr_chunks))
        target_count = 1
        for threshold in (180, 500, 900):
            if total_chars > threshold:
                target_count += 1
        target_count = min(configured_max, target_count, len(source_segments))
        groups = self._partition_weighted_segments(source_segments, target_count)
        global_consistency = self._truncate_text(
            (transcript.get("translation") or {}).get("asr_consistency", ""),
            120,
        )
        chunks: list[dict] = []
        for chunk_index, group in enumerate(groups, start=1):
            chunks.append({
                "asr_chunk_id": f"{segment_id}/asr:{chunk_index}",
                "start": round(float(group[0]["start"]), 3),
                "end": round(float(group[-1]["end"]), 3),
                "source_segment_ids": [item["source_segment_id"] for item in group],
                "source_text_dolphin": " ".join(item["source_text_dolphin"] for item in group if item["source_text_dolphin"]),
                "source_text_mms": " ".join(item["source_text_mms"] for item in group if item["source_text_mms"]),
                "translation_zh": " ".join(item["translation_zh"] for item in group if item["translation_zh"]),
                "consistency": global_consistency or "；".join(
                    dict.fromkeys(item["consistency"] for item in group if item["consistency"])
                ),
            })
        return chunks

    @staticmethod
    def _partition_weighted_segments(segments: list[dict], group_count: int) -> list[list[dict]]:
        if group_count <= 1 or len(segments) <= 1:
            return [segments]
        weights = [
            max(1, len(item.get("source_text_dolphin", "")) + len(item.get("translation_zh", "")))
            for item in segments
        ]
        groups: list[list[dict]] = []
        offset = 0
        remaining_weight = sum(weights)
        for group_index in range(group_count):
            remaining_groups = group_count - group_index
            max_take = len(segments) - offset - (remaining_groups - 1)
            if remaining_groups == 1:
                take = max_take
            else:
                target = remaining_weight / remaining_groups
                take = 1
                accumulated = weights[offset]
                while take < max_take:
                    next_weight = weights[offset + take]
                    if abs(accumulated - target) <= abs(accumulated + next_weight - target):
                        break
                    accumulated += next_weight
                    take += 1
            group = segments[offset : offset + take]
            groups.append(group)
            consumed = sum(weights[offset : offset + take])
            offset += take
            remaining_weight -= consumed
        return groups

    def _render_review_sheet_prompt(
        self,
        *,
        video_index: int,
        segment: dict,
        frames: list[dict],
        title: str,
        desc: str,
        library_policy: dict | None = None,
    ) -> str:
        library_policy = library_policy if isinstance(library_policy, dict) else {}
        payload = {
            "video": video_index + 1,
            "segment_id": segment.get("segment_id"),
            "main_time_range": {"start": segment.get("start"), "end": segment.get("end")},
            "risk_library": library_policy,
            "title": self._truncate_text(title, 180),
            "desc": self._truncate_text(desc, 360),
            "frames": [
                {
                    "frame_id": frame.get("frame_id"),
                    "frame_number": frame.get("frame_number"),
                    "timestamp": round(float(frame.get("timestamp") or 0.0), 3),
                }
                for frame in frames
            ],
            "ocr_chunks": segment.get("ocr_chunks") or [],
            "asr_chunks": segment.get("asr_chunks") or [],
        }
        if self._is_ruleset_v2():
            return self._render_v2_json_prompt(
                self.prompt_set.frame_prompt,
                payload,
                limit=settings.frame_audit_prompt_max_chars,
                kind="frame",
            )
        return (
            "你是视频内容审核器。输入图片是一张按真实时间排序的 4x4 contact sheet；空白格不代表视频内容。"
            "请只按输入 JSON 中 risk_library 指定的风险库审核本分段，并结合帖子上下文、逐帧 OCR 原文/中文译文和对应时间范围内的 ASR 大段。\n"
            "只输出合法 JSON，不要输出 Markdown，不要复述 OCR 或 ASR 全文：\n"
            "{\n"
            '  "segment_summary": "客观概括，最多45个汉字",\n'
            '  "segment_score": 0,\n'
            '  "risk_library_id": "风险库 id",\n'
            '  "risk_library_label": "风险库名称",\n'
            '  "visual_risks": [{"frame_ids":["f0001"],"score":0,"risk_type":"类别","reason":"短原因"}],\n'
            '  "ocr_risks": [{"ocr_chunk_id":".../ocr:1","frame_ids":["f0001"],"score":0,"risk_type":"类别","reason":"短原因"}],\n'
            '  "asr_risks": [{"asr_chunk_id":".../asr:1","score":0,"risk_type":"类别","reason":"短原因"}]\n'
            "}\n"
            "约束：所有 ID 只能从输入 JSON 中选择；没有明确风险时对应数组为空。"
            "每条风险的 score 为 0-100，reason 最多 35 个汉字。"
            "必须区分宣扬、诱导、攻击、交易、组织和新闻、科普、批判、举报、反讽、正常生活等豁免语境；不要仅凭关键词判违规。\n"
            + "输入 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _video_rule_code_mapping(self) -> dict[str, str]:
        """Bind short codes to the ordered, frozen rules of this request."""
        routes = self.rule_snapshot.get("stage_routes") or {}
        rule_ids = list(dict.fromkeys(routes.get("video_frame_evidence") or []))
        return {f"R{index:02d}": rule_id for index, rule_id in enumerate(rule_ids, start=1)}

    @staticmethod
    def _decode_video_rule_codes(raw: dict, mapping: dict[str, str]) -> dict:
        decoded = dict(raw)
        for field in ("visual_risks", "ocr_risks", "asr_risks"):
            decoded[field] = []
            for original in raw.get(field) or []:
                item = dict(original)
                code = str(item.get("rule_id") or item.get("id") or "").strip()
                if code:
                    if code not in mapping:
                        raise FusionAuditContractError(f"video_frame_evidence has an unknown rule code: {code!r}")
                    item["rule_id"] = mapping[code]
                    if "id" in item:
                        item["id"] = mapping[code]
                # Missing codes on non-none items still fail native validation.
                decoded[field].append(item)
        return decoded

    def _audit_review_sheet(self, job: dict, *, authoritative_m3: bool) -> dict:
        """Select short rule codes, restore stable IDs, and correct at most once."""
        sheet = job["sheet"]
        is_v2 = self._is_ruleset_v2()
        mapping = self._video_rule_code_mapping() if is_v2 else {}
        base_prompt = job["prompt"]
        if mapping:
            reverse = {value: key for key, value in mapping.items()}
            # One substitution pass prevents overlapping rule names or codes
            # from changing the mapping. Business rule descriptions stay intact.
            pattern = r"(?<![A-Za-z0-9_.:-])(?:" + "|".join(re.escape(value) for value in sorted(reverse, key=len, reverse=True)) + r")(?![A-Za-z0-9_.:-])"
            base_prompt = re.sub(pattern, lambda match: reverse[match.group()], base_prompt)
            base_prompt = base_prompt.replace("stable rule_id", "本次短编号，例如R01")
        if is_v2:
            base_prompt += "\n规则编号协议 video-rule-codes-v1：每个风险项的 rule_id 只能从下列短编号中准确选择，禁止自造、拼接、猜测或输出完整规则ID。必须满足该编号对应规则的必要条件；编号正确不代表风险成立，无明确风险时对应数组为空。frame_id、ocr_chunk_id、asr_chunk_id、豁免ID不是规则编号，保持原格式。\n" + json.dumps({"allowed_rule_codes": list(mapping)}, ensure_ascii=False)
        prompt = base_prompt
        attempts = 2 if is_v2 else 1
        allowed = {
            "rule_codes": list(mapping),
            "frame_ids": [item["frame_id"] for item in job["frames"]],
            "ocr_chunk_ids": [item["ocr_chunk_id"] for item in sheet.get("ocr_chunks") or []],
            "asr_chunk_ids": [item["asr_chunk_id"] for item in sheet.get("asr_chunks") or []],
        }
        for attempt in range(1, attempts + 1):
            trace = {
                "protocol": "video-rule-codes-v1", "attempt": attempt,
                "segment_id": sheet.get("segment_id"), "rule_code_mapping": mapping,
                "request_prompt": prompt, "sheet": sheet, "frames": job["frames"],
                "model": settings.qwen_contact_sheet_model, "max_tokens": settings.fusion_max_tokens,
                "allowed_ids": allowed, "status": "requesting",
            }
            trace_path = None
            if is_v2:
                directory = settings.outputs_dir / self.job_id / "video_review_requests"
                directory.mkdir(parents=True, exist_ok=True)
                trace_path = directory / f"{time_ns()}-{attempt}.json"
                trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            raw = self.qwen.analyze_image(
                job["sheet_path"], prompt,
                max_tokens=settings.fusion_max_tokens,
                model=settings.qwen_contact_sheet_model,
            )
            capture = getattr(self.qwen, "last_raw_response", None)
            trace.update(parsed_response=raw, raw_provider_response=capture() if callable(capture) else None)
            try:
                validated = self._validated_authoritative_visual_response(raw, response_contract="video_segment") if authoritative_m3 else raw
                decoded = self._decode_video_rule_codes(validated, mapping) if is_v2 else validated
                analysis = self._normalize_segment_review(
                    decoded, sheet, job["frames"], library_policy=job.get("library_policy") or {},
                )
            except (FusionAuditContractError, AuditProviderCallError) as exc:
                if not is_v2:
                    raise
                # Persist outside temporary video directories, including image.
                directory = settings.outputs_dir / self.job_id / "video_review_failures" / f"{time_ns()}-{attempt}"
                directory.mkdir(parents=True, exist_ok=False)
                image_path = Path(job["sheet_path"])
                shutil.copy2(image_path, directory / ("sheet" + image_path.suffix))
                returned = [
                    str(item.get("rule_id") or item.get("id") or "")
                    for field in ("visual_risks", "ocr_risks", "asr_risks")
                    for values in [raw.get(field)] if isinstance(values, list)
                    for item in values if isinstance(item, dict)
                ] if isinstance(raw, dict) else []
                trace.update(status="contract_invalid", error=str(exc), returned_rule_codes=returned)
                serialized = json.dumps(trace, ensure_ascii=False, indent=2)
                (directory / "failure.json").write_text(serialized, encoding="utf-8")
                trace_path.write_text(serialized, encoding="utf-8")
                job_store.log(self.job_id, f"视频分段 {sheet.get('segment_id')}：结构校验失败 {attempt}/{attempts}，error={exc}，已保存失败输入与响应")
                if attempt == attempts:
                    raise
                prompt = base_prompt + "\n上次输出未通过程序校验。请纠正规则或证据引用格式，沿用原审核标准，不能以删除已有风险项代替修正引用。只输出 JSON。\n" + json.dumps({
                    "error": str(exc), "returned_rule_codes": returned, "allowed_ids": allowed,
                }, ensure_ascii=False)
                continue
            if trace_path is not None:
                trace.update(status="validated", normalized_analysis=analysis)
                trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            return analysis
        raise AssertionError("video review attempts exhausted without a result")

    def _normalize_segment_review(
        self,
        analysis: dict,
        sheet: dict,
        frames: list[dict],
        *,
        library_policy: dict | None = None,
    ) -> dict:
        analysis = analysis if isinstance(analysis, dict) else {}
        library_policy = library_policy if isinstance(library_policy, dict) else {}
        risk_library_id = str(
            library_policy.get("id")
            or analysis.get("risk_library_id")
            or ""
        ).strip()
        risk_library_label = str(
            library_policy.get("title")
            or analysis.get("risk_library_label")
            or risk_library_id
        ).strip()
        valid_frame_ids = {str(frame.get("frame_id") or "") for frame in frames}
        ocr_by_id = {
            str(item.get("ocr_chunk_id") or ""): item
            for item in sheet.get("ocr_chunks") or []
        }
        asr_by_id = {
            str(item.get("asr_chunk_id") or ""): item
            for item in sheet.get("asr_chunks") or []
        }
        thresholds = self._active_thresholds()

        def normalized_score(item: dict) -> int:
            return self._normalize_risk_score(item.get("score"), item.get("risk_level") or "none")

        def is_v2_none_item(item: dict) -> bool:
            if not self._is_ruleset_v2():
                return False
            raw_score = item.get("score")
            if isinstance(raw_score, bool):
                raise FusionAuditContractError(
                    "video_frame_evidence score is missing or invalid"
                )
            try:
                score_value = float(raw_score)
            except (TypeError, ValueError) as exc:
                raise FusionAuditContractError(
                    "video_frame_evidence score is missing or invalid"
                ) from exc
            if not 0 <= score_value <= 100:
                raise FusionAuditContractError(
                    "video_frame_evidence score is outside 0-100"
                )
            if "risk_level" not in item and score_value == 0:
                return True
            if "risk_level" in item:
                return (
                    self._strict_v2_risk_level(
                        item.get("risk_level"),
                        field="video_frame_evidence.risk_level",
                        allow_none=True,
                    )
                    == "none"
                )
            return False

        applied_exemption_ids: list[str] = []

        def normalize_v2_rule(item: dict) -> tuple[str, bool]:
            if not self._is_ruleset_v2():
                return "", False
            rule_id = self._normalize_stage_rule_id(
                item.get("rule_id") or item.get("id"),
                "video_frame_evidence",
            )
            if not rule_id:
                raise FusionAuditContractError(
                    "video_frame_evidence non-none result has an invalid rule_id"
                )
            matched = self._normalize_matched_exemption_ids(
                rule_id,
                item.get("matched_exemption_ids"),
            )
            if matched:
                for exemption_id in matched:
                    if exemption_id not in applied_exemption_ids:
                        applied_exemption_ids.append(exemption_id)
            return rule_id, bool(matched)

        def normalized_level_and_score(item: dict, rule_id: str) -> tuple[str, int]:
            if not self._is_ruleset_v2():
                score = normalized_score(item)
                return self._level_from_score(score, thresholds), score
            risk_level = self._strict_v2_risk_level(
                item.get("risk_level"),
                field="video_frame_evidence.risk_level",
                allow_none=False,
            )
            return risk_level, self._risk_score_for_level(risk_level)

        visual_risks = []
        for item in analysis.get("visual_risks") or []:
            if not isinstance(item, dict):
                continue
            if is_v2_none_item(item):
                continue
            frame_ids = self._valid_reference_ids(item.get("frame_ids") or item.get("frame_id"), valid_frame_ids)
            if not frame_ids:
                if self._is_ruleset_v2():
                    raise FusionAuditContractError(
                        "video_frame_evidence visual risk has invalid frame_ids"
                    )
                continue
            rule_id, exempted = normalize_v2_rule(item)
            risk_level, score = normalized_level_and_score(item, rule_id)
            if exempted:
                continue
            normalized = {
                "frame_ids": frame_ids,
                "score": score,
                "risk_level": risk_level,
                "risk_library_id": risk_library_id,
                "risk_library_label": risk_library_label,
                "risk_type": self._normalize_risk_type(item.get("risk_type", ""), 60),
                "reason": self._truncate_text(item.get("reason", ""), 100),
            }
            if rule_id:
                normalized["rule_id"] = rule_id
            visual_risks.append(normalized)

        ocr_risks = []
        for item in analysis.get("ocr_risks") or []:
            if not isinstance(item, dict):
                continue
            if is_v2_none_item(item):
                continue
            chunk_id = str(item.get("ocr_chunk_id") or "")
            chunk = ocr_by_id.get(chunk_id)
            if not chunk:
                if self._is_ruleset_v2():
                    raise FusionAuditContractError(
                        "video_frame_evidence OCR risk has an invalid ocr_chunk_id"
                    )
                continue
            valid_chunk_frames = set(chunk.get("frame_ids") or []) & valid_frame_ids
            frame_ids = self._valid_reference_ids(item.get("frame_ids") or item.get("frame_id"), valid_chunk_frames)
            if not frame_ids:
                if self._is_ruleset_v2():
                    raise FusionAuditContractError(
                        "video_frame_evidence OCR risk has invalid frame_ids"
                    )
                continue
            rule_id, exempted = normalize_v2_rule(item)
            risk_level, score = normalized_level_and_score(item, rule_id)
            if exempted:
                continue
            normalized = {
                "ocr_chunk_id": chunk_id,
                "frame_ids": frame_ids,
                "score": score,
                "risk_level": risk_level,
                "risk_library_id": risk_library_id,
                "risk_library_label": risk_library_label,
                "risk_type": self._normalize_risk_type(item.get("risk_type", ""), 60),
                "reason": self._truncate_text(item.get("reason", ""), 100),
            }
            if rule_id:
                normalized["rule_id"] = rule_id
            ocr_risks.append(normalized)

        asr_risks = []
        for item in analysis.get("asr_risks") or []:
            if not isinstance(item, dict):
                continue
            if is_v2_none_item(item):
                continue
            chunk_id = str(item.get("asr_chunk_id") or "")
            if chunk_id not in asr_by_id:
                if self._is_ruleset_v2():
                    raise FusionAuditContractError(
                        "video_frame_evidence ASR risk has an invalid asr_chunk_id"
                    )
                continue
            rule_id, exempted = normalize_v2_rule(item)
            risk_level, score = normalized_level_and_score(item, rule_id)
            if exempted:
                continue
            normalized = {
                "asr_chunk_id": chunk_id,
                "score": score,
                "risk_level": risk_level,
                "risk_library_id": risk_library_id,
                "risk_library_label": risk_library_label,
                "risk_type": self._normalize_risk_type(item.get("risk_type", ""), 60),
                "reason": self._truncate_text(item.get("reason", ""), 100),
            }
            if rule_id:
                normalized["rule_id"] = rule_id
            asr_risks.append(normalized)

        segment_score = (
            0
            if self._is_ruleset_v2()
            else self._normalize_risk_score(analysis.get("segment_score"), "none")
        )
        segment_score = max(
            [segment_score]
            + [item["score"] for item in visual_risks + ocr_risks + asr_risks]
        )
        segment_level = (
            max(
                (item["risk_level"] for item in visual_risks + ocr_risks + asr_risks),
                key=self._risk_level_rank,
                default="none",
            )
            if self._is_ruleset_v2()
            else self._level_from_score(segment_score, thresholds)
        )
        normalized = {
            "segment_summary": self._truncate_text(analysis.get("segment_summary") or analysis.get("summary", ""), 100),
            "segment_score": segment_score,
            "segment_level": segment_level,
            "risk_library_id": risk_library_id,
            "risk_library_label": risk_library_label,
            "visual_risks": visual_risks,
            "ocr_risks": ocr_risks,
            "asr_risks": asr_risks,
        }
        if applied_exemption_ids:
            normalized["matched_exemption_ids"] = applied_exemption_ids
        return normalized

    def _merge_segment_reviews(self, analyses: list[dict]) -> dict:
        thresholds = self._active_thresholds()
        cleaned = [analysis for analysis in analyses if isinstance(analysis, dict)]
        if not cleaned:
            return {
                "segment_summary": "",
                "segment_score": 0,
                "segment_level": "none",
                "visual_risks": [],
                "ocr_risks": [],
                "asr_risks": [],
            }
        top = max(cleaned, key=lambda item: int(item.get("segment_score") or 0))
        visual_risks = []
        ocr_risks = []
        asr_risks = []
        matched_exemption_ids = []
        for analysis in cleaned:
            visual_risks.extend(analysis.get("visual_risks") or [])
            ocr_risks.extend(analysis.get("ocr_risks") or [])
            asr_risks.extend(analysis.get("asr_risks") or [])
            for exemption_id in analysis.get("matched_exemption_ids") or []:
                if exemption_id not in matched_exemption_ids:
                    matched_exemption_ids.append(exemption_id)
        segment_score = max(
            [self._normalize_risk_score(item.get("segment_score"), item.get("segment_level") or "none") for item in cleaned]
            + [int(item.get("score") or 0) for item in visual_risks + ocr_risks + asr_risks]
        )
        segment_level = (
            max(
                (item.get("risk_level", "none") for item in visual_risks + ocr_risks + asr_risks),
                key=self._risk_level_rank,
                default="none",
            )
            if self._is_ruleset_v2()
            else self._level_from_score(segment_score, thresholds)
        )
        merged = {
            "segment_summary": self._truncate_text(
                top.get("segment_summary")
                or next((item.get("segment_summary") for item in cleaned if item.get("segment_summary")), ""),
                100,
            ),
            "segment_score": segment_score,
            "segment_level": segment_level,
            "visual_risks": visual_risks,
            "ocr_risks": ocr_risks,
            "asr_risks": asr_risks,
        }
        if matched_exemption_ids:
            merged["matched_exemption_ids"] = matched_exemption_ids
        return merged

    @staticmethod
    def _valid_reference_ids(values, valid_ids: set[str]) -> list[str]:
        if isinstance(values, str):
            values = [values]
        out = []
        for value in values or []:
            item = str(value or "").strip()
            if item and item in valid_ids and item not in out:
                out.append(item)
        return out

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
            '  "summary": "一句话客观概括本 contact sheet 的画面和文字主旨，最多 35 个汉字，不写审核结论",\n'
            '  "status": "safe|suspicious",\n'
            '  "candidate_frame_ids": ["只能从本次 frames 中选择，如 f0001"],\n'
            '  "risk_types": ["风险类别"],\n'
            '  "reason": "如果 suspicious，用一句话说明怀疑点；safe 时写空字符串",\n'
            '  "safe_context": "兼容字段，可留空；如需说明明显豁免语境，最多 30 个汉字"\n'
            "}\n\n"
            "约束：\n"
            "1. 只能引用本 contact sheet 中存在的 frame_id，不要输出自由时间段。\n"
            "2. 没有明确画面证据时输出 safe，candidate_frame_ids 为空数组。\n"
            "3. OCR/ASR 只是上下文线索，不要仅凭单个敏感词升级风险。\n"
            "4. summary 必须简短客观，只概括本 sheet 画面/字幕主旨，不写“安全/可疑/违规”。\n\n"
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
            "summary": self._truncate_text(analysis.get("summary", ""), 80),
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
            rule_id = self._normalize_stage_rule_id(
                item.get("rule_id") or item.get("id"),
                "video_frame_evidence",
            )
            if self._is_ruleset_v2() and not rule_id:
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
                **(
                    {"rule_id": rule_id}
                    if self._is_ruleset_v2() and rule_id
                    else {}
                ),
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

    def _translate_transcript_if_needed(self, transcript: dict, video_label: str) -> dict:
        text = self._strip_asr_control_tokens(transcript.get("text"))
        cleaned_segments = []
        for segment in transcript.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            segment_text = self._strip_asr_control_tokens(segment.get("text"))
            if segment_text:
                cleaned_segments.append({**segment, "text": segment_text})
        if not text and cleaned_segments:
            text = " ".join(segment["text"] for segment in cleaned_segments)
        transcript["text"] = text
        transcript["segments"] = cleaned_segments
        language = str(transcript.get("language") or "")
        if not text:
            transcript["translation"] = {
                "translated": False,
                "skipped": True,
                "text": "",
                "reason": "no valid ASR speech text",
            }
            job_store.log(self.job_id, f"{video_label}：ASR 未识别到有效语音文本，跳过翻译")
            return transcript
        if not self.translator.should_translate(text, language, trust_language_label=True):
            return transcript

        if settings.asr_translate_engine == "qwen_text":
            job_store.log(
                self.job_id,
                f"{video_label}：开始 LLM ASR 全文翻译，model={settings.asr_translate_model}，"
                "按 Dolphin 原分段回填时间戳",
            )
            translation = self._translate_asr_segments_with_llm(transcript)
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
                )
                if translation.get("split_retry"):
                    detail += f"，自动拆批={int(translation.get('batch_count') or 0)}"
            job_store.log(self.job_id, f"{video_label}：ASR 翻译完成，译文长度={len(translation['text'])}{detail}")
        else:
            job_store.log(self.job_id, f"{video_label}：ASR 翻译未完成：{translation.get('error') or translation.get('reason')}")
        return transcript

    @staticmethod
    def _strip_asr_control_tokens(value) -> str:
        text = str(value or "").strip()
        return re.sub(r"<[^<>]{1,32}>", "", text).strip()

    def _translate_asr_segments_with_llm(self, transcript: dict) -> dict:
        segments = self._compact_asr_segments_for_translation(transcript)
        if not segments:
            return {"translated": False, "text": "", "reason": "no ASR segments"}
        started = perf_counter()
        try:
            result, prompt_chars = self._request_asr_translation_batch(
                transcript,
                segments,
                context_segments=segments,
            )
        except Exception as exc:
            return {"translated": False, "text": "", "error": str(exc), "provider": "qwen_text"}

        translation = self._normalize_asr_translation(result, segments)
        if not self._asr_translation_result_complete(result, segments) and len(segments) > 1:
            finish_reason = str((result.get("_llm_meta") or {}).get("finish_reason") or "unknown")
            job_store.log(
                self.job_id,
                f"ASR 翻译返回不完整，自动按段拆批重试：segments={len(segments)}，"
                f"finish_reason={finish_reason}",
            )
            midpoint = (len(segments) + 1) // 2
            resolved_batches: list[dict] = []
            batch_attempts: list[dict] = []
            for batch in (segments[:midpoint], segments[midpoint:]):
                resolved, attempts, batch_prompt_chars = self._resolve_asr_translation_batch(
                    transcript,
                    batch,
                    context_segments=segments,
                )
                resolved_batches.extend(resolved)
                batch_attempts.extend(attempts)
                prompt_chars += batch_prompt_chars

            merged_result, missing_indexes = self._merge_asr_translation_batches(
                resolved_batches,
                segments,
            )
            translation = self._normalize_asr_translation(merged_result, segments)
            translation["raw"] = {
                "split_retry": True,
                "initial_response": result,
                "batch_attempts": batch_attempts,
            }
            translation["split_retry"] = True
            translation["batch_count"] = len(resolved_batches)
            if missing_indexes:
                preview = ",".join(str(index) for index in missing_indexes[:20])
                suffix = ",..." if len(missing_indexes) > 20 else ""
                translation["translated"] = False
                translation["error"] = (
                    "ASR translation split retry incomplete "
                    f"(missing_indexes={preview}{suffix})"
                )

        translation["elapsed_seconds"] = perf_counter() - started
        translation["prompt_chars"] = prompt_chars
        translation["segment_count"] = len(segments)
        return translation

    def _request_asr_translation_batch(
        self,
        transcript: dict,
        segments: list[dict],
        *,
        context_segments: list[dict],
    ) -> tuple[dict, int]:
        prompt = self._render_asr_translation_prompt(
            transcript=transcript,
            segments=segments,
            context_segments=context_segments,
        )
        result = self.qwen.audit_text(
            prompt,
            max_tokens=settings.asr_translate_max_tokens,
            model=settings.asr_translate_model,
            enable_thinking=settings.asr_translate_enable_thinking,
        )
        return result, len(prompt)

    def _resolve_asr_translation_batch(
        self,
        transcript: dict,
        segments: list[dict],
        *,
        context_segments: list[dict],
    ) -> tuple[list[dict], list[dict], int]:
        indexes = [int(item["index"]) for item in segments]
        try:
            result, prompt_chars = self._request_asr_translation_batch(
                transcript,
                segments,
                context_segments=context_segments,
            )
        except Exception as exc:
            record = {"indexes": indexes, "error": str(exc), "result": {}}
            return [record], [record], 0

        record = {"indexes": indexes, "result": result}
        return [record], [record], prompt_chars

    @staticmethod
    def _asr_translation_result_complete(result: dict, source_segments: list[dict]) -> bool:
        if not isinstance(result, dict):
            return False
        finish_reason = str((result.get("_llm_meta") or {}).get("finish_reason") or "").lower()
        if finish_reason in {"length", "max_tokens"} or result.get("raw_response"):
            return False
        translated_indexes = set()
        for item in result.get("segments") or []:
            if not isinstance(item, dict) or not str(item.get("translation_zh") or "").strip():
                continue
            try:
                translated_indexes.add(int(item.get("index") or 0))
            except (TypeError, ValueError):
                continue
        expected_indexes = {int(item["index"]) for item in source_segments}
        return bool(expected_indexes) and expected_indexes.issubset(translated_indexes)

    @staticmethod
    def _merge_asr_translation_batches(
        records: list[dict],
        source_segments: list[dict],
    ) -> tuple[dict, list[int]]:
        expected_indexes = {int(item["index"]) for item in source_segments}
        translated_by_index: dict[int, dict] = {}
        for record in records:
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            for item in result.get("segments") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index") or 0)
                except (TypeError, ValueError):
                    continue
                if (
                    index in expected_indexes
                    and str(item.get("translation_zh") or "").strip()
                ):
                    translated_by_index[index] = item

        merged_segments = [
            translated_by_index[int(source["index"])]
            for source in source_segments
            if int(source["index"]) in translated_by_index
        ]
        global_translation = " ".join(
            str(item.get("translation_zh") or "").strip()
            for item in merged_segments
            if str(item.get("translation_zh") or "").strip()
        )
        missing_indexes = sorted(expected_indexes - set(translated_by_index))
        return {
            "segments": merged_segments,
            "global_translation_zh": global_translation,
        }, missing_indexes

    def _compact_asr_segments_for_translation(self, transcript: dict) -> list[dict]:
        out: list[dict] = []
        for idx, seg in enumerate(transcript.get("segments") or [], start=1):
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") if seg.get("end") is not None else start)
            except (TypeError, ValueError):
                start = 0.0
                end = 0.0
            out.append({
                "index": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
            })
        if out:
            return out
        text = str(transcript.get("text") or "").strip()
        return [{"index": 1, "start": 0.0, "end": 0.0, "text": text}] if text else []

    def _render_asr_translation_prompt(
        self,
        *,
        transcript: dict,
        segments: list[dict],
        context_segments: list[dict] | None = None,
    ) -> str:
        full_context = context_segments or segments
        full_source_text = "\n".join(
            f"[{item['index']}] {str(item.get('text') or '').strip()}"
            for item in full_context
        ).strip()
        payload = {
            "language": transcript.get("language") or "ug",
            "primary_asr_engine": transcript.get("asr_engine") or transcript.get("provider") or "dolphin",
            "full_source_text": full_source_text,
        }
        target_indexes = [int(item["index"]) for item in segments]
        batch_mode = target_indexes != [int(item["index"]) for item in full_context]
        if batch_mode:
            payload["target_indexes"] = target_indexes
        global_translation_scope = (
            "只合并 target_indexes 对应译文，不得包含其他 index"
            if batch_mode
            else "按顺序合并全部 index 的完整中文译文"
        )
        return (
            "你是维吾尔语 ASR 复核与中文翻译器。先完整阅读 full_source_text，把它作为一段连续讲话理解全文；"
            "方括号数字仅用于之后对齐，不是语义边界。结合全文语境复核 Dolphin 可能出现的错听、连写或非标准拼写，"
            "完成全文理解和翻译后，再将中文译文按原 index 对齐。不要合并或拆分 index；"
            "如果输入包含 target_indexes，本次只输出这些 index，且不得遗漏；否则输出全部 index。"
            "global_translation_zh 也只能合并本次要求输出的 index。"
            "不要返回或推断时间戳。\n"
            "译文应忠实、自然，不要逐词机械直译，也不要扩写原文没有的信息。"
            "如果存在 ASR 疑词，在 notes 中简短说明；不要把不确定猜测写成确定事实。\n\n"
            "只输出合法 JSON，不要输出 Markdown 或 JSON 之外的文字，字段必须完全一致：\n"
            "{\n"
            '  "segments": [\n'
            "    {\n"
            '      "index": 1,\n'
            '      "source_text": "该 index 的原 ASR 文本",\n'
            '      "translation_zh": "中文译文",\n'
            '      "confidence": "high|medium|low",\n'
            '      "notes": "必要时说明 ASR 疑词，否则为空"\n'
            "    }\n"
            "  ],\n"
            f'  "global_translation_zh": "{global_translation_scope}"\n'
            "}\n\n"
            + "输入 JSON：\n"
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
                "source_text": source["text"],
                "source_text_mms": "",
                "translation_zh": translation,
                "mms_reference_used": False,
                "confidence": str(item.get("confidence") or "medium").lower(),
                "notes": self._truncate_text(item.get("notes", ""), 180),
            })
        global_text = self._truncate_text(
            result.get("global_translation_zh", "") or " ".join(seg["translation_zh"] for seg in segments if seg["translation_zh"]),
            settings.transcript_max_chars,
        )
        normalized = {
            "translated": bool(global_text),
            "provider": "qwen_text",
            "model": settings.asr_translate_model,
            "text": global_text,
            "segments": segments,
            "raw": result,
        }
        raw_response = str(result.get("raw_response") or "")
        if raw_response and not global_text:
            normalized["error"] = (
                "ASR translation returned incomplete or invalid JSON "
                f"(response_chars={len(raw_response)})"
            )
        return normalized

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
                    "source_text_mms": item.get("source_text_mms", ""),
                    "translation_zh": item.get("translation_zh", ""),
                    "mms_reference_used": item.get("mms_reference_used", False),
                    "translation_confidence": item.get("confidence", ""),
                    "translation_notes": item.get("notes", ""),
                }
            else:
                seg = {
                    **seg,
                    "source_text_dolphin": seg.get("text", ""),
                    "source_text_mms": "",
                }
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
