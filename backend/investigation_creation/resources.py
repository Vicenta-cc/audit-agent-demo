from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
import unicodedata

from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import (
    CrawlerAccountStore,
    account_is_cooling_down,
)
from backend.audit_agent.creator_url import CreatorUrlValidationError, validate_creator_url
from backend.audit_agent.crawler_adapter import SUPPORTED_PLATFORMS
from backend.audit_agent.lexicon_store import LexiconStore
from backend.rulesets.compiler import content_hash as ruleset_content_hash
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.service import RuleSetService

from .contracts import (
    AuthoritativeDraftResolution,
    CrawlerAccountConfirmedState,
    ConfirmationPreview,
    ConfirmationResolution,
    ConfirmedRecallPlanSnapshot,
    InvestigationBlocker,
    InvestigationDraft,
    InvestigationDraftConfiguration,
    InvestigationTaskParameters,
    InvestigationConfiguration,
    InvestigationOptions,
    Platform,
    PlatformOption,
    QueryInvestigationOptions,
    RecallLexiconSummary,
    RecallPlanPreview,
    ResolvedExecutionConfiguration,
    RuleSetCategoryDetail,
    RuleSetExemptionDetail,
    RuleSetRevisionDetail,
    RuleSetRevisionSummary,
    RuleSetRuleDetail,
    confirmed_configuration_hash,
)
from .errors import ConfigurationValidationError, ResourceStaleError
from .principal import Principal


_PLATFORM_NAMES = {
    "dy": "抖音",
    "xhs": "小红书",
    "ks": "快手",
    "wb": "微博",
}
_CREATION_PLATFORM_ORDER = ("dy", "xhs", "ks", "wb")
_ENABLED_CREATION_PLATFORMS = frozenset({"dy"})
_RULES_MANAGEMENT_PATH = "/rule-assistant/rulesets?return_to=/investigation"


def _effective_m3_posts_per_keyword() -> int:
    return min(5, max(1, int(settings.m3_posts_per_keyword)))


def _effective_m3_comments_per_post() -> int:
    return min(1000, max(0, int(settings.m3_comments_per_post)))


def effective_task_parameters(configuration) -> InvestigationTaskParameters:
    requested = configuration.task_parameters or InvestigationTaskParameters(
        max_notes=_effective_m3_posts_per_keyword(),
        max_comments=_effective_m3_comments_per_post(),
        max_items_per_minute=5, analyze_limit=settings.m3_analyze_limit,
        analysis_batch_size=5,
    )
    from backend.audit_agent.task_settings import effective_parameters
    return effective_parameters(requested)


class InvestigationResourceService:
    """Read and freeze real M3 configuration resources through stable stores."""

    def __init__(
        self,
        *,
        lexicon_store: LexiconStore,
        ruleset_service: RuleSetService,
        configuration_resolver: Any,
        crawler_account_store: CrawlerAccountStore | None = None,
    ) -> None:
        self.lexicon_store = lexicon_store
        self.ruleset_service = ruleset_service
        self.configuration_resolver = configuration_resolver
        self.crawler_account_store = (
            crawler_account_store
            or configuration_resolver.crawler_account_store
        )
        resource_paths = {
            Path(path).expanduser().resolve()
            for path in (
                self.lexicon_store.db_path,
                self.ruleset_service.store.db_path,
                self.crawler_account_store.db_path,
            )
        }
        if len(resource_paths) != 1:
            raise ValueError(
                "M3 confirmation resources must share one audit index database"
            )
        self.resource_db_path = resource_paths.pop()
        from backend.audit_agent.task_settings import TaskSettingsStore
        self.task_settings = TaskSettingsStore(self.resource_db_path)

    def task_configuration(self, configuration, connection=None):
        saved = self.task_settings.get(connection)
        if saved["revision"]:
            configuration = configuration.model_copy(update={
                "task_parameters": InvestigationTaskParameters.model_validate(saved["parameters"])})
        return configuration, saved["revision"]

    @contextmanager
    def confirmation_fence(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.resource_db_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def authoritative_draft_fence(self) -> Iterator[sqlite3.Connection]:
        with self.confirmation_fence() as connection:
            yield connection

    def query_options(
        self,
        query: QueryInvestigationOptions,
        *,
        principal: Principal,
    ) -> InvestigationOptions:
        query = QueryInvestigationOptions.model_validate(
            query.model_dump(mode="json")
        )
        offset = self._cursor_offset(query.cursor)
        valid_rulesets: list[tuple[int, RuleSetRevisionSummary]] = []
        blockers: list[InvestigationBlocker] = []
        requested_revision_ids = set(query.ruleset_revision_ids)

        for revision in self.ruleset_service.list_current_published(
            principal=principal
        ):
            revision_id = str(revision.get("id") or "")
            if requested_revision_ids and revision_id not in requested_revision_ids:
                continue
            try:
                summary, content = self._ruleset_revision_resource(revision)
            except Exception:
                if requested_revision_ids:
                    blockers.append(
                        self._blocker(
                            "INVALID_RULESET_REFERENCE",
                            "The requested published RuleSetRevision is unavailable or invalid.",
                            resource_type="ruleset_revision",
                            resource_id=revision_id,
                        )
                    )
                continue
            match_score = self._text_match_score(
                query.domain_hint,
                summary.id,
                summary.ruleset_id,
                *self._ruleset_search_values(content),
            )
            valid_rulesets.append((match_score, summary))

        found_revision_ids = {summary.id for _, summary in valid_rulesets}
        for revision_id in query.ruleset_revision_ids:
            if revision_id not in found_revision_ids and not any(
                item.resource_id == revision_id for item in blockers
            ):
                blockers.append(
                    self._blocker(
                        "NO_PUBLISHED_RULESET",
                        "The requested RuleSetRevision is not available as a published resource.",
                        resource_type="ruleset_revision",
                        resource_id=revision_id,
                    )
                )

        valid_rulesets.sort(
            key=lambda item: (
                -item[0],
                item[1].name,
                item[1].ruleset_id,
                -item[1].version,
                item[1].id,
            )
        )
        ruleset_page = valid_rulesets[offset : offset + query.page_size]
        ruleset_details: list[RuleSetRevisionDetail] = []
        for revision_id in query.include_ruleset_details_for_revision_ids:
            try:
                ruleset_details.append(
                    self._ruleset_revision_detail(revision_id, principal=principal)
                )
            except Exception:
                blockers.append(
                    self._blocker(
                        "INVALID_RULESET_REFERENCE",
                        "The requested published RuleSetRevision is unavailable or invalid.",
                        resource_type="ruleset_revision",
                        resource_id=revision_id,
                    )
                )

        requested_lexicon_ids = set(query.lexicon_ids)
        include_terms = set(query.include_lexicon_terms_for_ids)
        lexicons: list[tuple[int, RecallLexiconSummary]] = []
        categories = (
            self.lexicon_store.list_categories()
            if query.mode == "search"
            else []
        )
        for category in categories:
            category_id = str(category.get("id") or "")
            if requested_lexicon_ids and category_id not in requested_lexicon_ids:
                continue
            lexicons.append(
                (
                    self._text_match_score(
                        query.domain_hint,
                        category_id,
                        category.get("title"),
                        category.get("risk_label"),
                    ),
                    self._lexicon_summary(
                        category_id,
                        category=category,
                        include_terms=category_id in include_terms,
                        term_limit=query.lexicon_term_limit,
                    ),
                )
            )
        existing_lexicon_ids = {item.id for _, item in lexicons}
        for lexicon_id in query.lexicon_ids:
            if lexicon_id not in existing_lexicon_ids:
                lexicons.append(
                    (
                        0,
                        RecallLexiconSummary(
                            id=lexicon_id,
                            enabled_main_term_count=0,
                            available=False,
                        ),
                    )
                )
        lexicons.sort(
            key=lambda item: (
                not item[1].available,
                -item[0],
                item[1].title,
                item[1].id,
            )
        )
        lexicon_page = [
            item for _, item in lexicons[offset : offset + query.page_size]
        ]

        if not valid_rulesets:
            blockers.append(
                self._blocker(
                    "NO_PUBLISHED_RULESET",
                    "No matching published RuleSetRevision is available for the requested scope.",
                    resource_type="ruleset_revision",
                    management_url=_RULES_MANAGEMENT_PATH,
                )
            )
        if query.mode == "search" and not any(item.available for _, item in lexicons):
            blockers.append(
                self._blocker(
                    "NO_PUBLISHED_RECALL_LEXICON",
                    "No available published recall lexicon exists for search mode.",
                    resource_type="recall_lexicon",
                )
            )
        has_more = (
            offset + query.page_size < len(valid_rulesets)
            or offset + query.page_size < len(lexicons)
        )
        platforms = [
            PlatformOption(
                id=Platform(value),
                name=_PLATFORM_NAMES.get(value, value),
                available=value in _ENABLED_CREATION_PLATFORMS,
            )
            for value in _CREATION_PLATFORM_ORDER
            if query.platform is None or value == query.platform.value
        ]
        return InvestigationOptions(
            platforms=platforms,
            ruleset_revisions=[item for _, item in ruleset_page],
            ruleset_revision_details=ruleset_details,
            recall_lexicons=lexicon_page,
            blockers=self._dedupe_blockers(blockers),
            next_cursor=str(offset + query.page_size) if has_more else "",
        )

    def confirmation_preview(
        self,
        draft: InvestigationDraft,
        *,
        principal: Principal,
        resource_connection: sqlite3.Connection | None = None,
    ) -> ConfirmationPreview:
        configuration = InvestigationDraftConfiguration.model_validate(
            draft.configuration.model_dump(mode="json")
        )
        resolution: AuthoritativeDraftResolution | None = None
        blockers: list[InvestigationBlocker] = []
        temporary = configuration.judgement.strategy == "temporary_ruleset"
        try:
            resolution = self.resolve_authoritative_draft(
                configuration,
                principal=principal,
                resource_connection=resource_connection,
            )
        except (ConfigurationValidationError, ResourceStaleError) as exc:
            blockers.append(self._authoritative_error_blocker(exc, draft_id=draft.id))

        effective_configuration = (
            resolution.normalized_configuration
            if resolution is not None
            else configuration
        )
        mode = effective_configuration.investigation.mode
        if resolution is not None:
            blockers.extend(resolution.editable_blockers)
        available_accounts = self._available_crawler_accounts(
            effective_configuration.platform.value,
            connection=resource_connection,
        )
        if not available_accounts:
            blockers.append(
                self._blocker(
                    "collection_service_unavailable",
                    self._collection_unavailable_message(
                        effective_configuration.platform.value
                    ),
                )
            )

        resolved_terms: list[str] = []
        creator_url = ""
        recall_preview = RecallPlanPreview(strategy="none")
        if mode == "search":
            plan = effective_configuration.investigation.recall_plan
            if plan.strategy == "existing_lexicon":
                summary = resolution.recall_lexicon if resolution is not None else None
                resolved_terms = list(plan.enabled_main_terms)
                recall_preview = RecallPlanPreview(
                    strategy="existing_lexicon",
                    lexicon_id=plan.lexicon_id,
                    lexicon_title=summary.title if summary is not None else "",
                    runtime_content_hash=(
                        summary.runtime_content_hash
                        if summary is not None
                        else plan.expected_runtime_content_hash
                    ),
                    enabled_main_term_count=(
                        summary.enabled_main_term_count
                        if summary is not None
                        else len(plan.enabled_main_terms)
                    ),
                    enabled_main_terms=list(plan.enabled_main_terms),
                )
            else:
                resolved_terms = list(plan.terms)
                recall_preview = RecallPlanPreview(
                    strategy="temporary_terms",
                    temporary_terms=list(plan.terms),
                    source_lexicon_ids=list(plan.source_lexicon_ids),
                )
        else:
            creator_url = effective_configuration.investigation.creator_url

        effective_configuration, settings_revision = self.task_configuration(effective_configuration, resource_connection)
        parameters = effective_task_parameters(effective_configuration)
        planned_content_count = min(
            parameters.max_total_notes,
            parameters.max_notes * max(1, len(resolved_terms)),
        )
        if parameters.crawler_account_id and not any(a["id"] == parameters.crawler_account_id for a in available_accounts):
            blockers.append(self._blocker("collection_service_unavailable", "所选采集账号当前不可用，请重新选择。"))
        blockers = self._dedupe_blockers(blockers)
        return ConfirmationPreview(
            task_settings_revision=settings_revision,
            requested_parameters=effective_configuration.task_parameters,
            effective_parameters=parameters,
            estimated_max_contents=planned_content_count,
            draft_id=draft.id,
            draft_revision=draft.current_revision,
            title=draft.title,
            objective=draft.objective,
            mode=mode,
            platform=effective_configuration.platform,
            resolved_search_terms=resolved_terms,
            creator_url=creator_url,
            recall_plan=recall_preview,
            max_notes=planned_content_count,
            max_posts_per_keyword=parameters.max_notes,
            max_comments_per_post=parameters.max_comments,
            get_sub_comment=parameters.get_sub_comment,
            ruleset_revision=(
                resolution.ruleset_revision if resolution is not None else None
            ),
            blockers=blockers,
            can_confirm=not blockers,
            temporary_ruleset=configuration.judgement if temporary else None,
        )

    def validate_temporary_provenance(
        self,
        source_lexicon_ids: list[str],
        *,
        resource_connection: sqlite3.Connection | None,
    ) -> None:
        """Check new provenance at the mutation boundary, under the resource fence."""
        for source_lexicon_id in source_lexicon_ids:
            try:
                self.lexicon_store.get_category(
                    source_lexicon_id, connection=resource_connection
                )
            except KeyError as exc:
                raise ConfigurationValidationError(
                    "A source recall lexicon does not exist.",
                    code="INVALID_SOURCE_LEXICON_REFERENCE",
                    details={
                        "mutation_applied": False,
                        "resource_type": "recall_lexicon",
                        "resource_id": source_lexicon_id,
                    },
                ) from exc

    def resolve_authoritative_draft(
        self,
        configuration: InvestigationDraftConfiguration,
        *,
        principal: Principal,
        resource_connection: sqlite3.Connection | None,
    ) -> AuthoritativeDraftResolution:
        """Validate and snapshot resource identity without judging relevance."""

        configuration = InvestigationDraftConfiguration.model_validate(
            configuration.model_dump(mode="json")
        )
        platform = configuration.platform.value
        if platform not in SUPPORTED_PLATFORMS:
            raise ConfigurationValidationError(
                "The selected platform is not supported for new Draft revisions.",
                code="PLATFORM_MISMATCH",
                details={"mutation_applied": False, "platform": platform},
            )

        selection = configuration.judgement
        ruleset_summary = None
        if selection.strategy == "existing_ruleset":
            try:
                revision = self.ruleset_service.get_published(
                    selection.ruleset_revision_id,
                    principal=principal,
                    connection=resource_connection,
                )
            except Exception as exc:
                raise ConfigurationValidationError(
                    "The selected RuleSetRevision does not exist.",
                    code="INVALID_RESOURCE_REFERENCE",
                    details={
                        "mutation_applied": False,
                        "resource_type": "ruleset_revision",
                        "resource_id": selection.ruleset_revision_id,
                    },
                ) from exc

            raw_version = int(revision.get("version") or 0)
            raw_hash = str(revision.get("content_hash") or "").strip().lower()
            if (
                selection.expected_ruleset_version != raw_version
                or selection.expected_ruleset_content_hash != raw_hash
            ):
                raise ResourceStaleError(
                    "The selected RuleSetRevision identity changed before the Draft revision was saved.",
                    details={
                        "mutation_applied": False,
                        "resource_type": "ruleset_revision",
                        "resource_id": selection.ruleset_revision_id,
                        "resource": {
                            "id": str(revision.get("id") or ""),
                            "ruleset_id": str(revision.get("ruleset_id") or ""),
                            "version": raw_version,
                            "content_hash": raw_hash,
                        },
                    },
                )

            try:
                ruleset_summary, _ = self._ruleset_revision_resource(revision)
            except Exception as exc:
                raise ConfigurationValidationError(
                    "The selected RuleSetRevision is not a valid authoritative resource.",
                    code="INVALID_RULESET_REFERENCE",
                    details={
                        "mutation_applied": False,
                        "resource_type": "ruleset_revision",
                        "resource_id": selection.ruleset_revision_id,
                    },
                ) from exc

            current_revision = self.ruleset_service.get_current_published(
                selection.ruleset_revision_id,
                principal=principal,
                connection=resource_connection,
            )
            if current_revision is None:
                raise ResourceStaleError(
                    "The selected RuleSetRevision is not the RuleSet's current published revision.",
                    details={
                        "mutation_applied": False,
                        "resource_type": "ruleset_revision",
                        "resource_id": selection.ruleset_revision_id,
                        "resource": ruleset_summary.model_dump(mode="json"),
                    },
                )
            try:
                self.ruleset_service.compile_for_execution(
                    selection.ruleset_revision_id,
                    principal=principal,
                    connection=resource_connection,
                )
            except Exception as exc:
                raise ConfigurationValidationError(
                    "The selected RuleSetRevision is not supported by the current compiler/runtime.",
                    code="INVALID_RULESET_REFERENCE",
                    details={
                        "mutation_applied": False,
                        "resource_type": "ruleset_revision",
                        "resource_id": selection.ruleset_revision_id,
                    },
                ) from exc

        if selection.strategy == "temporary_ruleset":
            try:
                from .frozen import compile_temporary
                compile_temporary(selection)
            except Exception as exc:
                raise ConfigurationValidationError(
                    "临时规则无法通过编译校验，请检查规则内容。",
                    code="INVALID_RULESET_REFERENCE",
                ) from exc

        normalized = configuration
        recall_summary: RecallLexiconSummary | None = None
        editable_blockers: list[InvestigationBlocker] = []
        if configuration.investigation.mode == "creator":
            creator_blocker = self._creator_blocker(
                platform, configuration.investigation.creator_url
            )
            if creator_blocker is not None:
                raise ConfigurationValidationError(
                    creator_blocker.message,
                    code=creator_blocker.code,
                    details={
                        "mutation_applied": False,
                        "platform": platform,
                    },
                )
        else:
            plan = configuration.investigation.recall_plan
            if plan.strategy == "existing_lexicon":
                try:
                    recall_summary = self._lexicon_summary(
                        plan.lexicon_id,
                        include_terms=True,
                        resource_connection=resource_connection,
                    )
                except KeyError as exc:
                    raise ConfigurationValidationError(
                        "The selected recall lexicon does not exist.",
                        code="INVALID_RESOURCE_REFERENCE",
                        details={
                            "mutation_applied": False,
                            "resource_type": "recall_lexicon",
                            "resource_id": plan.lexicon_id,
                        },
                    ) from exc
                if (
                    plan.expected_runtime_content_hash
                    != recall_summary.runtime_content_hash
                ):
                    raise ResourceStaleError(
                        "The selected recall lexicon changed before the Draft revision was saved.",
                        details={
                            "mutation_applied": False,
                            "resource_type": "recall_lexicon",
                            "resource_id": plan.lexicon_id,
                            "resource": recall_summary.model_dump(mode="json"),
                        },
                    )
                frozen_plan = plan.model_copy(
                    update={
                        "enabled_main_terms": list(
                            recall_summary.enabled_main_terms
                        )
                    }
                )
                normalized = configuration.model_copy(
                    update={
                        "investigation": configuration.investigation.model_copy(
                            update={"recall_plan": frozen_plan}
                        )
                    }
                )
                if not recall_summary.enabled_main_terms:
                    editable_blockers.append(
                        self._blocker(
                            "NO_SEARCH_TERMS",
                            "At least one confirmed search term is required.",
                        )
                    )
            else:
                if not plan.terms:
                    editable_blockers.append(
                        self._blocker(
                            "NO_SEARCH_TERMS",
                            "At least one confirmed search term is required.",
                        )
                    )

        return AuthoritativeDraftResolution(
            normalized_configuration=normalized,
            ruleset_revision=ruleset_summary,
            recall_lexicon=recall_summary,
            editable_blockers=editable_blockers,
        )

    def snapshot_draft_configuration(
        self,
        configuration: InvestigationDraftConfiguration,
        *,
        principal: Principal,
    ) -> InvestigationDraftConfiguration:
        """Compatibility wrapper over the authoritative Draft resolution boundary."""

        with self.authoritative_draft_fence() as resource_connection:
            return self.resolve_authoritative_draft(
                configuration,
                principal=principal,
                resource_connection=resource_connection,
            ).normalized_configuration

    def resolve_confirmation(
        self,
        draft: InvestigationDraft,
        *,
        principal: Principal,
        resource_connection: sqlite3.Connection | None = None,
    ) -> ConfirmationResolution:
        configuration = InvestigationDraftConfiguration.model_validate(
            draft.configuration.model_dump(mode="json")
        )
        try:
            resolution = self.resolve_authoritative_draft(
                configuration,
                principal=principal,
                resource_connection=resource_connection,
            )
        except ResourceStaleError as exc:
            blocker = self._authoritative_error_blocker(exc, draft_id=draft.id)
            raise ResourceStaleError(
                str(exc),
                details={
                    **exc.details,
                    "resources": [blocker.model_dump(mode="json")],
                },
            ) from exc
        if resolution.editable_blockers:
            blocker = resolution.editable_blockers[0]
            raise ConfigurationValidationError(
                blocker.message,
                code=blocker.code,
                details={
                    "blockers": [
                        item.model_dump(mode="json")
                        for item in resolution.editable_blockers
                    ]
                },
            )
        configuration = resolution.normalized_configuration
        available_accounts = self._available_crawler_accounts(
            configuration.platform.value,
            connection=resource_connection,
        )
        if not available_accounts:
            raise ConfigurationValidationError(
                self._collection_unavailable_message(configuration.platform.value),
                code="collection_service_unavailable",
            )
        configuration, _ = self.task_configuration(configuration, resource_connection)
        parameters = effective_task_parameters(configuration)
        selected_account = next((a for a in available_accounts if a["id"] == parameters.crawler_account_id), None) if parameters.crawler_account_id else available_accounts[0]
        if selected_account is None:
            raise ConfigurationValidationError("所选采集账号当前不可用，请重新选择。", code="collection_service_unavailable")
        mode = configuration.investigation.mode
        collection: dict[str, Any]
        if mode == "search":
            collection_keywords = list(
                configuration.investigation.recall_plan.enabled_main_terms
                if configuration.investigation.recall_plan.strategy
                == "existing_lexicon"
                else configuration.investigation.recall_plan.terms
            )
            planned_content_count = min(
                parameters.max_total_notes,
                parameters.max_notes * max(1, len(collection_keywords)),
            )
            collection = {
                "crawl_mode": "search",
                "keyword_source": "keyword",
                "keywords": collection_keywords,
                "max_notes": parameters.max_notes,
                "max_total_notes": planned_content_count,
                "crawler_account_id": selected_account["id"],
                "run_crawler": True,
            }
        else:
            planned_content_count = min(
                parameters.max_total_notes,
                parameters.max_notes,
            )
            collection = {
                "crawl_mode": "creator",
                "keyword_source": "keyword",
                "keywords": [],
                "creator_url": configuration.investigation.creator_url,
                "max_notes": parameters.max_notes,
                "max_total_notes": planned_content_count,
                "crawler_account_id": selected_account["id"],
                "run_crawler": True,
            }
        collection.update({key: getattr(parameters, key) for key in (
            "start_page", "max_comments", "max_concurrency", "get_sub_comment",
            "max_items_per_minute", "collect_comments", "collect_media",
        )})
        collection["display_name"] = draft.title
        execution_input = {
            "platform": configuration.platform.value,
            "collection": collection,
            "analysis": {
                "analyze_limit": planned_content_count,
                "analysis_batch_size": parameters.analysis_batch_size,
            },
        }
        recall_library_ids = []
        if mode == "search":
            plan = configuration.investigation.recall_plan
            if plan.strategy == "existing_lexicon":
                recall_library_ids = [plan.lexicon_id]
        resolve_direct = getattr(
            self.configuration_resolver,
            "resolve_ruleset_execution",
            None,
        )
        if not callable(resolve_direct):
            raise ConfigurationValidationError(
                "RuleSet-direct execution resolver is not configured"
            )
        resolved = dict(
            resolve_direct(
                InvestigationConfiguration.model_validate(execution_input),
                **({"temporary_ruleset": configuration.judgement}
                   if configuration.judgement.strategy == "temporary_ruleset"
                   else {"ruleset_revision_id": resolution.ruleset_revision.id}),
                recall_library_ids=recall_library_ids,
                principal=principal,
                resource_connection=resource_connection,
            )
        )
        resolved.update(
            {
                "max_notes": parameters.max_notes,
                "max_total_notes": planned_content_count,
                "analyze_limit": planned_content_count,
                "crawler_account_id": selected_account["id"],
                "crawler_account_display_name": selected_account["display_name"],
                "crawler_account_confirmed_state": CrawlerAccountConfirmedState(
                    status="active",
                    has_auth_state=True,
                    auth_state_updated_at=str(
                        selected_account.get("auth_state_updated_at") or ""
                    ),
                    last_validated_at=str(
                        selected_account.get("last_validated_at") or ""
                    ),
                ).model_dump(mode="json"),
            }
        )

        if configuration.task_parameters is not None:
            resolved.update(
                auto_analyze=True,
                collect_comments=parameters.collect_comments,
                collect_media=parameters.collect_media,
            )
        recall_snapshot: ConfirmedRecallPlanSnapshot | None = None
        if mode == "search":
            plan = configuration.investigation.recall_plan
            if plan.strategy == "existing_lexicon":
                resolved["keyword_source"] = "lexicon"
                resolved["lexicon_category"] = plan.lexicon_id
                resolved["lexicon_keywords"] = list(plan.enabled_main_terms)
                recall_snapshot = ConfirmedRecallPlanSnapshot(
                    strategy="existing_lexicon",
                    lexicon_id=plan.lexicon_id,
                    runtime_content_hash=resolution.recall_lexicon.runtime_content_hash,
                    enabled_main_terms=list(plan.enabled_main_terms),
                )
            else:
                resolved["keyword_source"] = "keyword"
                resolved["lexicon_category"] = ""
                resolved["lexicon_keywords"] = []
                recall_snapshot = ConfirmedRecallPlanSnapshot(
                    strategy="temporary_terms",
                    temporary_terms=list(plan.terms),
                    source_lexicon_ids=list(plan.source_lexicon_ids),
                )
        execution = ResolvedExecutionConfiguration.model_validate(resolved)
        resolved_search_terms = (
            list(configuration.investigation.recall_plan.enabled_main_terms)
            if mode == "search"
            and configuration.investigation.recall_plan.strategy == "existing_lexicon"
            else (
                list(configuration.investigation.recall_plan.terms)
                if mode == "search"
                else []
            )
        )
        creator_url = (
            configuration.investigation.creator_url if mode == "creator" else ""
        )
        hash_payload = {
            "mode": mode,
            "platform": configuration.platform.value,
            "resolved_search_terms": resolved_search_terms,
            "creator_url": creator_url,
            "recall_plan": (
                recall_snapshot.model_dump(mode="json")
                if recall_snapshot is not None
                else None
            ),
            **({"temporary_ruleset": configuration.judgement.model_dump(mode="json")}
               if configuration.judgement.strategy == "temporary_ruleset"
               else {"ruleset_revision": resolution.ruleset_revision.model_dump(mode="json")}),
            "execution": execution.model_dump(mode="json"),
        }
        return ConfirmationResolution(
            **hash_payload,
            requested_parameters=configuration.task_parameters,
            config_hash=confirmed_configuration_hash(hash_payload),
        )

    @staticmethod
    def _ruleset_revision_resource(
        revision: dict[str, Any],
    ) -> tuple[RuleSetRevisionSummary, RuleSetContent]:
        revision_id = str(revision.get("id") or "").strip()
        content = RuleSetContent.model_validate(
            {
                key: revision.get(key)
                for key in (
                    "schema_version",
                    "name",
                    "domain",
                    "audit_goal",
                    "general_exemptions",
                    "categories",
                )
            }
        )
        computed_hash = ruleset_content_hash(content)
        stored_hash = str(revision.get("content_hash") or "").strip().lower()
        if (
            not revision_id
            or str(revision.get("status") or "") != "published"
            or len(stored_hash) != 64
            or stored_hash != computed_hash
        ):
            raise ValueError("published RuleSetRevision hash is invalid")
        enabled_rules = sum(
            1
            for category in content.categories
            for rule in category.rules
            if rule.enabled
        )
        ruleset = RuleSetRevisionSummary(
            id=revision_id,
            ruleset_id=str(revision.get("ruleset_id") or ""),
            name=content.name,
            domain=content.domain,
            version=int(revision.get("version") or 0),
            content_hash=stored_hash,
            enabled_rule_count=enabled_rules,
        )
        return ruleset, content

    def _ruleset_revision_detail(
        self,
        revision_id: str,
        *,
        principal: Principal,
    ) -> RuleSetRevisionDetail:
        revision = self.ruleset_service.get_published(
            revision_id,
            principal=principal,
        )
        summary, content = self._ruleset_revision_resource(revision)
        return RuleSetRevisionDetail(
            id=summary.id,
            ruleset_id=summary.ruleset_id,
            name=summary.name,
            domain=summary.domain,
            version=summary.version,
            content_hash=summary.content_hash,
            audit_goal=content.audit_goal,
            general_exemptions=[
                RuleSetExemptionDetail(
                    exemption_id=item.exemption_id,
                    name=item.name,
                    condition=item.condition,
                )
                for item in content.general_exemptions
            ],
            categories=[
                RuleSetCategoryDetail(
                    category_id=category.category_id,
                    name=category.name,
                    description=category.description,
                    order=category.order,
                    rules=[
                        RuleSetRuleDetail(
                            rule_id=rule.rule_id,
                            name=rule.name,
                            hit_condition=rule.hit_condition,
                            suggested_risk_level=rule.suggested_risk_level,
                            rule_exemptions=[
                                RuleSetExemptionDetail(
                                    exemption_id=item.exemption_id,
                                    name=item.name,
                                    condition=item.condition,
                                )
                                for item in rule.rule_exemptions
                            ],
                            application_stages=list(rule.application_stages),
                            adjudication_notes=rule.adjudication_notes,
                            enabled=rule.enabled,
                            order=rule.order,
                        )
                        for rule in category.rules
                    ],
                )
                for category in content.categories
            ],
        )

    def _lexicon_summary(
        self,
        category_id: str,
        *,
        category: dict[str, Any] | None = None,
        include_terms: bool,
        term_limit: int | None = None,
        resource_connection: sqlite3.Connection | None = None,
    ) -> RecallLexiconSummary:
        category = category or self.lexicon_store.get_category(
            category_id, connection=resource_connection
        )
        terms = self.lexicon_store.enabled_main_terms(
            category_id, connection=resource_connection
        )
        returned_terms = (
            terms[:term_limit] if include_terms and term_limit is not None else terms
        ) if include_terms else []
        return RecallLexiconSummary(
            id=category_id,
            title=str(category.get("title") or category_id),
            risk_label=str(category.get("risk_label") or ""),
            enabled_main_term_count=len(terms),
            runtime_content_hash=self.lexicon_store.runtime_content_hash(
                category_id, connection=resource_connection
            ),
            enabled_main_terms=returned_terms,
            enabled_main_terms_returned=len(returned_terms),
            terms_included=include_terms,
            terms_truncated=include_terms and len(returned_terms) < len(terms),
        )

    @staticmethod
    def _ruleset_search_values(content: RuleSetContent) -> tuple[object, ...]:
        values: list[object] = [content.name, content.domain, content.audit_goal]
        values.extend(
            value
            for exemption in content.general_exemptions
            for value in (exemption.name, exemption.condition)
        )
        for category in content.categories:
            values.extend((category.name, category.description))
            for rule in category.rules:
                values.extend(
                    (
                        rule.name,
                        rule.hit_condition,
                        rule.suggested_risk_level,
                        rule.adjudication_notes,
                    )
                )
        return tuple(values)

    @staticmethod
    def _matches_text(hint: str, *values: object) -> bool:
        return (
            not InvestigationResourceService._normalize_search_text(hint)
            or InvestigationResourceService._text_match_score(hint, *values) > 0
        )

    @staticmethod
    def _text_match_score(hint: str, *values: object) -> int:
        normalized_hint = InvestigationResourceService._normalize_search_text(hint)
        if not normalized_hint:
            return 0
        normalized_values = [
            InvestigationResourceService._normalize_search_text(value)
            for value in values
        ]
        normalized_values = [value for value in normalized_values if value]
        if not normalized_values:
            return 0

        compact_hint = normalized_hint.replace(" ", "")
        score = 0
        if any(normalized_hint in value for value in normalized_values):
            score += 10_000
        elif compact_hint and any(
            compact_hint in value.replace(" ", "") for value in normalized_values
        ):
            score += 10_000

        for term in InvestigationResourceService._hint_terms(normalized_hint):
            if any(term in value.replace(" ", "") for value in normalized_values):
                score += len(term) * len(term)
        return score

    @staticmethod
    def _normalize_search_text(value: object) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))

    @staticmethod
    def _hint_terms(normalized_hint: str) -> tuple[str, ...]:
        terms = set(normalized_hint.split())
        for word in normalized_hint.split():
            for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", word):
                for width in range(2, min(4, len(sequence)) + 1):
                    terms.update(
                        sequence[index : index + width]
                        for index in range(len(sequence) - width + 1)
                    )
        return tuple(sorted((term for term in terms if len(term) >= 2)))

    @staticmethod
    def _creator_blocker(
        platform: str, creator_url: str
    ) -> InvestigationBlocker | None:
        try:
            validate_creator_url(platform, creator_url)
            return None
        except CreatorUrlValidationError as exc:
            for other in SUPPORTED_PLATFORMS:
                if other == platform:
                    continue
                try:
                    validate_creator_url(other, creator_url)
                except CreatorUrlValidationError:
                    continue
                return InvestigationResourceService._blocker(
                    "PLATFORM_MISMATCH",
                    "Creator profile URL does not match the selected platform.",
                )
            return InvestigationResourceService._blocker(
                "INVALID_CREATOR_URL", str(exc)
            )

    @staticmethod
    def _management_url(draft_id: str) -> str:
        return f"{_RULES_MANAGEMENT_PATH}&draft_id={draft_id}"

    @classmethod
    def _authoritative_error_blocker(
        cls,
        error: ConfigurationValidationError | ResourceStaleError,
        *,
        draft_id: str,
    ) -> InvestigationBlocker:
        resource_type = str(error.details.get("resource_type") or "")
        resource_id = str(error.details.get("resource_id") or "")
        return cls._blocker(
            error.code,
            str(error),
            resource_type=resource_type,
            resource_id=resource_id,
            latest_safe_summary=dict(error.details.get("resource") or {}),
            management_url=(
                cls._management_url(draft_id)
                if resource_type == "ruleset_revision"
                else ""
            ),
        )

    def _available_crawler_accounts(
        self,
        platform: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        available = [
            account
            for account in self.crawler_account_store.list(
                platform=platform,
                connection=connection,
            )
            if account["status"] == "active"
            and account["has_auth_state"]
            and not account_is_cooling_down(account)
        ]
        return sorted(available, key=lambda account: str(account["id"]))

    @staticmethod
    def _collection_unavailable_message(platform: str) -> str:
        if platform == "dy":
            return "抖音采集服务当前不可用，请稍后重试。"
        return "采集服务当前不可用，请稍后重试。"

    @staticmethod
    def _blocker(
        code: str,
        message: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        latest_safe_summary: dict[str, Any] | None = None,
        management_url: str = "",
    ) -> InvestigationBlocker:
        return InvestigationBlocker(
            code=code,
            message=message,
            resource_type=resource_type,
            resource_id=resource_id,
            latest_safe_summary=latest_safe_summary or {},
            management_url=management_url,
        )

    @staticmethod
    def _dedupe_blockers(
        blockers: list[InvestigationBlocker],
    ) -> list[InvestigationBlocker]:
        seen: set[tuple[str, str, str]] = set()
        output: list[InvestigationBlocker] = []
        for blocker in blockers:
            key = (blocker.code, blocker.resource_type, blocker.resource_id)
            if key not in seen:
                seen.add(key)
                output.append(blocker)
        return output

    @staticmethod
    def _cursor_offset(cursor: str) -> int:
        if not cursor:
            return 0
        try:
            value = int(cursor)
        except ValueError as exc:
            raise ValueError("cursor must be an opaque value returned by this query") from exc
        if value < 0:
            raise ValueError("cursor must be an opaque value returned by this query")
        return value
