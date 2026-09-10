from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


_RUNTIME_ROOT = Path(
    tempfile.mkdtemp(prefix="m3-provider-closure-tests-", dir="/tmp")
)
os.environ["XHS_AUDIT_DATA_DIR"] = str(_RUNTIME_ROOT / "data")
os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(_RUNTIME_ROOT / "outputs")
os.environ["PYTHONPYCACHEPREFIX"] = str(_RUNTIME_ROOT / "pycache")
os.environ["TMPDIR"] = str(_RUNTIME_ROOT / "tmp")
os.environ["HERMES_HOME"] = str(_RUNTIME_ROOT / "hermes")
(_RUNTIME_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, _RUNTIME_ROOT, True)

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlOutput
from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import (
    AuditPipeline,
    AuditProviderCallError,
)
from backend.audit_agent.prompts import get_prompt_set
from backend.audit_agent.qwen_client import QwenClient
from backend.audit_agent.video_processor import DemoAudioProcessor
from backend.investigation_creation.contracts import (
    AuditPolicySummary,
    ConfirmAndQueueCommand,
    ConfirmedRecallPlanSnapshot,
    CreateDraftCommand,
    ResolvedExecutionConfiguration,
    RuleSetRevisionSummary,
    RunStatus,
    confirmed_configuration_hash,
)
from backend.investigation_creation.principal import LOCAL_PRINCIPAL_ID, Principal
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.worker import InvestigationWorker


class OneItemCrawler:
    def __init__(self, item: dict) -> None:
        self.item = dict(item)

    def _run(self, **kwargs) -> CrawlOutput:
        callback = kwargs.get("content_callback")
        if callback:
            callback([dict(self.item)], [])
        return CrawlOutput(
            platform=kwargs["platform"],
            contents=[dict(self.item)],
            comments=[],
            output_dir=kwargs["save_root"],
            command=["explicit-test-crawler"],
        )

    def run_search(self, **kwargs) -> CrawlOutput:
        return self._run(**kwargs)

    def run_creator(self, **kwargs) -> CrawlOutput:
        return self._run(**kwargs)


class ExplicitQwenTestDouble:
    enabled = True
    provider_failure = ""

    def __init__(
        self,
        *,
        visual_error: Exception | None = None,
        image_response: object | None = None,
        video_response: object | None = None,
    ) -> None:
        self.visual_error = visual_error
        self.image_response = image_response
        self.video_response = video_response
        self.visual_calls = 0

    def analyze_image(self, *_args, **kwargs) -> object:
        self.visual_calls += 1
        if self.visual_error is not None:
            raise self.visual_error
        if kwargs.get("model") == "test-only-video-model":
            if self.video_response is not None:
                return self.video_response
            return {
                "segment_summary": "explicit deterministic test segment",
                "segment_score": 0,
                "risk_library_id": "soft",
                "risk_library_label": "soft",
                "visual_risks": [],
                "ocr_risks": [],
                "asr_risks": [],
            }
        if self.image_response is not None:
            return self.image_response
        return {
            "visual_summary": "explicit deterministic test visual result",
            "risk_items": [],
        }


class ExplicitAudioTestDouble:
    def __init__(
        self,
        audio_path: Path | None,
        transcript: object = None,
        *,
        extract_error: Exception | None = None,
        extract_status: str = "success",
    ) -> None:
        self.audio_path = audio_path
        self.transcript = transcript
        self.extract_error = extract_error
        self.last_extract_status = extract_status
        self.last_extract_error = "explicit extraction failure"

    def extract_audio(self, _video_path: Path, _output_dir: Path) -> Path | None:
        if self.extract_error is not None:
            raise self.extract_error
        return self.audio_path

    def transcribe(self, _audio_path: Path) -> dict:
        if isinstance(self.transcript, BaseException):
            raise self.transcript
        return self.transcript  # type: ignore[return-value]


class ExplicitFramesTestDouble:
    def extract_timeline_frames(
        self, *, video_path: Path, output_dir: Path, max_frames: int
    ) -> list[dict]:
        assert video_path.exists()
        assert max_frames > 0
        output_dir.mkdir(parents=True, exist_ok=True)
        frame_path = output_dir / "frame_0001.jpg"
        frame_path.write_bytes(b"explicit-test-frame")
        return [
            {
                "path": str(frame_path),
                "timestamp": 0.5,
                "frame_id": "f0001",
                "frame_number": 1,
            }
        ]

    @staticmethod
    def video_meta(_video_path: Path) -> tuple[int, float, float]:
        return 1, 1.0, 1.0

    @staticmethod
    def create_contact_sheet(
        _frames: list[dict], output_path: Path, *, columns: int, rows: int
    ) -> Path:
        assert columns > 0
        assert rows > 0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"explicit-test-contact-sheet")
        return output_path


class ExplicitPipelineWorkerAdapter:
    def __init__(
        self,
        *,
        jobs: JobStore,
        ingestion: IngestionStore,
        audit_results: AuditResultStore,
        subject: AuditSubject,
        qwen: ExplicitQwenTestDouble,
        fusion_result: dict | None = None,
    ) -> None:
        self.jobs = jobs
        self.ingestion = ingestion
        self.audit_results = audit_results
        self.subject = subject
        self.qwen = qwen
        self.fusion_result = dict(fusion_result) if fusion_result is not None else None
        self.pipeline_configurations: list[dict] = []

    @staticmethod
    def job_id_for_run(run_id: str) -> str:
        return f"visual-p1-{run_id[-16:]}"

    def ensure_job(self, run) -> str:
        job_id = self.job_id_for_run(run.id)
        if self.jobs.get(job_id) is None:
            execution = run.confirmed_configuration["execution"]
            self.jobs.create(
                job_id=job_id,
                **{
                    key: value
                    for key, value in execution.items()
                    if key
                    in {
                        "platform",
                        "display_name",
                        "crawl_mode",
                        "keyword",
                        "keyword_source",
                        "lexicon_category",
                        "library_ids",
                        "capabilities",
                        "scoring_template",
                        "rule_snapshot",
                        "lexicon_keywords",
                        "creator_url",
                        "creator_id",
                        "start_page",
                        "max_notes",
                        "max_comments",
                        "max_concurrency",
                        "max_items_per_minute",
                        "get_sub_comment",
                        "analyze_limit",
                        "run_crawler",
                        "source_output_id",
                        "analysis_batch_size",
                        "prompt_profile_snapshot",
                    }
                },
            )
        return job_id

    def run_pipeline(self, job_id: str, configuration: dict) -> None:
        self.pipeline_configurations.append(dict(configuration))
        pipeline = AuditPipeline.__new__(AuditPipeline)
        pipeline.job_id = job_id
        pipeline.crawler = OneItemCrawler(
            {"note_id": "selected-content", "title": "selected"}
        )
        pipeline.audio = ExplicitAudioTestDouble(
            None, extract_status="no_audio_track"
        )
        pipeline.qwen = self.qwen
        pipeline.translator = None
        pipeline.frames = ExplicitFramesTestDouble()
        pipeline.ocr = SimpleNamespace(
            scan_image=lambda *_args, **_kwargs: {
                "text": "",
                "text_zh": "",
                "confidence": 0.0,
                "engine": "explicit-test-disabled-ocr",
                "enabled": False,
            }
        )
        pipeline.ingestion = self.ingestion
        pipeline.audit_results = self.audit_results
        pipeline.prompt_set = get_prompt_set("soft")
        pipeline.prompt_profile_snapshot = {}
        pipeline.audit_config_revision_id = ""
        pipeline.rule_snapshot = {}
        pipeline.authoritative_m3 = False
        pipeline._build_subjects = lambda *_args, **_kwargs: [self.subject]
        if self.fusion_result is not None:
            explicit_fusion_result = dict(self.fusion_result)
            pipeline._run_fusion_audit = lambda *_args, **_kwargs: {
                **explicit_fusion_result,
                "_provider_provenance": {
                    "provider": "explicit-test-text-provider",
                    "model": "explicit-test-text-model",
                    "prompt_sha256": "a" * 64,
                    "prompt_version": pipeline.prompt_set.prompt_version,
                },
            }
        pipeline.run(SimpleNamespace(**configuration))

    def get_job_state(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return {
            "status": job["status"],
            "error": job.get("error") or "",
            "task_stats": self.ingestion.stats_for_task(job_id),
            "audit_results": self.audit_results.list_results(job_id=job_id)[
                "items"
            ],
        }

    def validate_selected_content_payloads(self, job_id: str) -> None:
        self.ingestion.validated_selection_for_task(job_id)


class ExplicitConfigurationResolver:
    def __init__(self, mode: str = "search") -> None:
        self.mode = mode

    def resolve(self, _draft_configuration) -> dict:
        execution = {
            key: value
            for key, value in _configuration(self.mode).items()
            if not key.startswith("_")
        }
        execution["audit_config_revision"] = {
            "source_policy_id": "",
            "source_policy_name": "Explicit test policy",
            "source_policy_version": "",
            "audit_config": {},
            "knowledge_package_snapshots": [],
            "rule_snapshot": {},
            "prompt_profile_snapshot": {},
            "config_hash": "explicit-test-config-hash",
        }
        return execution


class ExplicitUnusedReportAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def find_published(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("visual failure must not query reports")

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("visual failure must not generate reports")

    def verify_published(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("visual failure must not verify reports")


class ExplicitUnusedSessionAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_session(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("visual failure must not create a report session")


class ExplicitV2ReportAdapter:
    def __init__(self) -> None:
        self.published: dict[str, tuple[str, str]] = {}

    def find_published(self, task_id: str, *, r31_run_id: str = ""):
        existing = self.published.get(task_id)
        if existing is None:
            return None
        assert not r31_run_id or r31_run_id == existing[0]
        return existing[1]

    def generate(self, task_id: str, *, on_generation_started):
        r31_run_id = "report-run:v2-provider-compat"
        version_id = "report-version:v2-provider-compat"
        on_generation_started(r31_run_id, version_id)
        self.published[task_id] = (r31_run_id, version_id)
        return version_id

    def verify_published(self, report_version_id: str, *, task_id: str) -> None:
        assert self.published[task_id][1] == report_version_id


class ExplicitV2SessionAdapter:
    def ensure_session(self, run_id: str, report_version_id: str) -> str:
        assert run_id
        assert report_version_id == "report-version:v2-provider-compat"
        return "investigation-session:v2-provider-compat"


def _configuration(
    mode: str = "search", *, capabilities: list[str] | None = None
) -> dict:
    creator = (
        "https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753"
        "?xsec_token=test-token&xsec_source=pc_user"
        if mode == "creator"
        else ""
    )
    return {
        "platform": "xhs",
        "display_name": "M3 Provider Closure",
        "crawl_mode": mode,
        "keyword": "provider closure" if mode == "search" else "",
        "keyword_source": "keyword",
        "lexicon_category": "soft",
        "library_ids": ["soft"],
        "capabilities": list(capabilities or []),
        "scoring_template": "balanced",
        "rule_snapshot": {},
        "lexicon_keywords": [],
        "creator_url": creator,
        "creator_id": creator,
        "start_page": 1,
        "max_notes": 1,
        "max_comments": 0,
        "max_concurrency": 1,
        "max_items_per_minute": 1,
        "crawler_account_id": None,
        "get_sub_comment": False,
        "analyze_limit": 1,
        "run_crawler": True,
        "source_output_id": None,
        "analysis_batch_size": 1,
        "prompt_profile_snapshot": {},
        "policy_id": "",
        "_confirmed_analyze_limit": 1,
        "_authoritative_m3_contract": True,
    }


def _subject(*, video: Path | None = None, image: Path | None = None) -> AuditSubject:
    return AuditSubject(
        platform="xhs",
        note_id="selected-content",
        url="https://www.xiaohongshu.com/explore/selected-content",
        title="",
        desc="",
        author={"user_id": "content-author"},
        image_urls=[],
        video_urls=[],
        comments=[],
        local_image_paths=[str(image)] if image else [],
        local_video_paths=[str(video)] if video else [],
    )


def _run_authoritative_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    subject: AuditSubject,
    audio: ExplicitAudioTestDouble,
    qwen: ExplicitQwenTestDouble | None = None,
    mode: str = "search",
    capabilities: list[str] | None = None,
    configure_vision: bool = True,
    use_remote_vlm: bool = False,
    fusion_result: dict | None = None,
    authoritative_m3: bool = True,
) -> tuple[dict, dict, dict]:
    db_path = tmp_path / f"{mode}-provider.sqlite3"
    jobs = JobStore(db_path)
    ingestion = IngestionStore(db_path)
    audit_results = AuditResultStore(db_path)
    configuration = _configuration(mode, capabilities=capabilities)
    if not authoritative_m3:
        configuration.pop("_authoritative_m3_contract")
    job_id = f"provider-closure-{mode}"
    jobs.create(
        job_id=job_id,
        **{
            key: value
            for key, value in configuration.items()
            if not key.startswith("_")
        },
    )
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(pipeline_module.settings, "stream_crawl_analysis", False)
    monkeypatch.setattr(
        pipeline_module.settings, "auto_analyze_crawled_content", True
    )
    monkeypatch.setattr(pipeline_module.settings, "use_remote_vlm", use_remote_vlm)
    monkeypatch.setattr(
        pipeline_module.settings,
        "remote_inference_base_url",
        "http://explicit-test-remote" if use_remote_vlm else "",
    )
    monkeypatch.setattr(
        pipeline_module.settings,
        "dashscope_api_key",
        "test-only-key" if configure_vision else "",
    )
    monkeypatch.setattr(
        pipeline_module.settings,
        "qwen_image_audit_model",
        "test-only-image-model" if configure_vision else "",
    )
    monkeypatch.setattr(
        pipeline_module.settings,
        "qwen_contact_sheet_model",
        "test-only-video-model" if configure_vision else "",
    )
    monkeypatch.setattr(pipeline_module.settings, "ocr_enabled", False)
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = OneItemCrawler(
        {"note_id": "selected-content", "title": "selected"}
    )
    pipeline.audio = audio
    pipeline.qwen = qwen or ExplicitQwenTestDouble()
    pipeline.translator = None
    pipeline.frames = ExplicitFramesTestDouble()
    pipeline.ocr = SimpleNamespace(
        scan_image=lambda *_args, **_kwargs: {
            "text": "",
            "text_zh": "",
            "confidence": 0.0,
            "engine": "explicit-test-disabled-ocr",
            "enabled": False,
        }
    )
    pipeline.ingestion = ingestion
    pipeline.audit_results = audit_results
    pipeline.prompt_set = get_prompt_set("soft")
    pipeline.prompt_profile_snapshot = {}
    pipeline.audit_config_revision_id = ""
    pipeline.rule_snapshot = {}
    pipeline.authoritative_m3 = False
    pipeline._build_subjects = lambda *_args, **_kwargs: [subject]
    if fusion_result is not None:
        explicit_fusion_result = dict(fusion_result)
        pipeline._run_fusion_audit = lambda *_args, **_kwargs: {
            **explicit_fusion_result,
            "_provider_provenance": {
                "provider": "explicit-test-text-provider",
                "model": "explicit-test-text-model",
                "prompt_sha256": "a" * 64,
                "prompt_version": pipeline.prompt_set.prompt_version,
            },
        }

    pipeline.run(SimpleNamespace(**configuration))
    assert pipeline.authoritative_m3 is authoritative_m3

    return jobs.get(job_id), ingestion.stats_for_task(job_id), audit_results.list_results(
        job_id=job_id
    )


def _fusion_result(*, decision: str = "pass", risk_level: str = "none") -> dict:
    return {
        "content_title": "显式测试内容",
        "summary": "显式测试融合审核摘要",
        "decision": decision,
        "risk_level": risk_level,
        "risk_score": 0 if risk_level == "none" else 90,
        "primary_risk": "" if risk_level == "none" else "测试风险",
        "categories": [] if risk_level == "none" else ["测试风险"],
        "category_scores": [],
        "score_breakdown": [],
        "evidence": [],
    }


@pytest.mark.parametrize(
    "transcript",
    [
        TimeoutError("explicit ASR timeout"),
        {"text": "", "segments": [], "error": "explicit ASR error"},
        {"text": [], "segments": "invalid", "provider": "test-asr"},
    ],
    ids=["timeout", "explicit-error", "invalid-structure"],
)
@pytest.mark.parametrize("mode", ["search", "creator"])
def test_authoritative_video_asr_failure_never_completes_audit_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transcript: object,
    mode: str,
) -> None:
    video = tmp_path / f"{mode}.mp4"
    audio_path = tmp_path / f"{mode}.wav"
    video.write_bytes(b"explicit-test-video")
    audio_path.write_bytes(b"explicit-test-audio")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(video=video),
        audio=ExplicitAudioTestDouble(audio_path, transcript),
        mode=mode,
    )

    assert job["status"] == "completed"
    assert stats["failed_analysis_count"] == 1
    assert result_page["total"] == 0
    assert stats["completed_analysis_count"] == 0
    assert stats["failed_analysis_count"] == 1


def test_authoritative_video_audio_extraction_exception_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "extract.mp4"
    video.write_bytes(b"explicit-test-video")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(video=video),
        audio=ExplicitAudioTestDouble(
            None,
            extract_error=RuntimeError("explicit extraction exception"),
            extract_status="failed",
        ),
    )

    assert job["status"] == "completed"
    assert stats["failed_analysis_count"] == 1
    assert result_page["total"] == 0
    assert stats["completed_analysis_count"] == 0


def test_authoritative_video_missing_asr_configuration_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "missing-asr.mp4"
    audio_path = tmp_path / "missing-asr.wav"
    video.write_bytes(b"explicit-test-video")
    audio_path.write_bytes(b"explicit-test-audio")
    monkeypatch.setattr(pipeline_module.settings, "use_remote_asr", False)
    monkeypatch.setattr(pipeline_module.settings, "asr_engine", "dolphin")
    monkeypatch.setattr(pipeline_module.settings, "dolphin_model", "")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(video=video),
        audio=ExplicitAudioTestDouble(
            audio_path,
            {"text": "unused", "segments": [], "provider": "test-asr"},
        ),
    )

    assert job["status"] == "analysis_stopped"
    assert stats["failed_analysis_count"] == 1
    assert result_page["total"] == 0
    assert stats["completed_analysis_count"] == 0


@pytest.mark.parametrize("capabilities", [[], ["text"]])
@pytest.mark.parametrize("media_kind", ["image", "video"])
def test_visual_configuration_comes_from_selected_content_modality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: list[str],
    media_kind: str,
) -> None:
    media = tmp_path / ("content.jpg" if media_kind == "image" else "content.mp4")
    media.write_bytes(b"explicit-test-media")
    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(
            image=media if media_kind == "image" else None,
            video=media if media_kind == "video" else None,
        ),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        capabilities=capabilities,
        configure_vision=False,
    )

    assert job["status"] == "analysis_stopped"
    assert stats["failed_analysis_count"] == 1
    assert result_page["total"] == 0
    assert stats["completed_analysis_count"] == 0


def test_authoritative_text_only_does_not_require_vision_or_asr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_module.settings, "use_remote_vlm", False)
    monkeypatch.setattr(pipeline_module.settings, "dashscope_api_key", "")
    monkeypatch.setattr(pipeline_module.settings, "qwen_image_audit_model", "")
    monkeypatch.setattr(pipeline_module.settings, "qwen_contact_sheet_model", "")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(),
        audio=ExplicitAudioTestDouble(
            None,
            extract_error=AssertionError("text-only content must not call audio"),
        ),
        configure_vision=False,
        fusion_result=_fusion_result(),
    )

    assert job["status"] == "completed"
    assert stats["completed_analysis_count"] == 1
    assert result_page["total"] == 1
    assert result_page["items"][0]["model_provenance"]["completed_modalities"] == [
        "text"
    ]


def test_authoritative_visual_provider_failure_never_persists_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "visual-failure.jpg"
    image.write_bytes(b"explicit-test-image")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(image=image),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        qwen=ExplicitQwenTestDouble(
            visual_error=TimeoutError("explicit VLM timeout")
        ),
    )

    assert job["status"] == "completed"
    assert stats["failed_analysis_count"] == 1
    assert result_page["total"] == 0
    assert stats["completed_analysis_count"] == 0


@pytest.mark.parametrize("mode", ["search", "creator"])
@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        {"raw_response": "not-json"},
        {"foo": "bar"},
        {"visual_summary": "missing risks"},
        {
            "visual_summary": "provider error",
            "risk_items": [],
            "error": "explicit provider error",
        },
        {"visual_summary": [], "risk_items": []},
        {"visual_summary": "invalid list", "risk_items": "not-a-list"},
        {
            "visual_summary": "invalid risk item",
            "risk_items": ["not-an-object"],
        },
        {
            "visual_summary": "invalid severity",
            "risk_items": [
                {
                    "risk_type": "test",
                    "evidence": "test evidence",
                    "reason": "test reason",
                    "severity": "critical",
                }
            ],
        },
    ],
    ids=[
        "non-json",
        "raw-response",
        "unrelated-object",
        "missing-required-field",
        "provider-error",
        "summary-type",
        "risk-list-type",
        "risk-item-type",
        "severity-enum",
    ],
)
def test_authoritative_image_invalid_response_fails_full_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    response: object,
) -> None:
    image = tmp_path / f"{mode}-invalid.jpg"
    image.write_bytes(b"explicit-test-image")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(image=image),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        qwen=ExplicitQwenTestDouble(image_response=response),
        mode=mode,
    )

    assert job["status"] == "completed"
    assert stats["failed_analysis_count"] == 1
    assert stats["completed_analysis_count"] == 0
    assert result_page["total"] == 0


def test_authoritative_image_valid_no_risk_response_completes_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "valid-no-risk.jpg"
    image.write_bytes(b"explicit-test-image")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(image=image),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        qwen=ExplicitQwenTestDouble(
            image_response={
                "ocr_text": "",
                "visual_summary": "ordinary scene",
                "benign_context": "ordinary life record",
                "risk_items": [],
            }
        ),
        fusion_result=_fusion_result(),
    )

    assert job["status"] == "completed"
    assert stats["completed_analysis_count"] == 1
    assert result_page["total"] == 1


@pytest.mark.parametrize("use_remote_vlm", [False, True], ids=["dashscope", "remote"])
def test_authoritative_image_contract_applies_to_both_visual_provider_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote_vlm: bool,
) -> None:
    image = tmp_path / f"route-{use_remote_vlm}.jpg"
    image.write_bytes(b"explicit-test-image")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(image=image),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        qwen=ExplicitQwenTestDouble(
            image_response={"raw_response": "not-json"}
        ),
        use_remote_vlm=use_remote_vlm,
    )

    assert job["status"] == "completed"
    assert stats["failed_analysis_count"] == 1
    assert stats["completed_analysis_count"] == 0
    assert result_page["total"] == 0


def test_invalid_visual_response_fails_worker_run_without_report_or_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "worker-visual-failure.sqlite3"
    jobs = JobStore(db_path)
    ingestion = IngestionStore(db_path)
    audit_results = AuditResultStore(db_path)
    run_store = InvestigationCreationStore(db_path)
    service = InvestigationCreationService(
        run_store, configuration_resolver=ExplicitConfigurationResolver()
    )
    principal = Principal(LOCAL_PRINCIPAL_ID)
    draft = service.create_draft(
        CreateDraftCommand(
            title="Visual Provider P1",
            objective="Prove invalid visual output fails before audit completion.",
            configuration={
                "platform": "xhs",
                "collection": {
                    "crawl_mode": "search",
                    "keyword_source": "keyword",
                    "keywords": ["visual p1"],
                    "run_crawler": True,
                },
                "analysis": {
                    "library_ids": ["soft"],
                    "capabilities": ["text", "vision"],
                    "scoring_template": "balanced",
                    "analyze_limit": 1,
                },
            },
        ),
        principal=principal,
    )
    execution_snapshot = ExplicitConfigurationResolver().resolve(None)
    resolution_payload = {
        "mode": "search",
        "platform": "xhs",
        "resolved_search_terms": ["provider closure"],
        "creator_url": "",
        "recall_plan": ConfirmedRecallPlanSnapshot(
            strategy="temporary_terms",
            temporary_terms=["provider closure"],
        ).model_dump(mode="json"),
        "audit_policy": AuditPolicySummary(
            id="audit-policy:visual-p1",
            name="Visual P1",
            published_version="1",
            published_config_hash="a" * 64,
            ruleset_revision_id="ruleset-revision:visual-p1",
            ruleset_version=1,
            ruleset_content_hash="b" * 64,
            domain="content_safety",
        ).model_dump(mode="json"),
        "ruleset_revision": RuleSetRevisionSummary(
            id="ruleset-revision:visual-p1",
            ruleset_id="ruleset:visual-p1",
            name="Visual P1",
            domain="content_safety",
            version=1,
            content_hash="b" * 64,
            enabled_rule_count=1,
        ).model_dump(mode="json"),
        "execution": ResolvedExecutionConfiguration.model_validate(
            execution_snapshot
        ).model_dump(mode="json"),
    }
    resolution_payload["config_hash"] = confirmed_configuration_hash(
        resolution_payload
    )
    queued = run_store.confirm_and_queue(
        draft.id,
        principal=principal.id,
        expected_revision=1,
        confirmed=True,
        idempotency_key="visual-p1-worker",
        request_fingerprint=run_store.confirmation_fingerprint(
            principal=principal.id,
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            confirmed_configuration_hash=resolution_payload["config_hash"],
        ),
        resolved_configuration=execution_snapshot,
        confirmation_resolution=resolution_payload,
    )
    assert queued.confirmed_configuration["schema_version"] == (
        "investigation-run-config-v3"
    )
    assert "_authoritative_m3_contract" not in json.dumps(
        queued.confirmed_configuration
    )
    image = tmp_path / "worker-invalid.jpg"
    image.write_bytes(b"explicit-test-image")
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(pipeline_module.settings, "stream_crawl_analysis", False)
    monkeypatch.setattr(
        pipeline_module.settings, "auto_analyze_crawled_content", True
    )
    monkeypatch.setattr(pipeline_module.settings, "use_remote_vlm", False)
    monkeypatch.setattr(
        pipeline_module.settings, "dashscope_api_key", "test-only-key"
    )
    monkeypatch.setattr(
        pipeline_module.settings,
        "qwen_image_audit_model",
        "test-only-image-model",
    )
    monkeypatch.setattr(pipeline_module.settings, "ocr_enabled", False)
    execution = ExplicitPipelineWorkerAdapter(
        jobs=jobs,
        ingestion=ingestion,
        audit_results=audit_results,
        subject=_subject(image=image),
        qwen=ExplicitQwenTestDouble(image_response={"foo": "bar"}),
    )
    report = ExplicitUnusedReportAdapter()
    session = ExplicitUnusedSessionAdapter()
    worker = InvestigationWorker(
        run_store,
        execution_adapter=execution,
        report_adapter=report,
        session_adapter=session,
        worker_id="explicit-visual-p1-worker",
        heartbeat_interval_seconds=0.05,
    )

    failed = worker.run_once()

    assert failed is not None
    assert failed.status == RunStatus.AUDIT_COMPLETED
    assert not failed.error_code
    assert failed.job_id == execution.job_id_for_run(queued.id)
    stats = ingestion.stats_for_task(failed.job_id)
    assert stats["completed_analysis_count"] == 0
    assert audit_results.list_results(job_id=failed.job_id)["total"] == 0
    assert run_store.get_report_binding(failed.id) is None
    assert report.calls == 0
    assert session.calls == 0
    assert execution.pipeline_configurations[0]["_authoritative_m3_contract"] is True
    assert "_authoritative_m3_contract" not in jobs.get(failed.job_id)


@pytest.mark.parametrize("mode", ["search", "creator"])
def test_v2_worker_pipeline_preserves_raw_response_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    db_path = tmp_path / f"v2-{mode}-raw-response.sqlite3"
    jobs = JobStore(db_path)
    ingestion = IngestionStore(db_path)
    audit_results = AuditResultStore(db_path)
    run_store = InvestigationCreationStore(db_path)
    service = InvestigationCreationService(
        run_store, configuration_resolver=ExplicitConfigurationResolver(mode)
    )
    principal = Principal(LOCAL_PRINCIPAL_ID)
    creator_url = (
        "https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753"
        "?xsec_token=test-token&xsec_source=pc_user"
        if mode == "creator"
        else ""
    )
    draft = service.create_draft(
        CreateDraftCommand(
            title="V2 visual Provider compatibility",
            objective="Preserve the legacy raw response contract.",
            configuration={
                "platform": "xhs",
                "collection": {
                    "crawl_mode": mode,
                    "keyword_source": "keyword",
                    "keywords": ["visual v2"] if mode == "search" else [],
                    "creator_url": creator_url,
                    "run_crawler": True,
                },
                "analysis": {
                    "library_ids": ["soft"],
                    "capabilities": ["text", "vision"],
                    "scoring_template": "balanced",
                    "analyze_limit": 1,
                },
            },
        ),
        principal=principal,
    )
    queued = service.confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key=f"v2-raw-response:{mode}",
        ),
        principal=principal,
    )
    assert queued.confirmed_configuration["schema_version"] == (
        "investigation-run-config-v2"
    )
    assert "_authoritative_m3_contract" not in json.dumps(
        queued.confirmed_configuration
    )

    image = tmp_path / f"v2-{mode}.jpg"
    image.write_bytes(b"explicit-test-image")
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(pipeline_module.settings, "stream_crawl_analysis", False)
    monkeypatch.setattr(
        pipeline_module.settings, "auto_analyze_crawled_content", True
    )
    monkeypatch.setattr(pipeline_module.settings, "use_remote_vlm", False)
    monkeypatch.setattr(
        pipeline_module.settings, "dashscope_api_key", "test-only-key"
    )
    monkeypatch.setattr(
        pipeline_module.settings,
        "qwen_image_audit_model",
        "test-only-image-model",
    )
    monkeypatch.setattr(pipeline_module.settings, "ocr_enabled", False)
    execution = ExplicitPipelineWorkerAdapter(
        jobs=jobs,
        ingestion=ingestion,
        audit_results=audit_results,
        subject=_subject(image=image),
        qwen=ExplicitQwenTestDouble(
            image_response={"raw_response": "not-json"}
        ),
        fusion_result=_fusion_result(),
    )
    completed = InvestigationWorker(
        run_store,
        execution_adapter=execution,
        report_adapter=ExplicitV2ReportAdapter(),
        session_adapter=ExplicitV2SessionAdapter(),
        worker_id=f"explicit-v2-{mode}-worker",
        heartbeat_interval_seconds=0.05,
    ).run_once()

    assert completed is not None
    assert completed.status == RunStatus.PUBLISHED
    pipeline_configuration = execution.pipeline_configurations[0]
    assert pipeline_configuration["_confirmed_analyze_limit"] == 1
    assert "_authoritative_m3_contract" not in pipeline_configuration
    job_id = execution.job_id_for_run(queued.id)
    assert jobs.get(job_id)["status"] == "completed"
    assert ingestion.stats_for_task(job_id)["completed_analysis_count"] == 1
    result_page = audit_results.list_results(job_id=job_id)
    assert result_page["total"] == 1
    assert result_page["items"][0]["image_analyses"][0]["raw_response"] == (
        "not-json"
    )
    assert "_authoritative_m3_contract" not in jobs.get(job_id)


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        {"foo": "bar"},
        {
            "segment_summary": "missing lists",
            "segment_score": 0,
            "risk_library_id": "soft",
            "risk_library_label": "soft",
        },
        {
            "segment_summary": "provider error",
            "segment_score": 0,
            "risk_library_id": "soft",
            "risk_library_label": "soft",
            "visual_risks": [],
            "ocr_risks": [],
            "asr_risks": [],
            "error": "explicit provider error",
        },
        {
            "segment_summary": "invalid risk list",
            "segment_score": 0,
            "risk_library_id": "soft",
            "risk_library_label": "soft",
            "visual_risks": {},
            "ocr_risks": [],
            "asr_risks": [],
        },
        {
            "segment_summary": "invalid risk item",
            "segment_score": 0,
            "risk_library_id": "soft",
            "risk_library_label": "soft",
            "visual_risks": ["not-an-object"],
            "ocr_risks": [],
            "asr_risks": [],
        },
        {
            "segment_summary": "invalid score",
            "segment_score": 101,
            "risk_library_id": "soft",
            "risk_library_label": "soft",
            "visual_risks": [],
            "ocr_risks": [],
            "asr_risks": [],
        },
    ],
    ids=[
        "non-json",
        "unrelated-object",
        "missing-required-field",
        "provider-error",
        "risk-list-type",
        "risk-item-type",
        "score-range",
    ],
)
def test_authoritative_video_invalid_response_fails_full_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> None:
    video = tmp_path / "invalid-contact-sheet.mp4"
    video.write_bytes(b"explicit-test-video")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(video=video),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        qwen=ExplicitQwenTestDouble(video_response=response),
    )

    assert job["status"] == "completed"
    assert stats["failed_analysis_count"] == 1
    assert stats["completed_analysis_count"] == 0
    assert result_page["total"] == 0


def test_non_authoritative_qwen_parse_preserves_raw_response_compatibility() -> None:
    client = QwenClient.__new__(QwenClient)

    assert client._parse_chat_json("not-json") == {"raw_response": "not-json"}


def test_non_authoritative_pipeline_preserves_raw_response_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "legacy-raw-response.jpg"
    image.write_bytes(b"explicit-test-image")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(image=image),
        audio=ExplicitAudioTestDouble(None, extract_status="no_audio_track"),
        qwen=ExplicitQwenTestDouble(
            image_response={"raw_response": "not-json"}
        ),
        fusion_result=_fusion_result(),
        authoritative_m3=False,
    )

    assert job["status"] == "completed"
    assert stats["completed_analysis_count"] == 1
    assert result_page["total"] == 1
    assert result_page["items"][0]["image_analyses"][0]["raw_response"] == (
        "not-json"
    )


@pytest.mark.parametrize(
    ("audio_kind", "expected_status", "expected_modality"),
    [
        ("no_speech", "no_speech", "asr"),
        ("no_audio_track", "no_audio_track", "no_audio_track"),
    ],
)
@pytest.mark.parametrize(
    ("decision", "risk_level"),
    [("pass", "none"), ("reject", "high")],
)
def test_authoritative_video_success_requires_visual_and_fusion_after_empty_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audio_kind: str,
    expected_status: str,
    expected_modality: str,
    decision: str,
    risk_level: str,
) -> None:
    video = tmp_path / f"{audio_kind}-{decision}.mp4"
    audio_path = tmp_path / "silence.wav"
    video.write_bytes(b"explicit-test-video")
    audio_path.write_bytes(b"explicit-test-silence")
    audio = (
        ExplicitAudioTestDouble(
            audio_path,
            {"text": "", "segments": [], "provider": "explicit-test-asr"},
        )
        if audio_kind == "no_speech"
        else ExplicitAudioTestDouble(None, extract_status="no_audio_track")
    )
    qwen = ExplicitQwenTestDouble()
    monkeypatch.setattr(pipeline_module.settings, "use_remote_asr", False)
    monkeypatch.setattr(pipeline_module.settings, "asr_engine", "dolphin")
    monkeypatch.setattr(pipeline_module.settings, "dolphin_model", "test-model")

    job, stats, result_page = _run_authoritative_pipeline(
        tmp_path,
        monkeypatch,
        subject=_subject(video=video),
        audio=audio,
        qwen=qwen,
        fusion_result=_fusion_result(decision=decision, risk_level=risk_level),
    )

    assert job["status"] == "completed", job["logs"][-1]
    assert stats["completed_analysis_count"] == 1
    assert result_page["total"] == 1
    assert qwen.visual_calls == 1
    result = result_page["items"][0]
    assert result["decision"] == decision
    assert result["risk_level"] == risk_level
    assert result["video_results"][0]["transcript"]["completion_status"] == expected_status
    assert "vision" in result["model_provenance"]["completed_modalities"]
    assert expected_modality in result["model_provenance"]["completed_modalities"]
    assert result["model_provenance"]["vision_models"] == [
        "test-only-video-model"
    ]


def test_only_deterministic_ffmpeg_markers_prove_no_audio_track() -> None:
    assert DemoAudioProcessor._ffmpeg_confirms_no_audio_track(
        "Stream map '0:a' matches no streams."
    )
    assert DemoAudioProcessor._ffmpeg_confirms_no_audio_track(
        "Output file #0 does not contain any stream"
    )
    assert not DemoAudioProcessor._ffmpeg_confirms_no_audio_track(
        "ffmpeg exited with code 1: input/output error"
    )
    assert not DemoAudioProcessor._ffmpeg_confirms_no_audio_track(
        "ffmpeg produced no audio; the video may not contain an audio track"
    )


def test_non_authoritative_asr_error_keeps_legacy_error_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "legacy.mp4"
    audio_path = tmp_path / "legacy.wav"
    video.write_bytes(b"legacy-video")
    audio_path.write_bytes(b"legacy-audio")
    jobs = JobStore(tmp_path / "legacy.sqlite3")
    jobs.create(job_id="legacy", platform="xhs", run_crawler=False)
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = "legacy"
    pipeline.authoritative_m3 = False
    pipeline.audio = ExplicitAudioTestDouble(
        audio_path,
        {"text": "", "segments": [], "error": "legacy error"},
    )
    pipeline.frames = SimpleNamespace(
        extract_timeline_frames=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("legacy frame error")
        )
    )

    result = pipeline._analyze_video_file(
        0, video, tmp_path / "legacy-output", "", "local"
    )

    assert any("legacy frame error" in item for item in result["errors"])
    assert result["transcript"]["error"] == "legacy error"
