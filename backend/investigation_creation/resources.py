from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
import unicodedata

from backend.audit_agent.audit_policy_store import AuditPolicyStore
from backend.audit_agent.creator_url import CreatorUrlValidationError, validate_creator_url
from backend.audit_agent.crawler_adapter import SUPPORTED_PLATFORMS
from backend.audit_agent.lexicon_store import LexiconStore
from backend.rulesets.compiler import content_hash as ruleset_content_hash
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.service import RuleSetService

from .contracts import (
    AuditPolicySelection,
    AuditPolicySummary,
    ConfirmationPreview,
    ConfirmationResolution,
    ConfirmedRecallPlanSnapshot,
    InvestigationBlocker,
    InvestigationDraft,
    InvestigationDraftConfiguration,
    InvestigationConfiguration,
    InvestigationOptions,
    Platform,
    PlatformOption,
    QueryInvestigationOptions,
    RecallLexiconSummary,
    RecallPlanPreview,
    ResolvedExecutionConfiguration,
    RuleSetRevisionSummary,
    confirmed_configuration_hash,
)
from .errors import ConfigurationValidationError, ResourceStaleError
from .principal import Principal


_PLATFORM_NAMES = {"xhs": "小红书", "dy": "抖音", "ks": "快手"}
_CREATION_PLATFORM_ORDER = ("dy", "xhs", "ks")
_RULES_MANAGEMENT_PATH = "/rule-assistant/rulesets?return_to=/investigation"


class InvestigationResourceService:
    """Read and freeze real M3 configuration resources through stable stores."""

    def __init__(
        self,
        *,
        lexicon_store: LexiconStore,
        policy_store: AuditPolicyStore,
        ruleset_service: RuleSetService,
        configuration_resolver: Any,
    ) -> None:
        self.lexicon_store = lexicon_store
        self.policy_store = policy_store
        self.ruleset_service = ruleset_service
        self.configuration_resolver = configuration_resolver
        resource_paths = {
            Path(path).expanduser().resolve()
            for path in (
                self.lexicon_store.db_path,
                self.policy_store.db_path,
                self.ruleset_service.store.db_path,
            )
        }
        if len(resource_paths) != 1:
            raise ValueError(
                "M3 confirmation resources must share one audit index database"
            )
        self.resource_db_path = resource_paths.pop()

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
        valid_policies: list[
            tuple[int, AuditPolicySummary, RuleSetRevisionSummary]
        ] = []
        blockers: list[InvestigationBlocker] = []
        requested_policy_ids = set(query.audit_policy_ids)

        for policy in self.policy_store.list(include_drafts=True):
            policy_id = str(policy.get("id") or "")
            if requested_policy_ids and policy_id not in requested_policy_ids:
                continue
            summary, ruleset, error = self._policy_resource(policy, principal=principal)
            if error is not None:
                if requested_policy_ids:
                    blockers.append(error)
                continue
            if summary is None or ruleset is None:
                continue
            match_score = self._domain_hint_score(
                query.domain_hint, policy, summary, ruleset
            )
            if query.domain_hint and not requested_policy_ids and match_score <= 0:
                continue
            valid_policies.append((match_score, summary, ruleset))

        found_policy_ids = {summary.id for _, summary, _ in valid_policies}
        for policy_id in query.audit_policy_ids:
            if policy_id not in found_policy_ids and not any(
                item.resource_id == policy_id for item in blockers
            ):
                blockers.append(
                    self._blocker(
                        "NO_PUBLISHED_AUDIT_POLICY",
                        "The requested AuditPolicy is not available as a valid published resource.",
                        resource_type="audit_policy",
                        resource_id=policy_id,
                    )
                )

        valid_policies.sort(key=lambda item: (-item[0], item[1].name, item[1].id))
        policy_page = valid_policies[offset : offset + query.page_size]
        policy_summaries = [item[1] for item in policy_page]
        rulesets_by_id = {item[2].id: item[2] for item in policy_page}

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

        if not valid_policies:
            blockers.append(
                self._blocker(
                    "NO_PUBLISHED_AUDIT_POLICY",
                    "No matching published AuditPolicy is available for the requested scope.",
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
            offset + query.page_size < len(valid_policies)
            or offset + query.page_size < len(lexicons)
        )
        platforms = [
            PlatformOption(
                id=Platform(value),
                name=_PLATFORM_NAMES.get(value, value),
            )
            for value in _CREATION_PLATFORM_ORDER
            if value in SUPPORTED_PLATFORMS
            if query.platform is None or value == query.platform.value
        ]
        return InvestigationOptions(
            platforms=platforms,
            audit_policies=policy_summaries,
            ruleset_revisions=list(rulesets_by_id.values()),
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
        mode = configuration.investigation.mode
        blockers: list[InvestigationBlocker] = []
        policy_summary: AuditPolicySummary | None = None
        ruleset_summary: RuleSetRevisionSummary | None = None
        management_url = self._management_url(draft.id)

        if configuration.audit_policy is None:
            blockers.append(
                self._blocker(
                    "NO_PUBLISHED_AUDIT_POLICY",
                    "A valid published AuditPolicy must be selected before confirmation.",
                    management_url=management_url,
                )
            )
        else:
            policy = self.policy_store.get(
                configuration.audit_policy.id,
                connection=resource_connection,
            )
            if policy is None:
                blockers.append(
                    self._blocker(
                        "RESOURCE_STALE",
                        "The selected AuditPolicy is no longer available.",
                        resource_type="audit_policy",
                        resource_id=configuration.audit_policy.id,
                        management_url=management_url,
                    )
                )
            else:
                policy_summary, ruleset_summary, error = self._policy_resource(
                    policy,
                    principal=principal,
                    management_url=management_url,
                    resource_connection=resource_connection,
                )
                if error is not None:
                    blockers.append(
                        self._blocker(
                            "RESOURCE_STALE",
                            "The selected AuditPolicy or RuleSetRevision changed and is no longer confirmable.",
                            resource_type=error.resource_type,
                            resource_id=error.resource_id,
                            latest_safe_summary={
                                "available": False,
                                "reason_code": error.code,
                            },
                            management_url=management_url,
                        )
                    )
                elif policy_summary is not None and ruleset_summary is not None:
                    blockers.extend(
                        self._selection_drift_blockers(
                            configuration.audit_policy,
                            policy_summary,
                            ruleset_summary,
                            management_url=management_url,
                        )
                    )

        resolved_terms: list[str] = []
        creator_url = ""
        recall_preview = RecallPlanPreview(strategy="none")
        if mode == "search":
            plan = configuration.investigation.recall_plan
            if plan.strategy == "existing_lexicon":
                try:
                    summary = self._lexicon_summary(
                        plan.lexicon_id,
                        include_terms=True,
                        resource_connection=resource_connection,
                    )
                except KeyError:
                    blockers.append(
                        self._blocker(
                            "RESOURCE_STALE",
                            "The selected recall lexicon is no longer available.",
                            resource_type="recall_lexicon",
                            resource_id=plan.lexicon_id,
                        )
                    )
                    summary = RecallLexiconSummary(
                        id=plan.lexicon_id,
                        enabled_main_term_count=0,
                        available=False,
                    )
                resolved_terms = list(plan.enabled_main_terms)
                recall_preview = RecallPlanPreview(
                    strategy="existing_lexicon",
                    lexicon_id=summary.id,
                    lexicon_title=summary.title,
                    runtime_content_hash=summary.runtime_content_hash,
                    enabled_main_term_count=summary.enabled_main_term_count,
                    enabled_main_terms=list(plan.enabled_main_terms),
                )
                if (
                    summary.available
                    and plan.expected_runtime_content_hash
                    != summary.runtime_content_hash
                ):
                    blockers.append(
                        self._blocker(
                            "RESOURCE_STALE",
                            "The selected recall lexicon changed after the Draft was saved.",
                            resource_type="recall_lexicon",
                            resource_id=plan.lexicon_id,
                            latest_safe_summary=summary.model_dump(mode="json"),
                        )
                    )
                if summary.available and list(plan.enabled_main_terms) != list(
                    summary.enabled_main_terms
                ):
                    blockers.append(
                        self._blocker(
                            "RESOURCE_STALE",
                            "The saved enabled main-term snapshot no longer matches the selected recall lexicon.",
                            resource_type="recall_lexicon",
                            resource_id=plan.lexicon_id,
                            latest_safe_summary=summary.model_dump(mode="json"),
                        )
                    )
            else:
                resolved_terms = list(plan.terms)
                recall_preview = RecallPlanPreview(
                    strategy="temporary_terms",
                    temporary_terms=list(plan.terms),
                    source_lexicon_ids=list(plan.source_lexicon_ids),
                )
            if not resolved_terms:
                blockers.append(
                    self._blocker(
                        "NO_SEARCH_TERMS",
                        "At least one confirmed search term is required.",
                    )
                )
        else:
            creator_url = configuration.investigation.creator_url
            creator_error = self._creator_blocker(
                configuration.platform.value, creator_url
            )
            if creator_error is not None:
                blockers.append(creator_error)

        blockers = self._dedupe_blockers(blockers)
        return ConfirmationPreview(
            draft_id=draft.id,
            draft_revision=draft.current_revision,
            title=draft.title,
            objective=draft.objective,
            mode=mode,
            platform=configuration.platform,
            resolved_search_terms=resolved_terms,
            creator_url=creator_url,
            recall_plan=recall_preview,
            audit_policy=policy_summary,
            ruleset_revision=ruleset_summary,
            blockers=blockers,
            can_confirm=not blockers,
        )

    def snapshot_draft_configuration(
        self,
        configuration: InvestigationDraftConfiguration,
        *,
        principal: Principal,
    ) -> InvestigationDraftConfiguration:
        """Freeze real search resource content into each persisted Draft revision."""

        configuration = InvestigationDraftConfiguration.model_validate(
            configuration.model_dump(mode="json")
        )
        if configuration.investigation.mode != "search":
            return configuration
        plan = configuration.investigation.recall_plan
        if plan.strategy != "existing_lexicon":
            return configuration
        try:
            summary = self._lexicon_summary(plan.lexicon_id, include_terms=True)
        except KeyError as exc:
            raise ConfigurationValidationError(
                "The selected recall lexicon is not available.",
                code="NO_PUBLISHED_RECALL_LEXICON",
                details={"resource_id": plan.lexicon_id},
            ) from exc
        if plan.expected_runtime_content_hash != summary.runtime_content_hash:
            raise ResourceStaleError(
                "The selected recall lexicon changed before the Draft revision was saved.",
                details={
                    "resource": summary.model_dump(mode="json"),
                },
            )
        frozen_plan = plan.model_copy(
            update={"enabled_main_terms": list(summary.enabled_main_terms)}
        )
        return configuration.model_copy(
            update={
                "investigation": configuration.investigation.model_copy(
                    update={"recall_plan": frozen_plan}
                )
            }
        )

    def resolve_confirmation(
        self,
        draft: InvestigationDraft,
        *,
        principal: Principal,
        resource_connection: sqlite3.Connection | None = None,
    ) -> ConfirmationResolution:
        preview = self.confirmation_preview(
            draft,
            principal=principal,
            resource_connection=resource_connection,
        )
        if preview.blockers:
            stale = [item for item in preview.blockers if item.code == "RESOURCE_STALE"]
            if stale:
                raise ResourceStaleError(
                    "one or more selected resources changed after the Draft was saved",
                    details={
                        "resources": [item.model_dump(mode="json") for item in stale]
                    },
                )
            blocker = preview.blockers[0]
            raise ConfigurationValidationError(
                blocker.message,
                code=blocker.code,
                details={
                    "blockers": [
                        item.model_dump(mode="json") for item in preview.blockers
                    ]
                },
            )
        if preview.audit_policy is None or preview.ruleset_revision is None:
            raise ConfigurationValidationError(
                "published AuditPolicy resolution is incomplete",
                code="NO_PUBLISHED_AUDIT_POLICY",
            )

        configuration = InvestigationDraftConfiguration.model_validate(
            draft.configuration.model_dump(mode="json")
        )
        mode = configuration.investigation.mode
        collection: dict[str, Any]
        if mode == "search":
            collection = {
                "crawl_mode": "search",
                "keyword_source": "keyword",
                "keywords": list(preview.resolved_search_terms),
                "max_notes": 1,
                "run_crawler": True,
            }
        else:
            collection = {
                "crawl_mode": "creator",
                "keyword_source": "keyword",
                "keywords": [],
                "creator_url": preview.creator_url,
                "max_notes": 1,
                "run_crawler": True,
            }
        legacy = {
            "platform": configuration.platform.value,
            "collection": collection,
            "analysis": {"policy_id": preview.audit_policy.id},
        }
        resolved = dict(
            self.configuration_resolver.resolve(
                InvestigationConfiguration.model_validate(legacy),
                principal=principal,
                resource_connection=resource_connection,
            )
        )
        resolved["max_notes"] = 1

        recall_snapshot: ConfirmedRecallPlanSnapshot | None = None
        if mode == "search":
            plan = configuration.investigation.recall_plan
            if plan.strategy == "existing_lexicon":
                resolved["keyword_source"] = "lexicon"
                resolved["lexicon_category"] = plan.lexicon_id
                resolved["lexicon_keywords"] = list(preview.resolved_search_terms)
                recall_snapshot = ConfirmedRecallPlanSnapshot(
                    strategy="existing_lexicon",
                    lexicon_id=plan.lexicon_id,
                    runtime_content_hash=preview.recall_plan.runtime_content_hash,
                    enabled_main_terms=list(preview.resolved_search_terms),
                )
            else:
                resolved["keyword_source"] = "keyword"
                resolved["lexicon_category"] = ""
                resolved["lexicon_keywords"] = []
                recall_snapshot = ConfirmedRecallPlanSnapshot(
                    strategy="temporary_terms",
                    temporary_terms=list(preview.resolved_search_terms),
                    source_lexicon_ids=list(plan.source_lexicon_ids),
                )
        execution = ResolvedExecutionConfiguration.model_validate(resolved)
        hash_payload = {
            "mode": mode,
            "platform": configuration.platform.value,
            "resolved_search_terms": list(preview.resolved_search_terms),
            "creator_url": preview.creator_url,
            "recall_plan": (
                recall_snapshot.model_dump(mode="json")
                if recall_snapshot is not None
                else None
            ),
            "audit_policy": preview.audit_policy.model_dump(mode="json"),
            "ruleset_revision": preview.ruleset_revision.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json"),
        }
        return ConfirmationResolution(
            **hash_payload,
            config_hash=confirmed_configuration_hash(hash_payload),
        )

    def _policy_resource(
        self,
        policy: dict[str, Any],
        *,
        principal: Any,
        management_url: str = _RULES_MANAGEMENT_PATH,
        resource_connection: sqlite3.Connection | None = None,
    ) -> tuple[
        AuditPolicySummary | None,
        RuleSetRevisionSummary | None,
        InvestigationBlocker | None,
    ]:
        policy_id = str(policy.get("id") or "")
        if (
            str(policy.get("status") or "").lower() != "published"
            or not str(policy.get("published_version") or "").strip()
            or not isinstance(policy.get("published_config"), dict)
            or not policy.get("published_config")
        ):
            return (
                None,
                None,
                self._blocker(
                    "NO_PUBLISHED_AUDIT_POLICY",
                    "AuditPolicy is not available as a published configuration.",
                    resource_type="audit_policy",
                    resource_id=policy_id,
                    management_url=management_url,
                ),
            )
        config = dict(policy["published_config"])
        revision_id = str(config.get("ruleset_revision_id") or "").strip()
        if not revision_id:
            return (
                None,
                None,
                self._blocker(
                    "INVALID_RULESET_REFERENCE",
                    "Published AuditPolicy does not reference a published RuleSetRevision.",
                    resource_type="audit_policy",
                    resource_id=policy_id,
                    management_url=management_url,
                ),
            )
        try:
            revision = self.ruleset_service.get_published(
                revision_id,
                principal=principal,
                connection=resource_connection,
            )
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
                str(revision.get("status") or "") != "published"
                or len(stored_hash) != 64
                or stored_hash != computed_hash
            ):
                raise ValueError("published RuleSetRevision hash is invalid")
        except Exception:
            return (
                None,
                None,
                self._blocker(
                    "INVALID_RULESET_REFERENCE",
                    "Published AuditPolicy references an unavailable or invalid RuleSetRevision.",
                    resource_type="ruleset_revision",
                    resource_id=revision_id,
                    management_url=management_url,
                ),
            )
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
        policy_hash = self._hash_json(config)
        summary = AuditPolicySummary(
            id=policy_id,
            name=str(policy.get("name") or policy_id),
            description=str(policy.get("description") or ""),
            published_version=str(policy.get("published_version") or ""),
            published_config_hash=policy_hash,
            ruleset_revision_id=revision_id,
            ruleset_version=ruleset.version,
            ruleset_content_hash=ruleset.content_hash,
            domain=ruleset.domain,
        )
        return summary, ruleset, None

    def _selection_drift_blockers(
        self,
        selection: AuditPolicySelection,
        policy: AuditPolicySummary,
        ruleset: RuleSetRevisionSummary,
        *,
        management_url: str,
    ) -> list[InvestigationBlocker]:
        blockers: list[InvestigationBlocker] = []
        if (
            selection.expected_published_version != policy.published_version
            or selection.expected_published_config_hash
            != policy.published_config_hash
            or selection.expected_ruleset_revision_id != policy.ruleset_revision_id
        ):
            blockers.append(
                self._blocker(
                    "RESOURCE_STALE",
                    "The selected AuditPolicy changed after the Draft was saved.",
                    resource_type="audit_policy",
                    resource_id=selection.id,
                    latest_safe_summary=policy.model_dump(mode="json"),
                    management_url=management_url,
                )
            )
        if (
            selection.expected_ruleset_revision_id != ruleset.id
            or selection.expected_ruleset_version != ruleset.version
            or selection.expected_ruleset_content_hash != ruleset.content_hash
        ):
            blockers.append(
                self._blocker(
                    "RESOURCE_STALE",
                    "The selected RuleSetRevision changed after the Draft was saved.",
                    resource_type="ruleset_revision",
                    resource_id=selection.expected_ruleset_revision_id,
                    latest_safe_summary=ruleset.model_dump(mode="json"),
                    management_url=management_url,
                )
            )
        return blockers

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
    def _domain_hint_score(
        hint: str,
        policy: dict[str, Any],
        summary: AuditPolicySummary,
        ruleset: RuleSetRevisionSummary,
    ) -> int:
        return InvestigationResourceService._text_match_score(
            hint,
            summary.id,
            summary.name,
            summary.description,
            summary.domain,
            ruleset.name,
            policy.get("published_config", {}).get("library_ids"),
        )

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

    @staticmethod
    def _hash_json(value: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
