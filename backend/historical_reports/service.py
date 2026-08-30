from __future__ import annotations

from threading import RLock
from typing import Any

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
    ) -> None:
        self.specs = specs
        self.workspace_store = workspace_store
        self.report_store = report_store
        self.report_service = report_service
        self.executor = executor
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
        if principal_id in self._registered_principals:
            return
        with self._registration_lock:
            if principal_id in self._registered_principals:
                return
            for spec in self.specs:
                self.importer.import_report(spec)
                self.workspace_store.register(spec, principal_id=principal_id)
            self._registered_principals.add(principal_id)

    def list_workspaces(self, *, principal_id: str) -> tuple[dict[str, Any], ...]:
        self.ensure_registered(principal_id=principal_id)
        return tuple(
            self._workspace_state(item, principal_id=principal_id)
            for item in self.workspace_store.list(principal_id=principal_id)
        )

    def get_workspace(
        self, workspace_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        self.ensure_registered(principal_id=principal_id)
        try:
            workspace = self.workspace_store.get(
                workspace_id, principal_id=principal_id
            )
        except KeyError as exc:
            raise InvestigationSessionNotFoundError(
                "historical report workspace was not found"
            ) from exc
        return self._workspace_state(workspace, principal_id=principal_id)

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
                anchor_key=self._anchor(workspace_id, principal_id),
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
        session = self.report_service.store.find_session_by_anchor(
            self._anchor(workspace_id, principal_id)
        )
        if session is None:
            raise InvestigationTurnNotFoundError(turn_id)
        turn = self.report_service.store.get_turn(turn_id)
        if turn.session_id != session.id:
            raise InvestigationTurnNotFoundError(turn_id)
        return turn

    def resume_turn(
        self, workspace_id: str, turn_id: str, *, principal_id: str
    ) -> Any:
        self.require_turn(workspace_id, turn_id, principal_id=principal_id)
        return self.executor.resume_turn(turn_id)

    def internal_session_count(self, workspace_id: str, *, principal_id: str) -> int:
        session = self.report_service.store.find_session_by_anchor(
            self._anchor(workspace_id, principal_id)
        )
        return int(session is not None)

    def _workspace_state(
        self, workspace: dict[str, Any], *, principal_id: str
    ) -> dict[str, Any]:
        conversation: list[dict[str, Any]] = []
        latest_turn = None
        session = self.report_service.store.find_session_by_anchor(
            self._anchor(str(workspace["id"]), principal_id)
        )
        if session is not None:
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
            latest_turn = turns[-1] if turns else None
        return {
            **workspace,
            "conversation": conversation,
            "latest_turn": latest_turn,
        }

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
