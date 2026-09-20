from __future__ import annotations

from typing import Any

from backend.rulesets.compiler import compile_ruleset_content, content_hash
from backend.rulesets.contracts import RuleSetContent

from .contracts import (
    ConfirmationPreview,
    ConfirmAndQueueCommand,
    CreateDraftCommand,
    InvestigationDraftConfiguration,
    InvestigationDraftView,
    InvestigationOptions,
    InvestigationDraft,
    InvestigationRun,
    InvestigationRunProjection,
    LegacyInvestigationDraftConfigurationV3,
    DraftStatus,
    Platform,
    QueryInvestigationOptions,
    ResolvedExecutionConfiguration,
    UpdateDraftCommand,
    TemporaryRuleSetProposal,
    TemporaryRuleSetJudgement,
    confirmed_configuration_hash,
)
from .errors import (
    ConfigurationValidationError,
    ConfirmationRequiredError,
    DraftAlreadyConfirmedError,
    DraftRevisionConflictError,
)
from .ports import (
    ConfigurationResolver,
    EmptyRunProjector,
    ResourceService,
    RunProjector,
)
from .store import InvestigationCreationStore
from .principal import Principal
from .approval import UseRuleSetProposalInput, reject


class InvestigationCreationService:
    """Application commands and queries for M3 creation and execution status."""

    def __init__(
        self,
        store: InvestigationCreationStore,
        *,
        configuration_resolver: ConfigurationResolver,
        resource_service: ResourceService | None = None,
        run_projector: RunProjector | None = None,
        shared_lexicon_writes_require_admin: bool = False,
    ) -> None:
        self.store = store
        self.configuration_resolver = configuration_resolver
        self.resource_service = resource_service
        self.run_projector = run_projector or EmptyRunProjector()
        self.shared_lexicon_writes_require_admin = bool(
            shared_lexicon_writes_require_admin
        )
        self.conversation_store = None

    @property
    def resource_management(self):
        from backend.resource_management.service import ResourceManagementService
        if not hasattr(self, '_resource_management'):
            self._resource_management = ResourceManagementService(
                self,
                shared_lexicon_writes_require_admin=(
                    self.shared_lexicon_writes_require_admin
                ),
            )
        return self._resource_management

    def use_ruleset_proposal(self, command: UseRuleSetProposalInput, *, session_id: str, turn_id: str,
                            principal: Principal, tool_call_id: str = "", runtime_turn_id: str = "") -> InvestigationDraft:
        if self.conversation_store is None or not turn_id or not tool_call_id:
            reject("PROPOSAL_APPROVAL_REQUIRED", "An authoritative current conversation turn is required.")
        if self.resource_service is None:
            raise ConfigurationValidationError("investigation resource service is not configured")
        with self.resource_service.authoritative_draft_fence() as resource_connection:
            def normalize(configuration: InvestigationDraftConfiguration, *, previous) -> InvestigationDraftConfiguration:
                self._reject_legacy_creation_platform(configuration)
                sources = self._temporary_provenance(configuration)
                if sources and sources != self._temporary_provenance(previous):
                    self.resource_service.validate_temporary_provenance(
                        sources, resource_connection=resource_connection,
                    )
                return self.resource_service.resolve_authoritative_draft(
                    configuration, principal=principal, resource_connection=resource_connection,
                ).normalized_configuration

            return self.store.use_ruleset_proposal(
                command, session_id=session_id, turn_id=turn_id, tool_call_id=tool_call_id, runtime_turn_id=runtime_turn_id or turn_id, principal=principal.id,
                conversation_db=self.conversation_store.db_path, normalize=normalize,
            )

    def create_ruleset_proposal(
        self, content: RuleSetContent | dict, *, session_id: str
    ) -> TemporaryRuleSetProposal:
        session_id = self._required_identifier(session_id, "session_id")
        content = self._validated_proposal_content(content)
        digest = content_hash(content)
        compile_ruleset_content(content)
        return self.store.create_ruleset_proposal(
            session_id=session_id, content=content, content_hash=digest
        )

    def get_ruleset_proposal(
        self, proposal_id: str, *, session_id: str
    ) -> TemporaryRuleSetProposal:
        return self.store.get_ruleset_proposal(
            self._required_identifier(proposal_id, "proposal_id"),
            session_id=self._required_identifier(session_id, "session_id"),
        )

    def update_ruleset_proposal(
        self, proposal_id: str, *, session_id: str, expected_version: int,
        content: RuleSetContent | dict,
    ) -> TemporaryRuleSetProposal:
        current = self.get_ruleset_proposal(proposal_id, session_id=session_id)
        self.store.check_proposal_version(current, expected_version)
        content = self._validated_proposal_content(content)
        compile_ruleset_content(content)
        return self.store.update_ruleset_proposal(
            current.proposal_id, session_id=current.session_id, expected_version=expected_version,
            content=content, content_hash=content_hash(content),
        )

    @staticmethod
    def _validated_proposal_content(content: RuleSetContent | dict) -> RuleSetContent:
        if isinstance(content, RuleSetContent):
            content = content.model_dump(mode="json")
        return RuleSetContent.model_validate(content)

    def create_draft(
        self, command: CreateDraftCommand, *, principal: Principal
    ) -> InvestigationDraft:
        command = CreateDraftCommand.model_validate(
            command.model_dump(mode="json", warnings=False)
        )
        self._reject_legacy_creation_platform(command.configuration)
        configuration = command.configuration
        self.store.protect_temporary_judgement(configuration)
        if isinstance(configuration, InvestigationDraftConfiguration):
            if self.resource_service is None:
                raise ConfigurationValidationError(
                    "investigation resource service is not configured"
                )
            with self.resource_service.authoritative_draft_fence() as resource_connection:
                sources = self._temporary_provenance(configuration)
                if sources:
                    self.resource_service.validate_temporary_provenance(
                        sources, resource_connection=resource_connection
                    )
                resolution = self.resource_service.resolve_authoritative_draft(
                    configuration,
                    principal=principal,
                    resource_connection=resource_connection,
                )
                return self.store.create_draft(
                    principal=principal.id,
                    title=command.title,
                    objective=command.objective,
                    configuration=resolution.normalized_configuration,
                )
        return self.store.create_draft(
            principal=principal.id,
            title=command.title,
            objective=command.objective,
            configuration=configuration,
        )

    def update_draft(
        self, command: UpdateDraftCommand, *, principal: Principal
    ) -> InvestigationDraft:
        command = UpdateDraftCommand.model_validate(
            command.model_dump(mode="json", warnings=False)
        )
        draft = self.store.get_draft(command.draft_id, principal=principal.id)
        if draft.status is not DraftStatus.DRAFT:
            raise DraftAlreadyConfirmedError(command.draft_id)
        if draft.current_revision != command.expected_revision:
            raise DraftRevisionConflictError(
                f"expected revision {command.expected_revision}, "
                f"current revision is {draft.current_revision}"
            )
        effective_configuration = (
            command.configuration
            if command.configuration is not None
            else draft.configuration
        )
        self._reject_legacy_creation_platform(effective_configuration)
        self.store.protect_temporary_judgement(effective_configuration, draft.configuration)
        if isinstance(effective_configuration, InvestigationDraftConfiguration):
            if self.resource_service is None:
                raise ConfigurationValidationError(
                    "investigation resource service is not configured"
                )
            with self.resource_service.authoritative_draft_fence() as resource_connection:
                sources = self._temporary_provenance(effective_configuration)
                if sources and sources != self._temporary_provenance(draft.configuration):
                    self.resource_service.validate_temporary_provenance(
                        sources, resource_connection=resource_connection
                    )
                resolution = self.resource_service.resolve_authoritative_draft(
                    effective_configuration,
                    principal=principal,
                    resource_connection=resource_connection,
                )
                return self.store.update_draft(
                    command.draft_id,
                    principal=principal.id,
                    expected_revision=command.expected_revision,
                    title=command.title,
                    objective=command.objective,
                    configuration=resolution.normalized_configuration,
                )
        return self.store.update_draft(
            command.draft_id,
            principal=principal.id,
            expected_revision=command.expected_revision,
            title=command.title,
            objective=command.objective,
            configuration=effective_configuration,
        )

    @staticmethod
    def _temporary_provenance(configuration: object) -> list[str]:
        if (
            isinstance(configuration, InvestigationDraftConfiguration)
            and configuration.investigation.mode == "search"
            and configuration.investigation.recall_plan.strategy == "temporary_terms"
        ):
            return list(configuration.investigation.recall_plan.source_lexicon_ids)
        return []

    def get_draft(
        self, draft_id: str, *, principal: Principal
    ) -> InvestigationDraft:
        return self.store.get_draft(
            self._required_identifier(draft_id, "draft_id"),
            principal=principal.id,
        )

    def get_confirmation_preview(
        self, draft_id: str, *, principal: Principal
    ) -> ConfirmationPreview:
        draft = self.get_draft(draft_id, principal=principal)
        if not isinstance(draft.configuration, InvestigationDraftConfiguration):
            message = "confirmation preview is not available for a legacy Draft"
            if isinstance(
                draft.configuration, LegacyInvestigationDraftConfigurationV3
            ):
                message = (
                    "legacy Draft has no deterministic RuleSet judgement selection; "
                    "save a published RuleSetRevision before confirmation"
                )
            raise ConfigurationValidationError(
                message,
                code="CONFIGURATION_INVALID",
            )
        if self.resource_service is None:
            raise ConfigurationValidationError(
                "investigation resource service is not configured"
            )
        return self.resource_service.confirmation_preview(
            draft, principal=principal
        )

    def get_draft_view(
        self, draft_id: str, *, principal: Principal
    ) -> InvestigationDraftView:
        draft = self.get_draft(draft_id, principal=principal)
        return InvestigationDraftView(
            draft=draft,
            confirmation_preview=self.get_confirmation_preview(
                draft.id, principal=principal
            ),
        )

    def query_investigation_options(
        self,
        query: QueryInvestigationOptions,
        *,
        principal: Principal,
    ) -> InvestigationOptions:
        if self.resource_service is None:
            raise ConfigurationValidationError(
                "investigation resource service is not configured"
            )
        return self.resource_service.query_options(
            QueryInvestigationOptions.model_validate(
                query.model_dump(mode="json", warnings=False)
            ),
            principal=principal,
        )

    @staticmethod
    def _reject_legacy_creation_platform(configuration: Any) -> None:
        if getattr(configuration, "platform", None) is Platform.WEIBO:
            raise ConfigurationValidationError(
                "Weibo is retained only for historical Draft compatibility and cannot be selected for a new revision.",
                code="PLATFORM_MISMATCH",
                details={"mutation_applied": False},
            )

    def confirm_and_queue(
        self, command: ConfirmAndQueueCommand, *, principal: Principal
    ) -> InvestigationRun:
        command = ConfirmAndQueueCommand.model_validate(
            command.model_dump(mode="json", warnings=False)
        )
        replay = self.store.replay_confirmation_identity(
            command.draft_id,
            principal=principal.id,
            expected_revision=command.expected_revision,
            confirmed=command.confirmed,
            idempotency_key=command.idempotency_key,
        )
        if replay is not None:
            return self._prepare_run(replay)
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
        confirmation_resolution = None
        if isinstance(
            draft_configuration, LegacyInvestigationDraftConfigurationV3
        ):
            raise ConfigurationValidationError(
                "legacy Draft has no deterministic RuleSet judgement selection; "
                "save a published RuleSetRevision before confirmation",
                code="CONFIGURATION_INVALID",
            )
        if isinstance(draft_configuration, InvestigationDraftConfiguration):
            if self.resource_service is None:
                raise ConfigurationValidationError(
                    "investigation resource service is not configured"
                )
            with self.resource_service.confirmation_fence() as resource_connection:
                replay = self.store.replay_confirmation_identity(
                    command.draft_id,
                    principal=principal.id,
                    expected_revision=command.expected_revision,
                    confirmed=command.confirmed,
                    idempotency_key=command.idempotency_key,
                )
                if replay is not None:
                    run = replay
                else:
                    settings_revision = self.resource_service.task_settings.get(resource_connection)["revision"]
                    if settings_revision and command.expected_task_settings_revision != settings_revision:
                        from .errors import ResourceStaleError
                        raise ResourceStaleError(
                            "统一采集与分析设置已变化，请刷新配置预览后重新确认。",
                            code="TASK_SETTINGS_CHANGED",
                        )
                    resolution = self.resource_service.resolve_confirmation(
                        draft,
                        principal=principal,
                        resource_connection=resource_connection,
                    )
                    run = self._store_confirmation(
                        command,
                        principal=principal,
                        resolved_model=resolution.execution,
                        configuration_hash=resolution.config_hash,
                        confirmation_resolution=resolution.model_dump(mode="json"),
                    )
            return self._prepare_run(run)
        else:
            resolved_model = ResolvedExecutionConfiguration.model_validate(
                self.configuration_resolver.resolve(draft_configuration)
            ).model_copy(update={"max_notes": 1})
            configuration_hash = confirmed_configuration_hash(
                {"execution": resolved_model.model_dump(mode="json")}
            )
        return self._prepare_run(
            self._store_confirmation(
                command,
                principal=principal,
                resolved_model=resolved_model,
                configuration_hash=configuration_hash,
                confirmation_resolution=confirmation_resolution,
            )
        )

    def _prepare_run(self, run: InvestigationRun) -> InvestigationRun:
        prepare = getattr(self.run_projector, "prepare_run", None)
        if callable(prepare):
            prepare(run)
        return run

    def _store_confirmation(
        self,
        command: ConfirmAndQueueCommand,
        *,
        principal: Principal,
        resolved_model: ResolvedExecutionConfiguration,
        configuration_hash: str,
        confirmation_resolution: dict[str, Any] | None,
    ) -> InvestigationRun:
        resolved = resolved_model.model_dump(mode="json")
        if confirmation_resolution is not None:
            confirmation_resolution["execution"] = resolved
        fingerprint = self.store.confirmation_fingerprint(
            principal=principal.id,
            draft_id=command.draft_id,
            expected_revision=command.expected_revision,
            confirmed=command.confirmed,
            confirmed_configuration_hash=configuration_hash,
        )
        return self.store.confirm_and_queue(
            command.draft_id,
            principal=principal.id,
            expected_revision=command.expected_revision,
            confirmed=command.confirmed,
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            resolved_configuration=resolved,
            confirmation_resolution=confirmation_resolution,
        )

    def get_run(
        self, run_id: str, *, principal: Principal
    ) -> InvestigationRunProjection:
        run = self.store.get_run(
            self._required_identifier(run_id, "run_id"),
            principal=principal.id,
        )
        projected: dict[str, Any] = self.run_projector.project(run)
        from backend.task_admission.presentation import admission_actions
        admission = self.store.admission.for_task(run.id)
        projected["available_actions"] = admission_actions(
            projected.get("available_actions") or {}, admission
        )
        if admission and admission["state"] == "RELEASED" and admission["end_requested_at"]:
            projected.update(crawl_status="stopped", analysis_status="stopped", report_status="cancelled")
        return InvestigationRunProjection(
            run_id=run.id,
            draft_id=run.draft_id,
            draft_revision=run.draft_revision,
            status=projected.get("status") or run.status,
            job_id=str(projected.get("job_id") or run.job_id),
            crawl_status=str(projected.get("crawl_status") or "unknown"),
            analysis_status=str(projected.get("analysis_status") or "unknown"),
            task_stats=dict(projected.get("task_stats") or {}),
            audit_results=list(projected.get("audit_results") or []),
            logs=list(projected.get("logs") or []),
            available_actions=dict(projected.get("available_actions") or {}),
            report_status=str(projected.get("report_status") or "pending"),
            report_version_id=str(
                projected.get("report_version_id") or run.report_version_id
            ),
            report_session_id=run.report_session_id,
            error_code=str(projected.get("error_code") or run.error_code),
            error_message=str(projected.get("error_message") or run.error_message),
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    def find_run_for_draft(
        self, draft_id: str, *, principal: Principal
    ) -> InvestigationRunProjection | None:
        run = self.store.find_run_for_draft(
            self._required_identifier(draft_id, "draft_id"),
            principal=principal.id,
        )
        if run is None:
            return None
        return self.get_run(run.id, principal=principal)

    @staticmethod
    def _required_identifier(value: str, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be blank")
        return normalized
