"""Reconcile durable admissions only after obtaining the execution fence."""

import json
from uuid import uuid4


def reconcile(worker):
    admission = worker.store.admission
    with admission.connect() as db:
        rows = db.execute(
            "SELECT * FROM task_admissions WHERE state='RESERVED' ORDER BY created_at,id"
        ).fetchall()
    for value in rows:
        row = dict(value)
        try:
            reconcile_one(worker, row)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Admission reconciliation retained %s", row["id"]
            )


def reconcile_one(worker, row):
    admission = worker.store.admission
    # RUNNING here means the preceding owner is no longer executing: caller
    # owns a lock also inherited by crawler children. Never infer this from TTL.
    job_id = row["job_id"]
    published = worker.report_adapter.find_published(job_id) if job_id else None
    if published:
        worker.report_adapter.verify_published(published, task_id=job_id)
        if row["kind"] == "investigation":
            finalize_published(worker, row, published)
        else:
            admission.settle(
                row["task_id"],
                report_id=published,
                expected_updated_at=row["updated_at"],
            )
        return
    if row["decision"] in ("PUBLISHING", "PUBLISHED"):
        # The old publisher is stopped; no report committed after its intent.
        release_stopped(
            worker, row, "report_generation_failed", failed_publication=True
        )
        return
    expired = False
    try:
        with admission.connect() as db:
            admission.validate_user(db, row["owner_id"])
    except Exception as exc:
        from .store import AdmissionError

        if not isinstance(exc, AdmissionError):
            raise
        expired = True
    if row["decision"] == "CANCELLED" or expired:
        reason = (
            "account_expired"
            if expired
            else (
                "report_generation_failed"
                if row["reason"] == "report_generation_failed"
                else "cancelled"
            )
        )
        release_stopped(worker, row, reason)
        return
    if row["kind"] == "investigation":
        run = worker.store.get_run_for_worker(row["task_id"])
        pending_resume = row["queue_state"] == "QUEUED" and json.loads(
            row["payload_json"]
        ).get("resume_action")
        if pending_resume:
            return
        if run.status.value in ("FAILED", "AUDIT_COMPLETED"):
            admission.settle(
                row["task_id"],
                stopped=True,
                reason=run.error_code or "no_report",
                expected_updated_at=row["updated_at"],
            )
        elif run.status.value == "INTERRUPTED" and not (
            row["queue_state"] == "QUEUED"
            and json.loads(row["payload_json"]).get("resume_action")
        ):
            if run.error_code == "report_generation_result_unknown":
                release_stopped(worker, row, "report_generation_failed")
            else:
                hold_interrupted(worker, row, reason=run.error_code)
    elif row["queue_state"] == "HELD" and row["reason"] in (
        "failed_pending_stop",
        "no_reportable_results",
    ):
        admission.settle(
            row["task_id"],
            stopped=True,
            reason=row["reason"],
            expected_updated_at=row["updated_at"],
        )
    elif row["queue_state"] == "RUNNING":
        # No blind replay of a crawler whose execution result is uncertain.
        job = worker.execution_adapter.get_job_state(job_id)
        if job and job.get("status") == "failed":
            release_stopped(worker, row, "failed")
        else:
            hold_interrupted(worker, row)


def current_row(db, row):
    return (
        db.execute(
            "SELECT 1 FROM task_admissions WHERE id=? AND state='RESERVED' AND updated_at=?",
            (row["id"], row["updated_at"]),
        ).fetchone()
        is not None
    )


def release_stopped(worker, row, reason, *, failed_publication=False):
    """Commit Run and ledger together; an API resume cannot interleave them."""
    admission = worker.store.admission
    with admission.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not current_row(db, row):
            return
        if row["decision"] in ("PUBLISHING", "PUBLISHED") and not failed_publication:
            return
        if row["job_id"]:
            # Projection is written before releasing the slot; a crash here
            # leaves the reservation intact for the next reconciliation.
            worker.execution_adapter.job_store.update(
                row["job_id"],
                status="stopped",
                crawl_status="stopped",
                analysis_status="stopped",
            )
        if row["kind"] == "investigation":
            db.execute(
                "UPDATE investigation_runs SET status='FAILED',error_code=?,error_message=?,claim_token='',heartbeat_at='' WHERE id=? AND status!='PUBLISHED'",
                (reason, "任务已终止。", row["task_id"]),
            )
        db.execute(
            "UPDATE task_admissions SET state='RELEASED',decision='CANCELLED',queue_state='DONE',execution_token='',reason=?,updated_at=? WHERE id=?",
            (reason, admission.now(), row["id"]),
        )


def finalize_published(worker, row, published):
    """Finalize an existing report even if its user subsequently expired.

    This does not run collection or generation. The execution fence proves the
    previous worker stopped, and the admission transaction fences API resumes.
    """
    admission = worker.store.admission
    run = worker.store.get_run_for_worker(row["task_id"])
    if run.status.value != "PUBLISHED":
        token = uuid4().hex
        with admission.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not current_row(db, row) or row["decision"] == "CANCELLED":
                return
            binding = worker.store.get_report_binding(run.id)
            if not binding or binding.report_version_id != published:
                raise ValueError("Published report does not match the Run binding")
            db.execute(
                "UPDATE task_admissions SET decision='PUBLISHED',report_version_id=?,execution_token=?,queue_state='RUNNING' WHERE id=?",
                (published, token, row["id"]),
            )
            db.execute(
                "UPDATE investigation_runs SET status='REPORT_GENERATING',claim_token=?,heartbeat_at=? WHERE id=?",
                (token, admission.now(), run.id),
            )
        worker._complete_published_report(
            worker.store.get_run_for_worker(run.id), published
        )
    admission.settle(row["task_id"], report_id=published)


def hold_interrupted(worker, row, *, reason="interrupted_execution"):
    admission = worker.store.admission
    with admission.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not current_row(db, row):
            return
        if row["job_id"]:
            ingestion = getattr(worker.execution_adapter, "ingestion_store", None)
            if ingestion is not None:
                ingestion.reset_analyzing_for_task(row["job_id"])
            job_store = worker.execution_adapter.job_store
            job = job_store.get(row["job_id"])
            if job and job.get("status") in (
                "queued",
                "running",
                "analysis_running",
                "crawl_pausing",
                "analysis_pausing",
                "analysis_stopping",
            ):
                job_store.update(
                    row["job_id"],
                    status="interrupted",
                    crawl_status="interrupted",
                    analysis_status="stopped",
                )
        db.execute(
            "UPDATE task_admissions SET queue_state='HELD',execution_token='',reason=?,updated_at=? WHERE id=?",
            (reason, admission.now(), row["id"]),
        )
