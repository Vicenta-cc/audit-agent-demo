from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable

from backend.audit_agent.audit_policy_store import (
    AuditPolicyStore,
    TaskAuditConfigRevisionStore,
)
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.config import settings
from backend.audit_agent.creator_url import validate_creator_url
from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.job_state import available_job_actions
from backend.audit_agent.lexicon_store import LexiconStore
from backend.audit_agent.pipeline import AuditPipeline
from backend.audit_agent.rule_compiler import (
    DEFAULT_THRESHOLDS,
    TEMPLATE_IMPORTANCE,
    compile_rule_profile,
    normalize_capabilities,
    normalize_library_ids,
)
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.reporting.runtime import R31ReportRuntime
from backend.reporting.store import ReportStore
from backend.rulesets.service import RuleSetService

from .contracts import (
    InvestigationConfiguration,
    InvestigationRun,
    ResolvedExecutionConfiguration,
    RunStatus,
    parse_confirmed_configuration_snapshot,
)
from .errors import (
    AuthoritativeAuditProviderUnavailableError,
    ConfigurationValidationError,
    CrawlerAccountAuthenticationRequiredError,
)


class InvestigationConfigurationResolver:
    """Resolve editable selections into the immutable execution snapshot."""

    def __init__(
        self,
        *,
        lexicon_store: LexiconStore | None = None,
        policy_store: AuditPolicyStore | None = None,
        crawler_account_store: CrawlerAccountStore | None = None,
        ruleset_service: RuleSetService | None = None,
        principal_provider: Callable[[], Any] | None = None,
        crawler_account_authorizer: Callable[[Any, str], bool] | None = None,
    ) -> None:
        self.lexicon_store = lexicon_store or LexiconStore()
        self.policy_store = policy_store or AuditPolicyStore()
        self.crawler_account_store = crawler_account_store or CrawlerAccountStore()
        self.ruleset_service = ruleset_service
        self.principal_provider = principal_provider
        self.crawler_account_authorizer = crawler_account_authorizer

    def resolve(
        self,
        configuration: InvestigationConfiguration,
        *,
        principal: Any | None = None,
        resource_connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        validated = InvestigationConfiguration.model_validate(
            configuration.model_dump(mode="json")
        )
        platform = validated.platform.value
        collection = validated.collection
        analysis = validated.analysis
        crawl_mode = collection.crawl_mode.value
        policy_id = analysis.policy_id
        policy = (
            self.policy_store.get(policy_id, connection=resource_connection)
            if policy_id
            else None
        )
        if policy_id and policy is None:
            raise ConfigurationValidationError(f"audit policy not found: {policy_id}")
        policy_config = self.policy_store.config_for_use(policy) if policy else {}
        source_policy_name = str((policy or {}).get("name") or "Custom audit configuration")
        source_policy_version = (
            self.policy_store.version_for_use(policy) if policy else ""
        )

        library_ids = normalize_library_ids(
            policy_config.get("library_ids") or analysis.library_ids,
            analysis.library_ids[0] if analysis.library_ids else "soft",
        )
        capabilities = normalize_capabilities(
            policy_config.get("capabilities")
            or [capability.value for capability in analysis.capabilities]
        )
        scoring_template = str(
            policy_config.get("scoring_template")
            or analysis.scoring_template.value
            or "balanced"
        )
        if scoring_template not in TEMPLATE_IMPORTANCE:
            scoring_template = "balanced"
        ruleset_revision_id = str(policy_config.get("ruleset_revision_id") or "")
        if ruleset_revision_id:
            if not (policy or {}).get("published_config") or not (policy or {}).get(
                "published_version"
            ):
                raise ConfigurationValidationError(
                    "RuleSet-backed policy must be published before it can be frozen"
                )
            if self.ruleset_service is None or (
                principal is None and self.principal_provider is None
            ):
                raise ConfigurationValidationError(
                    "RuleSet-backed policy requires the RuleSet application service"
                )
            try:
                policy_rule_snapshot = policy_config.get("rule_snapshot")
                policy_thresholds = (
                    policy_rule_snapshot.get("thresholds")
                    if isinstance(policy_rule_snapshot, dict)
                    else None
                )
                compiled = self.ruleset_service.compile_for_execution(
                    ruleset_revision_id,
                    audit_policy={
                        "id": policy_id,
                        "name": source_policy_name,
                        "version": source_policy_version,
                        "ruleset_revision_id": ruleset_revision_id,
                        "library_ids": library_ids,
                        "capabilities": capabilities,
                        "scoring_template": scoring_template,
                        "thresholds": policy_thresholds
                        or policy_config.get("thresholds")
                        or DEFAULT_THRESHOLDS,
                        "policy_config": policy_config,
                    },
                    principal=(
                        principal
                        if principal is not None
                        else self.principal_provider()
                    ),
                    connection=resource_connection,
                )
            except Exception as exc:
                raise ConfigurationValidationError(str(exc)) from exc
            knowledge_packages: list[dict[str, Any]] = []
            rule_snapshot = dict(compiled["rule_snapshot"])
            prompt_profile_snapshot = dict(compiled["prompt_profile_snapshot"])
        else:
            incoming_rule_snapshot = policy_config.get("rule_snapshot") or {}
            try:
                knowledge_packages = self.lexicon_store.get_knowledge_packages(
                    library_ids
                )
            except KeyError as exc:
                raise ConfigurationValidationError(
                    f"recall library not found: {exc.args[0]}"
                ) from exc
            compiled = compile_rule_profile(
                libraries=knowledge_packages,
                capabilities=capabilities,
                scoring_template=scoring_template,
                rule_snapshot=incoming_rule_snapshot,
            )
            rule_snapshot = dict(compiled.get("rule_snapshot") or {})
            prompt_profile_snapshot = {
                key: value for key, value in compiled.items() if key != "rule_snapshot"
            }

        keyword_source = collection.keyword_source.value
        keywords = list(collection.keywords)
        lexicon_keywords: list[str] = []
        if crawl_mode == "search" and keyword_source == "lexicon":
            lexicon_keywords = self._enabled_keywords(library_ids)
            if not lexicon_keywords:
                raise ConfigurationValidationError(
                    "platform search keywords are required for the selected recall libraries"
                )
            keywords = lexicon_keywords
        if crawl_mode == "search" and not keywords:
            raise ConfigurationValidationError("at least one search keyword is required")

        creator_url = collection.creator_url
        if crawl_mode == "creator":
            creator_url = validate_creator_url(platform, creator_url)

        crawler_account_id = collection.crawler_account_id or ""
        crawler_account_display_name = ""
        if crawler_account_id:
            account = self.crawler_account_store.get(
                crawler_account_id,
                connection=resource_connection,
            )
            if account is None:
                raise ConfigurationValidationError("crawler account not found")
            if str(account.get("platform")) != platform:
                raise ConfigurationValidationError(
                    "crawler account does not match the selected platform"
                )
            if account.get("status") != "active" or not account.get("has_auth_state"):
                raise ConfigurationValidationError("crawler account is not ready")
            resolved_principal = (
                principal
                if principal is not None
                else self.principal_provider()
                if self.principal_provider is not None
                else None
            )
            if self.crawler_account_authorizer is not None and (
                resolved_principal is None
                or not self.crawler_account_authorizer(
                    resolved_principal, crawler_account_id
                )
            ):
                raise ConfigurationValidationError("crawler account not found")
            crawler_account_display_name = str(account.get("display_name") or "")

        run_crawler = collection.run_crawler
        source_output_id = collection.source_output_id or ""
        audit_config = {
            "schema_version": 2 if ruleset_revision_id else "1.0",
            "source_policy_id": policy_id,
            "source_policy_name": source_policy_name,
            "source_policy_version": source_policy_version,
            "library_ids": library_ids,
            "capabilities": capabilities,
            "scoring_template": scoring_template,
            "thresholds": rule_snapshot.get("thresholds") or DEFAULT_THRESHOLDS,
            "scoring_rules": rule_snapshot.get("scoring_rules") or [],
            "prompt_version": str(prompt_profile_snapshot.get("prompt_version") or ""),
        }
        if ruleset_revision_id:
            audit_config.update(
                {
                    "ruleset_ref": rule_snapshot["ruleset_ref"],
                    "system_template_version": prompt_profile_snapshot[
                        "system_template_version"
                    ],
                    "compiler_version": prompt_profile_snapshot["compiler_version"],
                }
            )
        revision_payload = {
            "source_policy_id": policy_id,
            "source_policy_name": source_policy_name,
            "source_policy_version": source_policy_version,
            "audit_config": audit_config,
            "knowledge_package_snapshots": knowledge_packages,
            "rule_snapshot": rule_snapshot,
            "prompt_profile_snapshot": prompt_profile_snapshot,
        }
        revision_payload["config_hash"] = (
            str(compiled["config_hash"])
            if ruleset_revision_id
            else self._hash(revision_payload)
        )
        resolved = {
            "platform": platform,
            "display_name": collection.display_name,
            "crawl_mode": crawl_mode,
            "keyword": ",".join(keywords),
            "keyword_source": keyword_source if crawl_mode == "search" else "keyword",
            "lexicon_category": library_ids[0],
            "library_ids": library_ids,
            "capabilities": capabilities,
            "scoring_template": scoring_template,
            "rule_snapshot": rule_snapshot,
            "lexicon_keywords": lexicon_keywords,
            "creator_url": creator_url,
            "creator_id": creator_url,
            "start_page": collection.start_page,
            "max_notes": collection.max_notes,
            "max_total_notes": collection.max_total_notes,
            "max_comments": collection.max_comments,
            "max_concurrency": collection.max_concurrency,
            "max_items_per_minute": collection.max_items_per_minute,
            "crawler_account_id": crawler_account_id or None,
            "crawler_account_display_name": crawler_account_display_name,
            "crawler_account_confirmed_state": None,
            "get_sub_comment": collection.get_sub_comment,
            "analyze_limit": analysis.analyze_limit,
            "run_crawler": run_crawler,
            "source_output_id": source_output_id or None,
            "analysis_batch_size": analysis.analysis_batch_size,
            "prompt_profile_snapshot": prompt_profile_snapshot,
            "policy_id": policy_id,
            "audit_config_revision": revision_payload,
        }
        return ResolvedExecutionConfiguration.model_validate(resolved).model_dump(
            mode="json"
        )

    def resolve_ruleset_execution(
        self,
        configuration: InvestigationConfiguration,
        *,
        ruleset_revision_id: str | None = None,
        temporary_ruleset: Any = None,
        recall_library_ids: list[str],
        principal: Any,
        resource_connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Package M3 execution from a validated formal revision or inline source."""

        validated = InvestigationConfiguration.model_validate(
            configuration.model_dump(mode="json")
        )
        try:
            if temporary_ruleset is not None:
                if ruleset_revision_id is not None:
                    raise ValueError("exactly one RuleSet source is required")
                from .frozen import compile_temporary
                compiled = compile_temporary(temporary_ruleset)
            else:
                if self.ruleset_service is None:
                    raise ValueError("RuleSet execution requires the RuleSet service")
                compiled = self.ruleset_service.compile_for_execution(
                    ruleset_revision_id, principal=principal, connection=resource_connection,
                )
        except Exception as exc:
            raise ConfigurationValidationError(str(exc)) from exc

        platform = validated.platform.value
        collection = validated.collection
        crawl_mode = collection.crawl_mode.value
        keywords = list(collection.keywords)
        if crawl_mode == "search" and not keywords:
            raise ConfigurationValidationError("at least one search keyword is required")
        creator_url = collection.creator_url
        if crawl_mode == "creator":
            creator_url = validate_creator_url(platform, creator_url)

        crawler_account_id = collection.crawler_account_id or ""
        crawler_account_display_name = ""
        if crawler_account_id:
            account = self.crawler_account_store.get(
                crawler_account_id,
                connection=resource_connection,
            )
            if account is None:
                raise ConfigurationValidationError("crawler account not found")
            if str(account.get("platform")) != platform:
                raise ConfigurationValidationError(
                    "crawler account does not match the selected platform"
                )
            if account.get("status") != "active" or not account.get("has_auth_state"):
                raise ConfigurationValidationError("crawler account is not ready")
            if self.crawler_account_authorizer is not None and not self.crawler_account_authorizer(
                principal, crawler_account_id
            ):
                raise ConfigurationValidationError("crawler account not found")
            crawler_account_display_name = str(account.get("display_name") or "")

        rule_snapshot = dict(compiled["rule_snapshot"])
        prompt_profile_snapshot = dict(compiled["prompt_profile_snapshot"])
        system_runtime = dict(compiled.get("audit_policy_snapshot") or {})
        capabilities = list(system_runtime.get("capabilities") or [])
        scoring_template = str(system_runtime.get("scoring_template") or "balanced")
        audit_config = {
            "schema_version": 2,
            "source_policy_id": "",
            "source_policy_name": "",
            "source_policy_version": "",
            "library_ids": list(recall_library_ids),
            "capabilities": capabilities,
            "scoring_template": scoring_template,
            "thresholds": rule_snapshot.get("thresholds") or DEFAULT_THRESHOLDS,
            "scoring_rules": rule_snapshot.get("scoring_rules") or [],
            "prompt_version": str(prompt_profile_snapshot.get("prompt_version") or ""),
            **({"temporary_ruleset": rule_snapshot["temporary_ruleset"]}
               if "temporary_ruleset" in rule_snapshot
               else {"ruleset_ref": rule_snapshot["ruleset_ref"]}),
            "system_template_version": prompt_profile_snapshot[
                "system_template_version"
            ],
            "compiler_version": prompt_profile_snapshot["compiler_version"],
        }
        revision_payload = {
            "source_policy_id": "",
            "source_policy_name": "",
            "source_policy_version": "",
            "audit_config": audit_config,
            "knowledge_package_snapshots": [],
            "rule_snapshot": rule_snapshot,
            "prompt_profile_snapshot": prompt_profile_snapshot,
            "config_hash": str(compiled["config_hash"]),
        }
        resolved = {
            "platform": platform,
            "display_name": collection.display_name,
            "crawl_mode": crawl_mode,
            "keyword": ",".join(keywords),
            "keyword_source": "keyword",
            "lexicon_category": recall_library_ids[0] if recall_library_ids else "",
            "library_ids": list(recall_library_ids),
            "capabilities": capabilities,
            "scoring_template": scoring_template,
            "rule_snapshot": rule_snapshot,
            "lexicon_keywords": [],
            "creator_url": creator_url,
            "creator_id": creator_url,
            "start_page": collection.start_page,
            "max_notes": collection.max_notes,
            "max_total_notes": collection.max_total_notes,
            "max_comments": collection.max_comments,
            "max_concurrency": collection.max_concurrency,
            "max_items_per_minute": collection.max_items_per_minute,
            "crawler_account_id": crawler_account_id or None,
            "crawler_account_display_name": crawler_account_display_name,
            "crawler_account_confirmed_state": None,
            "get_sub_comment": collection.get_sub_comment,
            "analyze_limit": validated.analysis.analyze_limit,
            "run_crawler": collection.run_crawler,
            "source_output_id": collection.source_output_id,
            "analysis_batch_size": validated.analysis.analysis_batch_size,
            "prompt_profile_snapshot": prompt_profile_snapshot,
            "policy_id": "",
            "audit_config_revision": revision_payload,
        }
        return ResolvedExecutionConfiguration.model_validate(resolved).model_dump(
            mode="json"
        )

    def _enabled_keywords(self, library_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for library_id in library_ids:
            for value in self.lexicon_store.enabled_search_keywords(library_id):
                cleaned = str(value).strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    output.append(cleaned)
        return output

    @staticmethod
    def _hash(value: dict[str, Any]) -> str:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AuditPipelineExecutionAdapter:
    """Stable M3 adapter around the existing Job and AuditPipeline contracts."""

    def __init__(
        self,
        *,
        job_store: JobStore | None = None,
        ingestion_store: IngestionStore | None = None,
        audit_result_store: AuditResultStore | None = None,
        revision_store: TaskAuditConfigRevisionStore | None = None,
        pipeline_factory: Callable[..., Any] = AuditPipeline,
        crawler_account_store: CrawlerAccountStore | None = None,
        test_provider_validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.job_store = job_store or JobStore()
        self.ingestion_store = ingestion_store or IngestionStore()
        self.audit_result_store = audit_result_store or AuditResultStore(
            self.ingestion_store.db_path
        )
        self.revision_store = revision_store or TaskAuditConfigRevisionStore()
        self.pipeline_factory = pipeline_factory
        self.crawler_account_store = crawler_account_store or CrawlerAccountStore()
        self._provider_validator = (
            test_provider_validator
            if test_provider_validator is not None
            else AuditPipeline.authoritative_provider_validator
        )

    def ensure_job(self, run: InvestigationRun) -> str:
        snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
        configuration = snapshot.execution.model_dump(mode="json")
        if snapshot.schema_version in {
            "investigation-run-config-v3",
            "investigation-run-config-v4",
        }:
            self.validate_m3_configuration(
                configuration,
                schema_version=snapshot.schema_version,
                job_id=self.job_id_for_run(run.id),
            )
            from .frozen import validate_execution_payload
            validate_execution_payload(configuration)
        job_id = self.job_id_for_run(run.id)
        existing = self.job_store.get(job_id)
        if existing is None:
            frozen_task_config = {
                key: configuration.get(key)
                for key in (
                    "platform",
                    "crawl_mode",
                    "keyword",
                    "keyword_source",
                    "creator_url",
                    "start_page",
                    "max_notes",
                    "max_total_notes",
                    "max_comments",
                    "max_concurrency",
                    "max_items_per_minute",
                    "get_sub_comment",
                    "analyze_limit",
                    "run_crawler",
                    "source_output_id",
                    "analysis_batch_size",
                )
            }
            frozen_task_config["auto_analyze"] = bool(
                int(configuration.get("analyze_limit") or 0) > 0
            )
            for key in ("collect_comments", "collect_media"):
                if key in configuration:
                    frozen_task_config[key] = configuration[key]
            requested_config = dict(frozen_task_config)
            requested = run.confirmed_configuration.get("requested_parameters")
            if requested:
                requested_config.update(requested)
            try:
                self.job_store.create(
                    job_id=job_id,
                    owner_user_id=run.owner_principal,
                    auto_analyze=frozen_task_config["auto_analyze"],
                    requested_config=requested_config,
                    effective_config=frozen_task_config,
                    **{key: configuration[key] for key in ("collect_comments", "collect_media") if key in configuration},
                    **{
                        key: configuration.get(key)
                        for key in (
                        "platform",
                        "crawler_account_id",
                        "crawler_account_display_name",
                        "display_name",
                        "crawl_mode",
                        "keyword",
                        "keyword_source",
                        "lexicon_category",
                        "library_ids",
                        "capabilities",
                        "scoring_template",
                        "rule_snapshot",
                        "lexicon_keywords",
                        "prompt_profile_snapshot",
                        "creator_url",
                        "creator_id",
                        "start_page",
                        "max_notes",
                        "max_total_notes",
                        "max_comments",
                        "max_concurrency",
                        "max_items_per_minute",
                        "get_sub_comment",
                        "analyze_limit",
                        "run_crawler",
                        "source_output_id",
                        "analysis_batch_size",
                        )
                    },
                )
            except sqlite3.IntegrityError:
                # A prior or concurrently fenced worker may have created the
                # deterministic Job between get() and insert().
                pass
            existing = self.job_store.get(job_id)
        if existing is None:
            raise RuntimeError("failed to create M3 Job")
        try:
            self._validate_existing_job(existing, configuration)
            if snapshot.schema_version in {"investigation-run-config-v3", "investigation-run-config-v4"}:
                self._validate_job_payload(existing, configuration)
        except RuntimeError as exc:
            # A persisted Job with a different frozen identity is never executable.
            self.job_store.update(job_id, status="failed", error=str(exc))
            raise
        if not existing.get("current_audit_config_revision_id"):
            revision_payload = dict(configuration.get("audit_config_revision") or {})
            revision = self.revision_store.create_or_get(
                job_id=job_id,
                created_by="m3-worker",
                **revision_payload,
            )
            if snapshot.schema_version in {"investigation-run-config-v3", "investigation-run-config-v4"}:
                from .frozen import verified_job_configuration
                verified_job_configuration(configuration, existing, revision)
                # Never overwrite a revision another writer attached meanwhile.
                with self.job_store._lock, self.job_store._connect() as connection:
                    connection.execute(
                        "UPDATE jobs SET current_audit_config_revision_id=? WHERE id=? "
                        "AND COALESCE(current_audit_config_revision_id, '')=''",
                        (revision["id"], job_id),
                    )
            else:
                self.job_store.update(job_id, current_audit_config_revision_id=revision["id"])
        if snapshot.schema_version in {"investigation-run-config-v3", "investigation-run-config-v4"}:
            self.verify_run_job(run)
        return job_id

    @staticmethod
    def _validate_job_payload(job, configuration):
        from .frozen import same_payload, validate_execution_payload
        validate_execution_payload(configuration)
        for field, value in configuration.items():
            if field in {"audit_config_revision", "policy_id", "crawler_account_confirmed_state"}:
                continue
            if not same_payload(job.get(field), value):
                raise RuntimeError("M3 Job " + field + " differs from frozen Run")

    def verify_run_job(self, run: InvestigationRun) -> dict[str, Any]:
        from .frozen import verified_job_configuration
        snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
        configuration = snapshot.execution.model_dump(mode="json")
        if snapshot.schema_version not in {"investigation-run-config-v3", "investigation-run-config-v4"}:
            return configuration
        job_id = self.job_id_for_run(run.id)
        if run.job_id and run.job_id != job_id:
            raise ValueError("M3 Run Job binding conflict")
        job = self.job_store.get(job_id)
        if job is None:
            raise ValueError("M3 frozen Job is missing")
        revision = self.revision_store.get(str(job.get("current_audit_config_revision_id") or ""))
        return verified_job_configuration(configuration, job, revision)

    def validate_execution_configuration(
        self, configuration: dict[str, Any], *, schema_version: str, job_id: str = ""
    ) -> None:
        """Re-check the authoritative account immediately before execution."""
        if schema_version in {
            "investigation-run-config-v3",
            "investigation-run-config-v4",
        }:
            self.validate_m3_configuration(
                configuration,
                schema_version=schema_version,
                job_id=job_id,
            )

    def invalidate_job_for_account(self, job_id: str, message: str) -> None:
        """Make a previously-created Job non-executable after account invalidation."""
        if self.job_store.get(job_id) is not None:
            self.job_store.update(job_id, status="failed", error=message)

    def run_pipeline(self, job_id: str, configuration: dict[str, Any]) -> None:
        request = SimpleNamespace(**configuration)
        pipeline = self.pipeline_factory(job_id=job_id)
        if configuration.get("_authoritative_m3_contract") is True:
            store = getattr(self, "creation_store", None)
            if store is None:
                from .store import InvestigationCreationStore
                store = InvestigationCreationStore()
            run = store.get_run_for_job(job_id)
            if run is None:
                raise ValueError("M3 Job has no authoritative Run")
            # The pipeline invokes this at consumption, and receives a detached copy
            # of the exact Job/revision records validated against the frozen Run.
            pipeline._m3_snapshot_validator = lambda: self.verify_run_job(run)
        pipeline.run(request)

    def get_job_state(self, job_id: str) -> dict[str, Any] | None:
        job = self.job_store.get(job_id)
        if job is None:
            return None
        return {
            "status": str(job.get("status") or ""),
            "error": str(job.get("error") or ""),
            "control": dict(job.get("control") or {}),
            "task_stats": self.ingestion_store.stats_for_task(job_id),
            "audit_results": _public_audit_results(self.audit_result_store, job_id),
        }

    def validate_selected_content_payloads(self, job_id: str) -> None:
        self.ingestion_store.validated_selection_for_task(job_id)

    @staticmethod
    def job_id_for_run(run_id: str) -> str:
        return f"m3-{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _execution_configuration(run: InvestigationRun) -> dict[str, Any]:
        snapshot = parse_confirmed_configuration_snapshot(
            run.confirmed_configuration
        )
        return snapshot.execution.model_dump(mode="json")

    @staticmethod
    def _validate_existing_job(
        job: dict[str, Any], configuration: dict[str, Any]
    ) -> None:
        for key in (
            "platform",
            "crawler_account_id",
            "crawler_account_display_name",
            "crawl_mode",
            "keyword",
            "creator_url",
            "max_notes",
            "max_total_notes",
            "analyze_limit",
        ):
            if key not in configuration:
                continue
            if str(job.get(key) or "") != str(configuration.get(key) or ""):
                raise RuntimeError(f"stable Job {key} does not match confirmed Run")

    def validate_m3_configuration(
        self,
        configuration: dict[str, Any],
        *,
        schema_version: str = "",
        job_id: str = "",
    ) -> None:
        max_notes_limit = (
            min(5, settings.m3_posts_per_keyword)
            if schema_version == "investigation-run-config-v3"
            else 5
        )
        if not 1 <= int(configuration.get("max_notes") or 0) <= max_notes_limit:
            raise ValueError("M3 execution exceeds this backend's per-keyword limit")
        analyze_limit = int(configuration.get("analyze_limit") or 0)
        max_total_notes = int(
            configuration.get("max_total_notes") or analyze_limit or 0
        )
        # v3 snapshots are immutable historical contracts. A deployment may
        # lower its current default later, but must still be able to resume the
        # exact limit that was confirmed and frozen into an existing v3 run.
        valid_analysis_limit = (
            analyze_limit >= 1
            if schema_version == "investigation-run-config-v3"
            else (
                1 <= max_total_notes <= 5
                and analyze_limit == max_total_notes
            )
        )
        if not valid_analysis_limit:
            raise ValueError("M3 execution exceeds this backend's analysis limit")
        if not 1 <= int(configuration.get("max_concurrency") or 0) <= max(1, settings.crawler_max_concurrency):
            raise ValueError("M3 execution exceeds the concurrency limit")
        account_id = str(configuration.get("crawler_account_id") or "").strip()
        if job_id:
            job = self.job_store.get(job_id) or {}
            runtime_account = (job.get("control") or {}).get("execution_account") or {}
            account_id = str(runtime_account.get("id") or account_id).strip()
        if not account_id:
            raise CrawlerAccountAuthenticationRequiredError(
                "抖音采集服务当前不可用，请稍后重试。"
            )
        # The frozen account is provenance, not an execution capability flag.
        # M3 and ordinary Jobs both enter the same current eligible pool.
        if not self.crawler_account_store.available_accounts(
            str(configuration.get("platform") or "")
        ):
            raise CrawlerAccountAuthenticationRequiredError(
                "抖音采集服务当前不可用，请稍后重试。"
            )
        try:
            self._provider_validator(configuration)
        except Exception as exc:
            raise AuthoritativeAuditProviderUnavailableError(
                "审核服务当前不可用"
            ) from exc


class InvestigationRunProjector:
    def __init__(
        self,
        *,
        job_store: JobStore | None = None,
        ingestion_store: IngestionStore | None = None,
        audit_result_store: AuditResultStore | None = None,
        report_store: ReportStore | None = None,
        crawler_account_store: CrawlerAccountStore | None = None,
    ) -> None:
        self.job_store = job_store or JobStore()
        self.ingestion_store = ingestion_store or IngestionStore()
        self.crawler_account_store = crawler_account_store or CrawlerAccountStore(
            self.job_store.db_path
        )
        self.audit_result_store = audit_result_store or AuditResultStore(
            self.ingestion_store.db_path
        )
        self.report_store = report_store or ReportStore()

    def project(self, run: InvestigationRun) -> dict[str, Any]:
        task_stats: dict[str, Any] = {}
        audit_results: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        actions: dict[str, bool] = {}
        crawl_status = "pending"
        analysis_status = "pending"
        if run.job_id:
            job = self.job_store.get(run.job_id)
            if job is None:
                crawl_status = "unknown"
                analysis_status = "unknown"
            else:
                task_stats = self.ingestion_store.stats_for_task(run.job_id)
                audit_results = _public_audit_results(
                    self.audit_result_store, run.job_id
                )
                crawl_status, analysis_status = self._job_projection(job, task_stats)
                logs = _public_job_logs(job.get("logs") or [])
                actions = available_job_actions(
                    {**job, "crawl_status": crawl_status, "analysis_status": analysis_status},
                    task_stats,
                )
                preferred_account_id = str(
                    (((job.get("control") or {}).get("execution_account") or {}).get("id"))
                    or job.get("crawler_account_id")
                    or ""
                ).strip()
                if (
                    actions.get("resume_crawl")
                    and preferred_account_id
                    and not self.crawler_account_store.available_accounts(
                        str(job.get("platform") or "")
                    )
                ):
                    actions["resume_crawl"] = False
        report_status = self._report_status(run)
        if run.status == RunStatus.AUDIT_COMPLETED and int(task_stats.get("failed_analysis_count") or 0) > 0:
            report_status = "blocked_by_failed_posts"
        return {
            "crawl_status": crawl_status,
            "analysis_status": analysis_status,
            "task_stats": task_stats,
            "audit_results": audit_results,
            "logs": logs,
            "available_actions": actions,
            "report_status": report_status,
        }

    def _report_status(self, run: InvestigationRun) -> str:
        if run.report_version_id:
            version = self.report_store.get_version(run.report_version_id)
            if version and version.get("status") == "published":
                return "published"
        return {
            RunStatus.REPORT_GENERATING: "generating",
            RunStatus.PUBLISHED: "published",
            RunStatus.FAILED: "failed",
            RunStatus.INTERRUPTED: "interrupted",
        }.get(run.status, "pending")

    @staticmethod
    def _job_projection(
        job: dict[str, Any], stats: dict[str, Any]
    ) -> tuple[str, str]:
        status = str(job.get("status") or "")
        control = dict(job.get("control") or {})
        persisted_crawl_status = str(job.get("crawl_status") or "")
        persisted_analysis_status = str(job.get("analysis_status") or "")
        if not job.get("run_crawler"):
            crawl_status = "skipped"
        elif persisted_crawl_status and persisted_crawl_status != "pending":
            crawl_status = persisted_crawl_status
        elif status in {"completed", "analysis_paused", "analysis_stopped"}:
            crawl_status = "completed"
        elif status in {"interrupted", "crawl_paused", "stopped"}:
            crawl_status = "stopped"
        elif status in {"queued", "running", "crawl_pausing", "analysis_stopping"}:
            crawl_status = "running"
        elif status == "failed":
            crawl_status = "failed"
        else:
            crawl_status = "unknown"

        if persisted_analysis_status and persisted_analysis_status != "pending":
            analysis_status = persisted_analysis_status
        elif status == "failed":
            analysis_status = "failed"
        elif control.get("analysis_stop_requested") or status == "analysis_stopped":
            analysis_status = "stopped"
        elif control.get("analysis_paused") or status == "analysis_paused":
            analysis_status = "paused"
        elif status in {"running", "analysis_running", "crawl_pausing"}:
            analysis_status = "running"
        elif int(stats.get("analyzing_count") or 0) > 0:
            analysis_status = "running"
        elif status == "completed" and int(stats.get("failed_analysis_count") or 0) > 0:
            analysis_status = "partial"
        elif int(stats.get("pending_analysis_count") or 0) > 0:
            analysis_status = "pending"
        elif status == "completed":
            analysis_status = "completed"
        elif status in {"stopped", "analysis_stopped"}:
            analysis_status = "stopped"
        else:
            analysis_status = "idle"
        return crawl_status, analysis_status


def _public_audit_results(
    store: AuditResultStore, job_id: str
) -> list[dict[str, Any]]:
    page = store.list_results(job_id=job_id, limit=1_000, sort="id")
    return [_public_audit_result(item) for item in page.get("items") or []]


def _public_job_logs(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for raw_log in values[-200:]:
        item = dict(raw_log)
        message = str(item.get("message") or "")
        if "crawler_account_verification_required" in message:
            item.update(stage="account", message="采集账号需要平台验证，已进入冷却；登录态未判定失效。")
        elif "crawler_account_login_required" in message:
            item.update(stage="account", message="采集账号登录态失效或不可用，请检查登录状态。")
        elif "crawler_rate_limited" in message:
            item.update(stage="account", message="采集受到平台限流，请等待冷却结束后再继续。")
        elif any(marker in message for marker in ("执行账号", "采集账号")):
            item.update(
                stage="account",
                message="采集账号校验未通过" if item.get("level") == "error" else "已完成采集账号可用性校验",
            )
        elif "MediaCrawler command:" in message:
            item.update(
                stage="crawl",
                message="采集执行器命令已完成",
            )
        else:
            item["message"] = re.sub(
                r"/(?:Users|private|tmp|var)/[^\s，,;]+",
                "[本地路径]",
                message,
            )
        item["reason"] = re.sub(
            r"/(?:Users|private|tmp|var)/[^\s，,;]+",
            "[本地路径]",
            str(item.get("reason") or ""),
        )
        logs.append(item)
    return logs


def _public_audit_result(item: dict[str, Any]) -> dict[str, Any]:
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    author_display_name = next(
        (
            str(author.get(key) or "").strip()
            for key in (
                "nickname",
                "user_unique_id",
                "short_user_id",
                "user_id",
                "sec_uid",
            )
            if str(author.get(key) or "").strip()
        ),
        "未知作者",
    )
    evidence = [
        projected
        for raw in item.get("evidence_items") or []
        if isinstance(raw, dict)
        for projected in [_public_audit_evidence(raw)]
        if projected is not None
    ]
    return {
        "audit_result_id": str(item.get("audit_result_id") or item.get("id") or ""),
        "content_key": str(item.get("content_key") or ""),
        "platform": str(item.get("platform") or ""),
        "content_title": str(
            item.get("content_title") or item.get("title") or "未命名内容"
        ),
        "author_display_name": author_display_name,
        "decision": str(item.get("decision") or ""),
        "risk_level": str(item.get("risk_level") or ""),
        "summary": str(item.get("summary") or ""),
        "analyzed_at": str(item.get("analyzed_at") or ""),
        "evidence": evidence,
    }


def _public_audit_evidence(item: dict[str, Any]) -> dict[str, str] | None:
    evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
    content = next(
        (
            str(item.get(key) or "").strip()
            for key in (
                "original_text",
                "source_text_dolphin",
                "ocr_text",
                "text",
                "content",
                "visual_summary",
            )
            if str(item.get(key) or "").strip()
        ),
        "",
    )
    if not evidence_id or not content:
        return None
    return {
        "evidence_id": evidence_id,
        "evidence_type": str(
            item.get("primary_modality")
            or item.get("evidence_type")
            or item.get("type")
            or "text"
        ),
        "content": content,
        "translation": str(
            item.get("translated_text")
            or item.get("translation_zh")
            or item.get("text_zh")
            or ""
        ),
        "explanation": str(
            item.get("summary")
            or item.get("hit_explanation")
            or item.get("reason")
            or item.get("risk_basis")
            or ""
        ),
    }


class R31ReportAdapter:
    def __init__(
        self,
        *,
        store: ReportStore | None = None,
        runtime: R31ReportRuntime | None = None,
    ) -> None:
        self.store = store or ReportStore()
        self.runtime = runtime or R31ReportRuntime(self.store)

    def find_published(
        self, task_id: str, *, r31_run_id: str = ""
    ) -> str | None:
        if r31_run_id:
            generation = self.store.get_run(r31_run_id)
            if generation is None or str(generation.get("task_id") or "") != task_id:
                raise RuntimeError("R3.1 generation binding is not scoped to the Run Job")
            report_version_id = str(generation.get("report_version_id") or "")
            version = self.store.get_version(report_version_id)
            if version is not None and version.get("status") == "published":
                self.verify_published(report_version_id, task_id=task_id)
                return report_version_id
            return None
        published = self.store.list_published_versions_for_task(task_id)
        if not published:
            return None
        report_version_id = str(published[0]["id"])
        self.verify_published(report_version_id, task_id=task_id)
        return report_version_id

    def generate(
        self,
        task_id: str,
        *,
        on_generation_started: Callable[[str, str], None],
    ) -> str:
        if self.store.get_latest_run_for_task(task_id) is not None:
            raise RuntimeError(
                "an unbound R3.1 generation already exists; automatic resume is fenced"
            )

        def bind(generation: dict[str, Any]) -> None:
            on_generation_started(
                str(generation["run_id"]),
                str(generation["report_version_id"]),
            )

        result = self.runtime.generate(task_id, on_generation_created=bind)
        report_version_id = str(result.report_version_id)
        self.verify_published(report_version_id, task_id=task_id)
        return report_version_id

    def verify_published(self, report_version_id: str, *, task_id: str) -> None:
        version = self.store.get_version(report_version_id)
        if version is None or version.get("status") != "published":
            raise RuntimeError("R3.1 did not publish a ReportVersion")
        report = self.store.get_report(str(version.get("report_id") or ""))
        if report is None or str(report.get("task_id") or "") != task_id:
            raise RuntimeError("published ReportVersion is not scoped to the Run Job")


class ProductSessionAdapter:
    def __init__(self, service: HermesInvestigationAgentService) -> None:
        self.service = service

    def ensure_session(self, run_id: str, report_version_id: str) -> str:
        session = self.service.create_session(
            report_version_id,
            anchor_key=f"m3-run:{run_id}",
        )
        if str(session.report_version_id) != report_version_id:
            raise RuntimeError("Product Session anchor does not match ReportVersion")
        return str(session.id)
