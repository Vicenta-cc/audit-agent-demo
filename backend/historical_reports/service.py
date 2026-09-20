from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from backend.historical_reports.catalog import HistoricalReportSpec
from backend.historical_reports.importer import HistoricalReportImporter
from backend.historical_reports.store import HistoricalReportWorkspaceStore
from backend.investigation.errors import (
    InvestigationSessionNotFoundError,
    InvestigationTurnNotFoundError,
)


class HistoricalReportDemoService:
    def __init__(
        self,
        *,
        specs: tuple[HistoricalReportSpec, ...],
        workspace_store: HistoricalReportWorkspaceStore,
        report_store: Any,
        report_service: Any,
        executor: Any,
        auto_register: bool = True,
        access_authorizer: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.specs = specs
        self.workspace_store = workspace_store
        self.report_store = report_store
        # Only these registered report archives participate in presentation activity.
        self.report_store.account_report_sources = tuple(
            (str(s.source_database), s.report_version_id, s.source_database_sha256,
             s.report_content_hash, s.snapshot_hash) for s in specs
        )
        self.report_service = report_service
        self.executor = executor
        self.auto_register = bool(auto_register)
        self.access_authorizer = access_authorizer
        self.importer = HistoricalReportImporter(report_store.db_path)
        self._specs_by_workspace = {item.workspace_id: item for item in specs}
        self._report_version_ids = frozenset(
            item.report_version_id for item in specs
        )
        self._registration_lock = RLock()
        self._turn_lock = RLock()
        self._registered_principals: set[str] = set()

    def is_historical_report_version(self, report_version_id: str) -> bool:
        return report_version_id in self._report_version_ids

    def is_historical_task(self, task_id: str) -> bool:
        return any(item.task_id == task_id for item in self.specs)

    def require_report_access(
        self, report_version_id: str, *, principal_id: str
    ) -> None:
        spec = next(
            (
                item
                for item in self.specs
                if item.report_version_id == report_version_id
            ),
            None,
        )
        if spec is None:
            return
        self.get_workspace(spec.workspace_id, principal_id=principal_id)

    def require_task_access(self, task_id: str, *, principal_id: str) -> None:
        spec = next((item for item in self.specs if item.task_id == task_id), None)
        if spec is None:
            return
        self.get_workspace(spec.workspace_id, principal_id=principal_id)

    def ensure_registered(self, *, principal_id: str) -> None:
        if not self.auto_register:
            return
        if principal_id in self._registered_principals:
            return
        with self._registration_lock:
            if principal_id in self._registered_principals:
                return
            for spec in self.specs:
                if self.workspace_store.is_deleted(spec.workspace_id):
                    continue
                self.importer.import_report(spec)
                self.workspace_store.register(spec, principal_id=principal_id)
            self._registered_principals.add(principal_id)

    def list_workspaces(self, *, principal_id: str) -> tuple[dict[str, Any], ...]:
        self.ensure_registered(principal_id=principal_id)
        workspaces = (
            self.workspace_store.list(principal_id=principal_id)
            if self.auto_register
            else tuple(
                item
                for item in self.workspace_store.list_all()
                if self._can_access(principal_id, item)
            )
        )
        return tuple(
            self._workspace_state(item, principal_id=principal_id)
            for item in workspaces
        )

    def get_workspace(
        self, workspace_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        self.ensure_registered(principal_id=principal_id)
        try:
            workspace = (
                self.workspace_store.get(workspace_id, principal_id=principal_id)
                if self.auto_register
                else self.workspace_store.get_any(workspace_id)
            )
            if not self._can_access(principal_id, workspace):
                raise KeyError(workspace_id)
        except KeyError as exc:
            raise InvestigationSessionNotFoundError(
                "historical report workspace was not found"
            ) from exc
        return self._workspace_state(workspace, principal_id=principal_id)

    def _can_access(self, principal_id: str, workspace: dict[str, Any]) -> bool:
        if str(workspace.get("principal_id") or "") == principal_id:
            return True
        return bool(
            self.access_authorizer
            and self.access_authorizer(principal_id, workspace)
        )

    def analysis_records(self, workspace_id: str, *, principal_id: str) -> dict[str, Any]:
        """Read all frozen post results, never replay or invent execution events."""
        from backend.reporting.presentation_projection import build_post_detail

        workspace = self.get_workspace(workspace_id, principal_id=principal_id)
        version_id = str(workspace["report_version_id"])
        document = self.report_store.get_frontend_report(version_id)
        posts = document.get("posts") or []
        coverage = document.get("source_coverage") or {}
        failures = {str(item["post_id"]): item for item in self._specs_by_workspace[workspace_id].analysis_failures}
        from backend.reporting.public_references import public_post_and_finding_refs
        snapshot = self.report_store.load_immutable_snapshot(version_id)
        post_refs, _ = public_post_and_finding_refs(snapshot)
        sources = {post_refs[finding.post_ref]: {
            "task_id": (finding.payload.get("source_provenance") or {}).get("source_job_id") or snapshot.task_id,
            "output_id": str(finding.audit_result_id),
        } for finding in snapshot.findings}

        return {
            "report_version_id": version_id,
            "title": workspace["title"],
            "record_count": len(posts),
            "candidate_count": coverage.get("candidate_posts", len(posts)),
            "notice": "以下为已保存的审核结果，不表示恢复了历史实时执行顺序或时间线。",
            "excluded_posts": [{**item, "reason": failures.get(str(item["post_id"]), {}).get("reason", "未保存逐帖失败原因")}
                               for item in coverage.get("excluded_failed_posts", [])],
            "comment_coverage": coverage.get("comments", {}),
            "records": [{**build_post_detail(document, post_ref=post["post_ref"]), "audit_source": sources.get(post["post_ref"])} for post in posts],
        }

    def accept_turn(
        self,
        workspace_id: str,
        *,
        principal_id: str,
        client_message_id: str,
        content: str,
    ) -> Any:
        workspace = self.get_workspace(workspace_id, principal_id=principal_id)
        with self._turn_lock:
            session = self.report_service.create_session(
                str(workspace["report_version_id"]),
                anchor_key=self._current_anchor(workspace, principal_id),
                owner_principal=principal_id,
            )
            return self.executor.accept_turn(
                session.id,
                client_message_id=client_message_id,
                content=content,
            )

    def require_turn(
        self, workspace_id: str, turn_id: str, *, principal_id: str
    ) -> Any:
        self.get_workspace(workspace_id, principal_id=principal_id)
        sessions = self.report_service.store.list_report_version_sessions(
            self._anchor(workspace_id, principal_id)
        )
        turn = self.report_service.store.get_turn(turn_id)
        if turn.session_id not in {session.id for session in sessions}:
            raise InvestigationTurnNotFoundError(turn_id)
        return turn

    def resume_turn(
        self, workspace_id: str, turn_id: str, *, principal_id: str
    ) -> Any:
        self.require_turn(workspace_id, turn_id, principal_id=principal_id)
        return self.executor.resume_turn(turn_id)

    def internal_session_count(self, workspace_id: str, *, principal_id: str) -> int:
        return len(self.report_service.store.list_report_version_sessions(
            self._anchor(workspace_id, principal_id)
        ))

    def _workspace_state(
        self, workspace: dict[str, Any], *, principal_id: str
    ) -> dict[str, Any]:
        conversation: list[dict[str, Any]] = []
        latest_turn = None
        sessions = self.report_service.store.list_report_version_sessions(
            self._anchor(str(workspace["id"]), principal_id)
        )
        for session in sessions:
            messages = self.report_service.get_messages(
                session.id, include_tool_messages=False
            )
            turns = self.report_service.store.list_turns(session.id)
            turns_by_id = {item.id: item for item in turns}
            for message in messages:
                if message.role == "user":
                    conversation.append(self._message_projection(message))
                    continue
                turn = turns_by_id.get(message.turn_id)
                if (
                    message.role == "assistant"
                    and turn is not None
                    and turn.assistant_message_id == message.id
                ):
                    conversation.append(self._message_projection(message))
            if session.report_version_id == workspace["report_version_id"]:
                latest_turn = turns[-1] if turns else None
        return {
            **workspace,
            "conversation": conversation,
            "latest_turn": latest_turn,
        }

    def _current_anchor(self, workspace: dict[str, Any], principal_id: str) -> str:
        anchor = self._anchor(str(workspace["id"]), principal_id)
        original = self.report_service.store.find_session_by_anchor(anchor)
        if original is None or original.report_version_id == workspace["report_version_id"]:
            return anchor
        # Preserve old session scope/history; future questions bind to the new version.
        return anchor + ":version:" + str(workspace["report_version_id"])

    @staticmethod
    def _message_projection(message: Any) -> dict[str, Any]:
        return {
            "message_id": message.id,
            "turn_id": message.turn_id,
            "role": message.role,
            "content": message.content,
            "sequence": message.sequence,
            "created_at": message.created_at,
        }

    @staticmethod
    def _anchor(workspace_id: str, principal_id: str) -> str:
        return f"historical-report:{principal_id}:{workspace_id}"
