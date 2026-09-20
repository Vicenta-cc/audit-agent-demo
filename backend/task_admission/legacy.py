"""Durable dispatch for the retained ordinary Job API and resume commands."""

from __future__ import annotations

import json
import logging
import sqlite3
from uuid import uuid4

from .execution import execution_context
from .store import AdmissionError, fingerprint


logger = logging.getLogger(__name__)


def materialize(row, job_store, revision_store):
    payload = json.loads(row["payload_json"])
    job = job_store.get(row["job_id"])
    if not job:
        try:
            job = job_store.create(
                job_id=row["job_id"], owner_user_id=row["owner_id"], **payload["job"]
            )
        except sqlite3.IntegrityError:
            job = job_store.get(row["job_id"])
            if job is None or job["owner_user_id"] != row["owner_id"]:
                raise
    if not job.get("current_audit_config_revision_id"):
        revision = revision_store.create_or_get(
            job_id=row["job_id"], created_by="admission-worker", **payload["revision"]
        )
        job_store.update(row["job_id"], current_audit_config_revision_id=revision["id"])
    if payload.get("relation_context") and payload.get("relation_parent_result"):
        job_store.upsert_comment_user_relation(
            analysis_job_id=row["job_id"],
            relation_context=payload["relation_context"],
            parent_audit_result=payload["relation_parent_result"],
            owner_user_id=row["owner_id"],
        )
    return job_store.get(row["job_id"])


def replay_resume(admission, job_id, *, owner, action, key, analyze_limit=0):
    digest = fingerprint(
        {"job_id": job_id, "action": action, "analyze_limit": analyze_limit}
    )
    with admission.connect() as db:
        command = db.execute(
            "SELECT * FROM task_admission_commands WHERE owner_id=? AND request_key=?",
            (owner, key),
        ).fetchone()
        if command is None:
            return None
        if command["fingerprint"] != digest:
            raise AdmissionError(
                "同一恢复请求不能改变内容。", code="IDEMPOTENCY_CONFLICT"
            )
        return dict(
            db.execute(
                "SELECT * FROM task_admissions WHERE id=?", (command["admission_id"],)
            ).fetchone()
        )


def enqueue_resume(admission, job, *, owner, action, key, analyze_limit=0):
    if job["owner_user_id"] != owner:
        raise AdmissionError("只能恢复自己的采集任务。")
    if analyze_limit and analyze_limit != int(job.get("analyze_limit") or 0):
        raise AdmissionError(
            "恢复任务不能扩大已冻结的分析范围。", code="FROZEN_TASK_CHANGED"
        )
    payload = {"action": action, "crawler_account_id": job.get("crawler_account_id")}
    command_hash = fingerprint(
        {"job_id": job["id"], "action": action, "analyze_limit": analyze_limit}
    )
    with admission.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        admission.validate_user(db, owner, payload["crawler_account_id"] or "")
        replay = db.execute(
            "SELECT * FROM task_admission_commands WHERE owner_id=? AND request_key=?",
            (owner, key),
        ).fetchone()
        if replay:
            if replay["fingerprint"] != command_hash:
                raise AdmissionError(
                    "同一恢复请求不能改变内容。", code="IDEMPOTENCY_CONFLICT"
                )
            return dict(
                db.execute(
                    "SELECT * FROM task_admissions WHERE id=?",
                    (replay["admission_id"],),
                ).fetchone()
            )

        def receipt(row):
            db.execute(
                "INSERT INTO task_admission_commands VALUES (?,?,?,?)",
                (owner, key, command_hash, row["id"]),
            )
            return dict(row)

        row = db.execute(
            "SELECT * FROM task_admissions WHERE job_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (job["id"],),
        ).fetchone()
        if row and row["state"] == "SUCCEEDED":
            raise AdmissionError(
                "任务已有成功报告，请创建新任务。", code="TASK_ALREADY_SUCCEEDED"
            )
        if row and row["state"] == "RESERVED":
            if row["decision"] != "OPEN":
                raise AdmissionError("任务正在取消或发布，不能恢复。")
            if row["queue_state"] != "HELD":
                raise AdmissionError("任务仍在排队或执行中。", code="USER_TASK_LIMIT")
            stored = json.loads(row["payload_json"])
            stored["resume_action"] = action
            db.execute(
                "UPDATE task_admissions SET payload_json=?,queue_state='QUEUED',updated_at=? WHERE id=?",
                (json.dumps(stored), admission.now(), row["id"]),
            )
            return receipt(
                db.execute(
                    "SELECT * FROM task_admissions WHERE id=?", (row["id"],)
                ).fetchone()
            )
        if row:
            stored = json.loads(row["payload_json"])
            stored["resume_action"] = action
            task_id, kind = row["task_id"], row["kind"]
        else:
            # Historical Jobs retain their owner and frozen configuration.
            stored = dict(payload, resume_action=action)
            task_id, kind = "legacy:" + job["id"], "legacy"
        return receipt(
            admission.reserve(
                db,
                owner=owner,
                task_id=task_id,
                kind=kind,
                key="resume:" + key,
                payload=stored,
                job_id=job["id"],
                request_hash=command_hash,
            )
        )


def dispatch_one(worker, task_id=None):
    """Called only under the process/crawler execution fence."""
    admission = worker.store.admission
    with admission.connect() as db:
        rows = db.execute(
            "SELECT * FROM task_admissions WHERE state='RESERVED' AND queue_state='QUEUED' AND decision='OPEN' AND (? IS NULL OR task_id=?) ORDER BY created_at,id",
            (task_id, task_id),
        ).fetchall()
    for value in rows:
        row = dict(value)
        payload = json.loads(row["payload_json"])
        action = payload.get("resume_action")
        if row["kind"] != "legacy" and not action:
            continue
        token = uuid4().hex
        try:
            admission.start(row["task_id"], token)
        except AdmissionError as exc:
            admission.settle(row["task_id"], stopped=True, reason=exc.code)
            return row
        from backend.audit_agent.pipeline import AuditPipeline
        from backend.audit_agent.requests import CrawlRequest, crawl_request_from_job

        adapter = worker.execution_adapter
        try:
            if action:
                job = adapter.job_store.get(row["job_id"])
            else:
                job = materialize(row, adapter.job_store, adapter.revision_store)
        except Exception as exc:
            logger.exception("Job materialization retained admission %s", row["id"])
            admission.hold(row["task_id"], str(exc))
            return row
        if job is None:
            admission.settle(row["task_id"], stopped=True, reason="job_missing")
            return row
        try:
            with execution_context(admission, row["task_id"], token):
                if action == "resume_report":
                    if row["kind"] == "investigation":
                        with worker.store._connect() as db:
                            db.execute(
                                "UPDATE investigation_runs SET status='REPORT_GENERATING',claim_token=?,heartbeat_at=?,error_code='',error_message='' WHERE id=?",
                                (token, admission.now(), row["task_id"]),
                            )
                        run = worker.store.get_run_for_worker(row["task_id"])
                        binding = worker.store.get_report_binding(run.id)

                        def bind(generation_id, version_id, run=run, binding=binding):
                            worker.store.bind_report_generation_started(
                                run.id,
                                generation_key=binding.generation_key,
                                r31_run_id=generation_id,
                                report_version_id=version_id,
                            )

                        report_id = worker.report_adapter.resume(
                            job["id"], on_generation_started=bind
                        )
                        worker._complete_published_report(run, report_id)
                    else:
                        report_id = worker.report_adapter.resume(
                            job["id"], on_generation_started=lambda *args: None
                        )
                        admission.settle(row["task_id"], report_id=report_id)
                    return row
                pipeline = AuditPipeline(job_id=job["id"])
                if row["kind"] == "investigation":
                    run = worker.store.get_run_for_worker(row["task_id"])
                    pipeline._m3_snapshot_validator = lambda adapter=adapter, run=run: (
                        adapter.verify_run_job(run)
                    )
                if action:
                    # The caller holds the process fence: any previous analyzer
                    # has stopped, so only this task's orphan claims can reset.
                    pipeline.ingestion.reset_analyzing_for_task(job["id"])
                    adapter.job_store.update_control(
                        job["id"],
                        crawl_stop_requested=False,
                        analysis_paused=False,
                        analysis_stop_requested=False,
                        stop_all_requested=False,
                    )
                    if action == "resume_crawl":
                        adapter.job_store.update(
                            job["id"], status="queued", crawl_status="queued", error=""
                        )
                        pipeline.run(crawl_request_from_job(job))
                    else:
                        pipeline.resume_pending_analysis(
                            int(job.get("analyze_limit") or 0),
                            int(job.get("analysis_batch_size") or 5),
                        )
                else:
                    pipeline.run(CrawlRequest.model_validate(payload["request"]))
                if row["kind"] == "investigation":
                    with worker.store._connect() as db:
                        db.execute(
                            "UPDATE investigation_runs SET status='RUNNING',recovery_required=1,claim_token='',heartbeat_at='',error_code='',error_message='' WHERE id=?",
                            (row["task_id"],),
                        )
                    payload.pop("resume_action", None)
                    with admission.connect() as db:
                        db.execute(
                            "UPDATE task_admissions SET payload_json=?,queue_state='HELD' WHERE id=?",
                            (json.dumps(payload), row["id"]),
                        )
                else:
                    finish_legacy(worker, row, token)
        except Exception as exc:
            logger.exception("Execution retained admission %s", row["id"])
            if action == "resume_report":
                published = worker.report_adapter.find_published(row["job_id"])
                if published:
                    admission.hold(row["task_id"], "report_completion_retryable")
                else:
                    with admission.connect() as db:
                        db.execute(
                            "UPDATE task_admissions SET decision='CANCELLED',execution_token='' WHERE id=?",
                            (row["id"],),
                        )
                    admission.hold(row["task_id"], "report_generation_failed")
                    if row["kind"] == "investigation":
                        with worker.store._connect() as db:
                            db.execute(
                                "UPDATE investigation_runs SET status='FAILED',claim_token='',error_code='report_generation_failed',error_message=? WHERE id=?",
                                (str(exc), row["task_id"]),
                            )
            else:
                admission.hold(row["task_id"], str(exc))
        return row
    return None


def finish_legacy(worker, row, token):
    admission = worker.store.admission
    job = worker.execution_adapter.get_job_state(row["job_id"]) or {}
    latest = admission.for_task(row["task_id"])
    published = worker.report_adapter.find_published(row["job_id"])
    if published:
        admission.settle(row["task_id"], report_id=published)
        return
    if latest["decision"] == "CANCELLED" or job.get("status") == "failed":
        admission.hold(
            row["task_id"],
            "cancelled" if latest["decision"] == "CANCELLED" else "failed_pending_stop",
        )
        return
    if job.get("status") != "completed":
        admission.hold(row["task_id"], "paused_or_result_unknown")
        return
    stats = job.get("task_stats") or {}
    if int(stats.get("pending_analysis_count") or 0) or int(
        stats.get("analyzing_count") or 0
    ):
        admission.hold(row["task_id"], "analysis_pending")
        return
    if not int(stats.get("completed_analysis_count") or 0):
        admission.hold(row["task_id"], "no_reportable_results")
        return
    try:
        with execution_context(admission, row["task_id"], token):
            report_id = worker.report_adapter.generate(
                row["job_id"], on_generation_started=lambda *args: None
            )
        worker.report_adapter.verify_published(report_id, task_id=row["job_id"])
        admission.settle(row["task_id"], report_id=report_id)
    except Exception:
        logger.exception("Report generation will reconcile admission %s", row["id"])
        # Publication might have committed before an exception: always reconcile.
        published = worker.report_adapter.find_published(row["job_id"])
        if published:
            admission.settle(row["task_id"], report_id=published)
        else:
            with admission.connect() as db:
                db.execute(
                    "UPDATE task_admissions SET decision='CANCELLED',execution_token='' WHERE id=?",
                    (row["id"],),
                )
            admission.hold(row["task_id"], "report_generation_failed")
