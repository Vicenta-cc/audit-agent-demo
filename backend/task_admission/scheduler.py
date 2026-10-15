"""Dispatch a single admitted task under its own execution/resource leases."""

from __future__ import annotations

import json
from contextlib import contextmanager

from .execution import execution_lock
from .resources import account_lease, capacity_lease, assigned_account


def waiting(admission, row, reason, account_id=""):
    with admission.connect() as db:
        db.execute(
            "UPDATE task_admissions SET waiting_reason=?,resource_account_id=? WHERE id=? AND state='RESERVED'",
            (reason, account_id, row["id"]),
        )


@contextmanager
def select_account(worker, row):
    payload = json.loads(row["payload_json"])
    action = payload.get("resume_action")
    config = payload.get("request", payload)
    account_store = getattr(worker.execution_adapter, "crawler_account_store", None)
    recovery_without_crawl = False
    if row["kind"] == "investigation" and not action:
        run = worker.store.get_run_for_worker(row["task_id"])
        recovery_without_crawl = run.status.value == "REPORT_GENERATING"
        if run.status.value == "RUNNING":
            job_id = run.job_id or worker.execution_adapter.job_id_for_run(run.id)
            job = worker.execution_adapter.get_job_state(job_id)
            # Recovery classifies existing results; it never blindly reruns a
            # Pipeline that has already left the queued state.
            recovery_without_crawl = bool(job and job.get("status") != "queued")
    if (
        account_store is None
        or recovery_without_crawl
        or action in ("resume_report", "resume_analysis", "backfill_analysis")
        or not config.get("run_crawler", True)
    ):
        yield True
        return
    from backend.audit_agent.config import settings
    platform = config.get("platform", "xhs")
    preferred = config.get("crawler_account_id") or ""
    from .store import AdmissionError
    from backend.audit_agent.account_rotation import AccountRotationManager
    all_permitted=set()
    for candidate in account_store.list(platform=platform):
        try:
            with worker.store.admission.connect() as db:
                worker.store.admission.validate_user(db,row['owner_id'],candidate['id'])
            all_permitted.add(candidate['id'])
        except AdmissionError:
            continue
    manager=AccountRotationManager(account_store)
    manager.refresh_authorized(platform,row['owner_id'],all_permitted)
    manager.recover_authorized(platform,row['owner_id'],all_permitted)
    manager.prepare_exhausted_pool(platform,row['owner_id'],all_permitted)
    accounts = account_store.available_accounts(platform)
    permitted_accounts = []

    for account in accounts:
        try:
            with worker.store.admission.connect() as db:
                access_scope = worker.store.admission.validate_user(
                    db, row["owner_id"], account["id"]
                )
        except AdmissionError:
            continue
        permitted_accounts.append((access_scope, account))
    permitted_accounts.sort(
        key=lambda item: (
            item[0] != "public",
            *(account_store.fair_key(item[1]) if platform=='dy' and settings.crawler_safety_enabled else ()),
            item[1]["id"] != preferred,
        )
    )

    for _, account in permitted_accounts:
        with account_lease(platform, account["id"]) as acquired:
            if not acquired:
                continue
            # Recheck after lease acquisition; login/maintenance may have changed it.
            if account["id"] not in {
                a["id"] for a in account_store.available_accounts(platform)
            }:
                continue
            from backend.audit_agent.crawler_route import (
                resolve_account_route, authorize_route_owner, CollectionRouteError,
            )
            try:
                from backend.audit_agent.config import settings
                requires_route = settings.crawler_proxy_enabled or account.get("proxy_required") or account.get("proxy_id")
                if platform == "dy" and requires_route:
                    snapshot = resolve_account_route(account["id"], account_store=account_store)
                    if snapshot:
                        authorize_route_owner(account["id"], None if snapshot.route_mode in {"direct_degraded", "legacy_direct"} else snapshot.proxy_id, row["owner_id"], account_store=account_store)
            except CollectionRouteError as exc:
                waiting(worker.store.admission, row, exc.code, account["id"])
                continue
            account_store.record_fair_selection(account['id'], [item['id'] for _,item in permitted_accounts])
            waiting(worker.store.admission, row, "", account["id"])
            with assigned_account(account["id"]):
                yield True
            return
    waiting(
        worker.store.admission,
        row,
        "account_busy"
        if permitted_accounts
        else "no_available_authorized_account",
    )
    yield False


def dispatch(worker):
    from .legacy import dispatch_one
    from .recovery import reconcile, reconcile_one
    from backend.audit_agent.config import settings

    trial_id = str(getattr(settings, "crawler_proxy_trial_id", "") or "").strip()
    allowed = getattr(settings, "crawler_proxy_trial_job_ids", ())
    if isinstance(allowed, str):
        allowed = [value.strip() for value in allowed.split(",") if value.strip()]
    trial_jobs = frozenset(allowed)
    if trial_id and not trial_jobs:
        # An isolated trial starts idle; only a manifest-listed task may run.
        return None
    admission = worker.store.admission
    reconcile(worker)
    with admission.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM task_admissions WHERE state='RESERVED' AND decision!='CANCELLED' ORDER BY created_at,id"
            )
        ]
    for candidate in rows:
        if trial_id and candidate["task_id"] not in trial_jobs and candidate.get("job_id") not in trial_jobs:
            continue
        task_id = candidate["task_id"]
        result = None
        executed = False
        with execution_lock(worker.store.db_path, task_id) as acquired:
            if not acquired:
                continue
            row = admission.for_task(task_id)
            if not row or row["state"] != "RESERVED":
                continue
            payload = json.loads(row["payload_json"])
            resumable_run = False
            if row["kind"] == "investigation" and not payload.get("resume_action"):
                run = worker.store.get_run_for_worker(task_id)
                resumable_run = run.status.value in (
                    "QUEUED",
                    "RUNNING",
                    "REPORT_GENERATING",
                )
            if row["queue_state"] != "QUEUED" and not resumable_run:
                continue
            with capacity_lease("execution", worker.execution_capacity) as capacity:
                if not capacity:
                    waiting(admission, row, "system_capacity")
                    continue
                with select_account(worker, row) as account:
                    if not account:
                        continue
                    waiting(
                        admission,
                        row,
                        "",
                        admission.for_task(task_id)["resource_account_id"],
                    )
                    if resumable_run:
                        # Acquiring the task FD, not age of the heartbeat, proves
                        # the old execution (including its crawler) has stopped.
                        with worker.store._connect() as db:
                            db.execute(
                                "UPDATE investigation_runs SET heartbeat_at='' WHERE id=? AND status IN ('RUNNING','REPORT_GENERATING')",
                                (task_id,),
                            )
                    result = dispatch_one(worker, task_id) or worker._run_once(task_id)
                    executed = True
        if executed:
            # Must use a NEW file description. An orphan child may still own
            # the descriptor we just closed; never refund merely on return.
            with execution_lock(worker.store.db_path, task_id) as stopped:
                if stopped:
                    row = admission.for_task(task_id)
                    if row and row["state"] == "RESERVED":
                        reconcile_one(worker, row)
            return result
    return None
