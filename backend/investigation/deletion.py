"""Explicit workspace erasure, serialized with turns and worker claims.

Business rows are removed in one attached SQLite transaction. Immutable-report
delete triggers are restored before commit; ordinary report writes stay fenced.
Archive imports retain only an ID tombstone so restart cannot resurrect a report.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.hermes_runtime.adapter import session_runtime_home
from backend.hermes_runtime.report_snapshot import delete_published_report_snapshots


def _ids(value, key):
    if isinstance(value, dict):
        found = {value[key]} if isinstance(value.get(key), str) and value[key] else set()
        for child in value.values():
            found.update(_ids(child, key))
        return found
    if isinstance(value, list):
        return set().union(*(_ids(child, key) for child in value))
    return set()


class WorkspaceDeletionService:
    def __init__(self, *, conversations, creation_store, reports, historical, report_service):
        self.conversations = conversations
        self.creation_store = creation_store
        self.reports = reports
        self.historical = historical
        self.report_service = report_service

    def delete(self, workspace_id: str, *, principal_id: str, historical: bool = False):
        # Registration uses the same lock as deletion, including its archive import.
        with self.historical._registration_lock, closing(sqlite3.connect(
            self.conversations.store.db_path, timeout=30
        )) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            for alias, path in (("creation", self.creation_store.db_path),
                                ("reports_db", self.reports.db_path),
                                ("history", self.historical.workspace_store.db_path)):
                db.execute(f"ATTACH DATABASE ? AS {alias}", (str(path),))
            checkpoints = self.reports.db_path.parent / "report_checkpoints.sqlite3"
            if checkpoints.exists():
                db.execute("ATTACH DATABASE ? AS checkpoints_db", (str(checkpoints),))
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("PRAGMA defer_foreign_keys=ON")
                deleted = db.execute(
                    "SELECT principal_id FROM history.deleted_workspaces WHERE id=?",
                    (workspace_id,),
                ).fetchone()
                if deleted:
                    if deleted[0] != principal_id:
                        raise HTTPException(404, "会话不存在。")
                    return
                session_ids, draft_ids, run_ids, version_ids = set(), set(), set(), set()
                if historical:
                    workspace = db.execute(
                        "SELECT * FROM history.historical_report_workspaces WHERE id=? AND principal_id=?",
                        (workspace_id, principal_id),
                    ).fetchone()
                    if workspace is None:
                        raise HTTPException(404, "会话不存在。")
                    version_ids.add(workspace["report_version_id"])
                else:
                    workspace = db.execute(
                        "SELECT * FROM investigation_sessions WHERE id=? AND scope_type='creation' AND owner_principal=?",
                        (workspace_id, principal_id),
                    ).fetchone()
                    if workspace is None:
                        raise HTTPException(404, "会话不存在。")
                    session_ids.add(workspace_id)
                    # Include every durable draft/run artifact, including superseded drafts.
                    payloads = db.execute(
                        "SELECT public_artifact_json FROM investigation_turns WHERE session_id=? "
                        "UNION ALL SELECT response_json FROM creation.investigation_creation_tool_receipts "
                        "WHERE session_id=? AND status='SUCCEEDED' AND is_mutation=1",
                        (workspace_id, workspace_id),
                    )
                    for row in payloads:
                        value = json.loads(row[0] or "{}")
                        draft_ids.update(_ids(value, "draft_id"))
                        run_ids.update(_ids(value, "run_id"))
                    for row in db.execute("SELECT * FROM creation.investigation_runs"):
                        if row["id"] in run_ids or row["draft_id"] in draft_ids:
                            if row["owner_principal"] != principal_id:
                                raise HTTPException(409, "会话关联了其他用户的任务，无法删除。")
                            if row["status"] in {"QUEUED", "RUNNING", "AUDIT_COMPLETED", "REPORT_GENERATING"}:
                                raise HTTPException(409, "任务正在执行，请等待任务结束后再删除会话。")
                            active_admission = db.execute("SELECT 1 FROM creation.task_admissions WHERE task_id=? AND state='RESERVED'",(row["id"],)).fetchone()
                            if active_admission:
                                raise HTTPException(409,"请先取消任务，等待执行停止后再删除会话。")
                            run_ids.add(row["id"])
                            draft_ids.add(row["draft_id"])
                            if row["report_version_id"]:
                                version_ids.add(row["report_version_id"])
                            if row["report_session_id"]:
                                session_ids.add(row["report_session_id"])
                    # A draft reused by another workspace must not be erased under it.
                    for row in db.execute(
                        "SELECT session_id, public_artifact_json FROM investigation_turns WHERE session_id<>?",
                        (workspace_id,),
                    ):
                        value = json.loads(row["public_artifact_json"] or "{}")
                        if _ids(value, "draft_id") & draft_ids or _ids(value, "run_id") & run_ids:
                            raise HTTPException(409, "任务被其他会话引用，暂不能删除。")
                for row in db.execute("SELECT * FROM creation.investigation_report_generation_bindings"):
                    if row["run_id"] in run_ids and row["report_version_id"]:
                        version_ids.add(row["report_version_id"])
                # All versions of a workspace's report belong to the deletion.
                report_ids = {row["report_id"] for row in db.execute("SELECT * FROM reports_db.report_versions")
                              if row["id"] in version_ids}
                version_ids.update(row["id"] for row in db.execute("SELECT * FROM reports_db.report_versions")
                                   if row["report_id"] in report_ids)
                session_ids.update(row["id"] for row in db.execute("SELECT * FROM investigation_sessions")
                                   if row["report_version_id"] in version_ids)
                # Report caches contain cross-report snapshots. Wait for their readers too.
                for row in db.execute(
                    "SELECT t.session_id,s.scope_type FROM investigation_turns t "
                    "JOIN investigation_sessions s ON s.id=t.session_id WHERE t.status='running'"
                ):
                    if row["session_id"] in session_ids or (version_ids and row["scope_type"] == "report"):
                        raise HTTPException(409, "报告问答或会话正在执行，请等待结束后再删除。")
                turn_ids = self._values(db, "main", "investigation_turns", "id", "session_id", session_ids)
                query_ids = self._values(db, "main", "investigation_query_result_artifacts", "artifact_id", "session_id", session_ids)
                receipt_ids = self._values(db, "creation", "investigation_creation_tool_receipts", "receipt_id", "session_id", session_ids)
                edit_ids = self._values(db, "creation", "resource_edit_origins", "edit_id", "session_id", session_ids)
                generation_ids = self._values(db, "reports_db", "report_generation_runs", "id", "report_id", report_ids)
                thread_ids = self._values(db, "reports_db", "report_generation_runs", "checkpoint_thread_id", "report_id", report_ids)
                claim_ids = self._values(db, "reports_db", "report_claims", "id", "report_version_id", version_ids)
                category_ids = self._values(db, "reports_db", "report_categories", "id", "report_version_id", version_ids)
                # Drop only DELETE guards inside this exclusive write transaction.
                guards = list(db.execute("SELECT name,sql FROM reports_db.sqlite_master WHERE type='trigger' AND upper(sql) LIKE '%BEFORE DELETE%'").fetchall())
                for guard in guards:
                    db.execute(f'DROP TRIGGER reports_db."{guard["name"]}"')
                self._erase_columns(db, "main", "investigation_", {
                    "session_id": session_ids, "turn_id": turn_ids,
                    "query_result_artifact_id": query_ids,
                })
                self._delete(db, "main", "investigation_sessions", "id", session_ids)
                self._erase_columns(db, "creation", "", {"session_id": session_ids,
                    "draft_id": draft_ids, "run_id": run_ids, "receipt_id": receipt_ids, "edit_id": edit_ids})
                self._delete(db, "creation", "investigation_runs", "id", run_ids)
                self._delete(db, "creation", "investigation_drafts", "id", draft_ids)
                self._erase_columns(db, "reports_db", "report", {"report_version_id": version_ids,
                    "report_id": report_ids, "run_id": generation_ids, "claim_id": claim_ids, "category_id": category_ids})
                self._delete(db, "reports_db", "report_versions", "id", version_ids)
                self._delete(db, "reports_db", "reports", "id", report_ids)
                self._delete(db, "reports_db", "historical_report_imports", "report_version_id", version_ids)
                self._delete(db, "reports_db", "resource_save_receipts", "session_id", session_ids)
                if checkpoints.exists():
                    self._erase_columns(db, "checkpoints_db", "", {"thread_id": thread_ids})
                if historical:
                    self._delete(db, "history", "historical_report_runs", "workspace_id", {workspace_id})
                    self._delete(db, "history", "historical_report_workspaces", "id", {workspace_id})
                for guard in guards:
                    sql = guard["sql"]
                    # SQLite stores unqualified trigger names in sqlite_master.
                    sql = sql.replace("CREATE TRIGGER ", "CREATE TRIGGER reports_db.", 1)
                    db.execute(sql)
                self._clear_runtime(session_ids, bool(version_ids))
                db.executemany("INSERT OR IGNORE INTO deleted_report_versions VALUES (?)", ((version_id,) for version_id in version_ids))
                db.execute("INSERT INTO history.deleted_workspaces(id,principal_id) VALUES (?,?)", (workspace_id, principal_id))

    @staticmethod
    def _values(db, schema, table, column, selector, values):
        if not values or not db.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return set()
        return {row[0] for row in db.execute(
            f'SELECT "{column}" FROM {schema}."{table}" WHERE "{selector}" IN ({",".join("?" for _ in values)})', tuple(values)) if row[0]}

    @staticmethod
    def _delete(db, schema, table, column, values):
        if values and db.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            db.execute(f'DELETE FROM {schema}."{table}" WHERE "{column}" IN ({",".join("?" for _ in values)})', tuple(values))

    def _erase_columns(self, db, schema, prefix, selectors):
        tables = [row[0] for row in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")]
        for table in tables:
            if not table.startswith(prefix):
                continue
            columns = {row[1] for row in db.execute(f'PRAGMA {schema}.table_info("{table}")')}
            for column, values in selectors.items():
                if column in columns:
                    self._delete(db, schema, table, column, values)

    def _clear_runtime(self, session_ids, reports_deleted):
        for service in (self.conversations, self.report_service):
            with service._agent_lock:
                for session_id in session_ids:
                    agent = service._agents.pop(session_id, None)
                    if agent and callable(getattr(agent, "close", None)):
                        agent.close()
                    if service is self.report_service:
                        if service.bind_runtime:
                            service.runtime_binding.release_published_report_session(session_id)
                        service._bound_sessions.discard(session_id)
            root = Path(service.hermes_state_dir)
            for session_id in session_ids:
                # Session IDs come exclusively from persisted business records.
                if Path(session_id).name != session_id:
                    raise RuntimeError("invalid persisted session identifier")
                directory = session_runtime_home(root, session_id)
                if directory.exists():
                    shutil.rmtree(directory)
                for suffix in (".sqlite3", ".sqlite3-wal", ".sqlite3-shm"):
                    (root / f"{session_id}{suffix}").unlink(missing_ok=True)
            if reports_deleted and service is self.report_service:
                delete_published_report_snapshots(
                    root / "snapshot-cleanup-ledger.sqlite3",
                    session_ids=frozenset(session_ids),
                )


def create_workspace_deletion_router(service, principal_provider):
    router = APIRouter(tags=["workspace-deletion"])

    @router.delete("/api/investigation-workspaces/{workspace_id}", status_code=204)
    def delete_workspace(workspace_id: str, principal=Depends(principal_provider)):
        service.delete(workspace_id, principal_id=principal.id)
        return Response(status_code=204)

    @router.delete("/api/historical-report-workspaces/{workspace_id}", status_code=204)
    def delete_historical_workspace(workspace_id: str, principal=Depends(principal_provider)):
        service.delete(workspace_id, principal_id=principal.id, historical=True)
        return Response(status_code=204)

    return router
