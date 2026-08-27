from __future__ import annotations

from typing import Any

from .contracts import (
    ConfirmAndQueueCommand,
    CreateDraftCommand,
    InvestigationDraft,
    InvestigationRun,
    InvestigationRunProjection,
    ResolvedExecutionConfiguration,
    UpdateDraftCommand,
)
from .errors import ConfirmationRequiredError, DraftRevisionConflictError
from .ports import (
    ConfigurationResolver,
    EmptyRunProjector,
    RunProjector,
)
from .store import InvestigationCreationStore
from .principal import Principal


class InvestigationCreationService:
    """Application commands and queries for M3 creation and execution status."""

    def __init__(
        self,
        store: InvestigationCreationStore,
        *,
        configuration_resolver: ConfigurationResolver,
        run_projector: RunProjector | None = None,
    ) -> None:
        self.store = store
        self.configuration_resolver = configuration_resolver
        self.run_projector = run_projector or EmptyRunProjector()

    def create_draft(
        self, command: CreateDraftCommand, *, principal: Principal
    ) -> InvestigationDraft:
        command = CreateDraftCommand.model_validate(
            command.model_dump(mode="json", warnings=False)
        )
        return self.store.create_draft(
            principal=principal.id,
            title=command.title,
            objective=command.objective,
            configuration=command.configuration,
        )

    def update_draft(
        self, command: UpdateDraftCommand, *, principal: Principal
    ) -> InvestigationDraft:
        command = UpdateDraftCommand.model_validate(
            command.model_dump(mode="json", warnings=False)
        )
        return self.store.update_draft(
            command.draft_id,
            principal=principal.id,
            expected_revision=command.expected_revision,
            title=command.title,
            objective=command.objective,
            configuration=command.configuration,
        )

    def get_draft(
        self, draft_id: str, *, principal: Principal
    ) -> InvestigationDraft:
        return self.store.get_draft(
            self._required_identifier(draft_id, "draft_id"),
            principal=principal.id,
        )

    def confirm_and_queue(
        self, command: ConfirmAndQueueCommand, *, principal: Principal
    ) -> InvestigationRun:
        command = ConfirmAndQueueCommand.model_validate(
            command.model_dump(mode="json", warnings=False)
        )
        fingerprint = self.store.confirmation_fingerprint(
            principal=principal.id,
            draft_id=command.draft_id,
            expected_revision=command.expected_revision,
            confirmed=command.confirmed,
        )
        replay = self.store.replay_confirmation(
            command.draft_id,
            principal=principal.id,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        if not command.confirmed:
            raise ConfirmationRequiredError("explicit confirmation is required")
        draft = self.store.get_draft(command.draft_id, principal=principal.id)
        if draft.current_revision != command.expected_revision:
            raise DraftRevisionConflictError(
                f"expected revision {command.expected_revision}, "
                f"current revision is {draft.current_revision}"
            )
        # Re-validate the persisted Draft before resolution and freeze only a
        # validated execution contract.
        draft_configuration = type(draft.configuration).model_validate(
            draft.configuration.model_dump(mode="json")
        )
        resolved = ResolvedExecutionConfiguration.model_validate(
            self.configuration_resolver.resolve(draft_configuration)
        ).model_dump(mode="json")
        return self.store.confirm_and_queue(
            command.draft_id,
            principal=principal.id,
            expected_revision=command.expected_revision,
            confirmed=command.confirmed,
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            resolved_configuration=resolved,
        )

    def get_run(
        self, run_id: str, *, principal: Principal
    ) -> InvestigationRunProjection:
        run = self.store.get_run(
            self._required_identifier(run_id, "run_id"),
            principal=principal.id,
        )
        projected: dict[str, Any] = self.run_projector.project(run)
        return InvestigationRunProjection(
            run_id=run.id,
            draft_id=run.draft_id,
            draft_revision=run.draft_revision,
            status=run.status,
            job_id=run.job_id,
            crawl_status=str(projected.get("crawl_status") or "unknown"),
            analysis_status=str(projected.get("analysis_status") or "unknown"),
            task_stats=dict(projected.get("task_stats") or {}),
            report_status=str(projected.get("report_status") or "pending"),
            report_version_id=run.report_version_id,
            report_session_id=run.report_session_id,
            error_code=run.error_code,
            error_message=run.error_message,
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    @staticmethod
    def _required_identifier(value: str, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be blank")
        return normalized
