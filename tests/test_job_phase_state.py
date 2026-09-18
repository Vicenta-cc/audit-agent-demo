import json

from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_state import available_job_actions, failure_metadata
from backend.audit_agent.job_store import JobStore
from backend.investigation_creation.adapters import _public_job_logs


def test_job_persists_independent_phase_statuses_configs_and_structured_logs(tmp_path):
    store = JobStore(tmp_path / "audit.sqlite3")
    store.create(
        job_id="phase-state",
        run_crawler=True,
        analyze_limit=5,
        auto_analyze=True,
        requested_config={"max_notes": 5, "max_concurrency": 3},
        effective_config={"max_notes": 5, "max_concurrency": 1},
    )
    store.update(
        "phase-state",
        status="crawl_paused",
        crawl_status="stopped",
        analysis_status="running",
    )
    store.log("phase-state", "收到控制指令：暂停采集", stage="control")

    job = store.get("phase-state")
    assert job["crawl_status"] == "stopped"
    assert job["analysis_status"] == "running"
    assert job["requested_config"]["max_concurrency"] == 3
    assert job["effective_config"]["max_concurrency"] == 1
    assert job["logs"][-1]["stage"] == "control"
    assert job["logs"][-1]["level"] == "warning"
    assert job["logs"][-1]["reason"] == "任务已在安全检查点暂停"
    assert job["logs"][-1]["error_code"] == "operation_paused"
    assert job["logs"][-1]["retryable"] is True
    assert job["logs"][-1]["action"] == "resume_when_ready"


def test_log_level_uses_explicit_metadata_and_does_not_treat_zero_failures_as_error(tmp_path):
    store = JobStore(tmp_path / "log-levels.sqlite3")
    store.create(job_id="log-levels", run_crawler=True)

    store.log(
        "log-levels",
        "逐条评论审核完成，成功=10，失败=0，翻译失败=0",
    )
    store.log(
        "log-levels",
        "首次结果漏项，进行补偿重试",
        level="warning",
        reason="模型首次结果缺少部分评论",
        error_code="comment_audit_incomplete",
        retryable=True,
        action="retry_missing_comments",
    )
    store.log(
        "log-levels",
        "补偿重试后仍漏项，停止重试",
        level="error",
        reason="重试耗尽",
        error_code="comment_audit_retry_exhausted",
        retryable=False,
        action="mark_missing_comments_failed",
    )

    logs = store.get("log-levels")["logs"]
    assert [item["level"] for item in logs] == ["info", "warning", "error"]
    assert logs[1]["error_code"] == "comment_audit_incomplete"
    assert logs[1]["retryable"] is True
    assert logs[1]["action"] == "retry_missing_comments"
    assert logs[2]["reason"] == "重试耗尽"
    assert logs[2]["retryable"] is False


def test_warning_logs_fill_safe_structured_diagnostics_when_callers_omit_them(tmp_path):
    store = JobStore(tmp_path / "warning-metadata.sqlite3")
    store.create(job_id="warning-metadata", run_crawler=True)

    messages = (
        (
            "ASR 翻译返回不完整，自动按段拆批重试：segments=8，finish_reason=length",
            "asr_translation_incomplete",
            True,
            "retry_in_smaller_batches",
        ),
        (
            "笔记 note-1：视频下载失败：private provider detail",
            "video_download_failed",
            True,
            "continue_without_video",
        ),
        (
            "笔记 note-2：远程媒体不是可播放视频，已跳过 video.bin",
            "invalid_remote_video",
            False,
            "skip_invalid_video",
        ),
        (
            "继续分析：已有输出媒体目录不可用，回退当前任务目录：private path",
            "expected_fallback",
            False,
            "continue_with_fallback",
        ),
    )
    for message, _, _, _ in messages:
        store.log("warning-metadata", message)

    logs = store.get("warning-metadata")["logs"]
    assert len(logs) == len(messages)
    for log, (_, error_code, retryable, action) in zip(logs, messages):
        assert log["level"] == "warning"
        assert log["reason"]
        assert "private" not in log["reason"]
        assert log["error_code"] == error_code
        assert log["retryable"] is retryable
        assert log["action"] == action


def test_explicit_warning_metadata_overrides_inferred_defaults(tmp_path):
    store = JobStore(tmp_path / "explicit-warning-metadata.sqlite3")
    store.create(job_id="explicit-warning-metadata", run_crawler=True)
    store.log(
        "explicit-warning-metadata",
        "视频下载失败",
        level="warning",
        reason="明确原因",
        error_code="explicit_code",
        retryable=False,
        action="explicit_action",
    )

    log = store.get("explicit-warning-metadata")["logs"][-1]
    assert log["reason"] == "明确原因"
    assert log["error_code"] == "explicit_code"
    assert log["retryable"] is False
    assert log["action"] == "explicit_action"


def test_available_actions_keep_collection_and_analysis_controls_independent():
    stats = {
        "pending_analysis_count": 2,
        "failed_analysis_count": 0,
        "analyzing_count": 0,
    }
    collection_paused = {
        "id": "job",
        "status": "crawl_paused",
        "run_crawler": True,
        "crawl_status": "stopped",
        "analysis_status": "running",
        "control": {},
    }
    actions = available_job_actions(collection_paused, stats)
    assert actions["resume_crawl"] is True
    assert actions["pause_analysis"] is True
    assert actions["stop_analysis"] is True

    analysis_stopped = {
        **collection_paused,
        "status": "analysis_stopped",
        "crawl_status": "running",
        "analysis_status": "stopped",
    }
    actions = available_job_actions(analysis_stopped, stats)
    assert actions["pause_crawl"] is True
    assert actions["resume_analysis"] is True


def test_failed_collection_is_resumable_only_from_structured_recoverable_failure():
    base = {
        "id": "job",
        "status": "failed",
        "run_crawler": True,
        "crawl_status": "failed",
        "analysis_status": "failed",
        "control": {},
        "error": "an arbitrary failure message",
    }
    stats = {"pending_analysis_count": 0, "failed_analysis_count": 0}

    assert available_job_actions(base, stats)["resume_crawl"] is False

    for code in (
        "crawler_account_verification_required",
        "crawler_account_login_required",
        "crawler_rate_limited",
    ):
        job = {
            **base,
            "control": {"failure": failure_metadata(code, "synthetic failure")},
        }
        assert available_job_actions(job, stats)["resume_crawl"] is True

    provider_failure = {
        **base,
        "control": {
            "failure": failure_metadata(
                "audit_provider_unavailable", "provider unavailable"
            )
        },
    }
    assert available_job_actions(provider_failure, stats)["resume_crawl"] is False


def test_analysis_completion_does_not_overwrite_a_paused_crawl_phase(tmp_path):
    store = JobStore(tmp_path / "audit.sqlite3")
    store.create(job_id="independent-completion", run_crawler=True, analyze_limit=1)
    store.update(
        "independent-completion",
        status="crawl_paused",
        crawl_status="stopped",
        analysis_status="running",
    )
    store.update(
        "independent-completion",
        status="crawl_paused",
        analysis_status="completed",
    )

    job = store.get("independent-completion")
    assert job["crawl_status"] == "stopped"
    assert job["analysis_status"] == "completed"
    assert job["status"] == "crawl_paused"


def test_task_content_analysis_claim_is_atomic_and_limit_aware(tmp_path):
    ingestion = IngestionStore(tmp_path / "claim.sqlite3")
    batch = tmp_path / "claim-batch.json"
    batch.write_text(
        json.dumps(
            {
                "task_id": "claim-task",
                "platform": "dy",
                "keyword": "测试",
                "items": [
                    {"aweme_id": "claim-1", "desc": "one"},
                    {"aweme_id": "claim-2", "desc": "two"},
                ],
                "comments": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    refs = ingestion.ingest_batch(batch, tmp_path / "raw")

    assert ingestion.claim_content_for_analysis(
        "claim-task", "dy", refs[0]["content_key"], analyze_limit=1
    ) is True
    assert ingestion.claim_content_for_analysis(
        "claim-task", "dy", refs[0]["content_key"], analyze_limit=1
    ) is False
    assert ingestion.claim_content_for_analysis(
        "claim-task", "dy", refs[1]["content_key"], analyze_limit=1
    ) is False


def test_restart_requeues_orphaned_analysis_without_changing_task_identity(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    jobs = JobStore(db_path)
    ingestion = IngestionStore(db_path)
    jobs.create(
        job_id="same-task",
        run_crawler=True,
        analyze_limit=1,
        auto_analyze=True,
    )
    jobs.update(
        "same-task",
        status="running",
        crawl_status="running",
        analysis_status="running",
    )
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "task_id": "same-task",
                "platform": "dy",
                "keyword": "测试",
                "items": [{"aweme_id": "aweme-1", "desc": "content"}],
                "comments": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    refs = ingestion.ingest_batch(batch, tmp_path / "raw")
    ingestion.mark_content_status(
        "dy",
        refs[0]["content_key"],
        "analyzing",
        task_id="same-task",
    )

    assert jobs.recover_interrupted_jobs() == 1
    assert ingestion.reset_all_analyzing() == 1
    recovered = jobs.get("same-task")
    assert recovered["id"] == "same-task"
    assert recovered["crawl_status"] == "interrupted"
    assert recovered["analysis_status"] == "pending"
    assert ingestion.stats_for_task("same-task")["queued_analysis_count"] == 1


def test_public_runtime_logs_redact_accounts_commands_and_local_paths():
    logs = _public_job_logs(
        [
            {"stage": "account", "level": "info", "message": "执行账号：Alice"},
            {"stage": "crawl", "level": "info", "message": "MediaCrawler command: --secret /Users/demo/data"},
            {
                "stage": "media",
                "level": "warning",
                "message": "处理 /private/tmp/video.mp4 完成",
                "reason": "源文件 /Users/demo/video.mp4 不可用",
                "error_code": "media_unavailable",
                "retryable": False,
                "action": "skip_media",
            },
        ]
    )
    assert logs[0]["message"] == "已完成采集账号可用性校验"
    assert logs[1]["message"] == "采集执行器命令已完成"
    assert "/private/tmp" not in logs[2]["message"]
    assert "[本地路径]" in logs[2]["message"]
    assert "/Users/demo" not in logs[2]["reason"]
    assert logs[2]["error_code"] == "media_unavailable"
    assert logs[2]["retryable"] is False
    assert logs[2]["action"] == "skip_media"


def test_monitor_job_projection_redacts_paths_without_mutating_stored_logs(tmp_path, monkeypatch):
    from backend import main

    jobs = JobStore(tmp_path / "audit.sqlite3")
    ingestion = IngestionStore(tmp_path / "audit.sqlite3")
    jobs.create(job_id="monitor-log", run_crawler=True)
    jobs.log("monitor-log", "视频：处理 /Users/demo/private/video.mp4")
    monkeypatch.setattr(main, "ingestion_store", ingestion)

    projected = main.enrich_job(jobs.get("monitor-log"))

    assert "[本地路径]" in projected["logs"][-1]["message"]
    assert "/Users/demo" in jobs.get("monitor-log")["logs"][-1]["message"]
