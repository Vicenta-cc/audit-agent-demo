from __future__ import annotations

import sqlite3
from typing import Protocol

from .compiler import (
    COMPILER_VERSION,
    SYSTEM_TEMPLATE_VERSION,
    compile_ruleset_revision,
)
from .contracts import RuleSetContent
from .errors import RuleSetNotFoundError, RuleSetValidationError
from .store import RuleSetStore


class Principal(Protocol):
    id: str


class RuleSetService:
    def __init__(self, store: RuleSetStore) -> None:
        self.store = store

    def create_draft(
        self,
        content: RuleSetContent,
        *,
        principal: Principal,
        ruleset_id: str = "",
    ) -> dict:
        self._validate_pilot_scope(content)
        self._validate_operator_content(content)
        return self.store.create_draft(
            content=content,
            actor_id=principal.id,
            ruleset_id=ruleset_id,
        )

    def get(self, ruleset_id: str, *, principal: Principal) -> dict:
        return self.store.get(ruleset_id, actor_id=principal.id)

    def list(self, *, principal: Principal) -> list[dict]:
        return self.store.list(actor_id=principal.id)

    def update_draft(
        self,
        ruleset_id: str,
        *,
        expected_revision: int,
        content: RuleSetContent,
        principal: Principal,
    ) -> dict:
        self._validate_pilot_scope(content)
        self._validate_operator_content(content)
        return self.store.update_draft(
            ruleset_id,
            expected_revision=expected_revision,
            content=content,
            actor_id=principal.id,
        )

    def publish(
        self,
        ruleset_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        principal: Principal,
    ) -> dict:
        key = str(idempotency_key or "").strip()
        if not key:
            raise RuleSetValidationError("Idempotency-Key is required")
        try:
            aggregate = self.store.get(ruleset_id, actor_id=principal.id)
        except RuleSetNotFoundError:
            # Preserve the Store's existing not-found versus forbidden contract.
            return self.store.publish(
                ruleset_id,
                expected_revision=expected_revision,
                idempotency_key=key,
                actor_id=principal.id,
            )
        if int(aggregate["draft_revision"]) == int(expected_revision):
            draft = aggregate["draft"]
            compile_ruleset_revision(
                {
                    **draft,
                    "id": f"ruleset-revision:{ruleset_id}:validation",
                    "ruleset_id": ruleset_id,
                    "status": "published",
                },
                audit_policy={
                    "ruleset_revision_id": f"ruleset-revision:{ruleset_id}:validation"
                },
            )
        return self.store.publish(
            ruleset_id,
            expected_revision=expected_revision,
            idempotency_key=key,
            actor_id=principal.id,
        )

    def list_published(
        self,
        *,
        principal: Principal,
        ruleset_id: str = "",
    ) -> list[dict]:
        del principal
        return self.store.list_published(ruleset_id=ruleset_id)

    def list_current_published(
        self,
        *,
        principal: Principal,
        ruleset_id: str = "",
    ) -> list[dict]:
        del principal
        return self.store.list_current_published(ruleset_id=ruleset_id)

    def get_published(
        self,
        revision_id: str,
        *,
        principal: Principal,
        connection: sqlite3.Connection | None = None,
    ) -> dict:
        del principal
        return self.store.get_published(revision_id, connection=connection)

    def get_current_published(
        self,
        revision_id: str,
        *,
        principal: Principal,
        connection: sqlite3.Connection | None = None,
    ) -> dict | None:
        del principal
        return self.store.get_current_published(
            revision_id, connection=connection
        )

    def compile_preview(
        self,
        revision_id: str,
        *,
        audit_policy: dict | None = None,
        principal: Principal,
        system_template_version: str = SYSTEM_TEMPLATE_VERSION,
        compiler_version: str = COMPILER_VERSION,
    ) -> dict:
        compiled = self.compile_for_execution(
            revision_id,
            audit_policy=audit_policy,
            principal=principal,
            system_template_version=system_template_version,
            compiler_version=compiler_version,
        )
        rule_snapshot = compiled["rule_snapshot"]
        prompt_snapshot = compiled["prompt_profile_snapshot"]
        routes = rule_snapshot["stage_routes"]
        return {
            "ruleset_ref": rule_snapshot["ruleset_ref"],
            "system_template_version": prompt_snapshot["system_template_version"],
            "compiler_version": prompt_snapshot["compiler_version"],
            "stages": {
                stage: {"rule_ids": list(rule_ids), "rule_count": len(rule_ids)}
                for stage, rule_ids in routes.items()
            },
            "fixed_prompt_chars": dict(prompt_snapshot.get("fixed_prompt_chars") or {}),
            "config_hash": compiled["config_hash"],
            "diagnostics": {"valid": True, "errors": []},
        }

    def compile_for_execution(
        self,
        revision_id: str,
        *,
        audit_policy: dict | None = None,
        principal: Principal,
        connection: sqlite3.Connection | None = None,
        system_template_version: str = SYSTEM_TEMPLATE_VERSION,
        compiler_version: str = COMPILER_VERSION,
    ) -> dict:
        revision = self.get_published(
            revision_id, principal=principal, connection=connection
        )
        return compile_ruleset_revision(
            revision,
            audit_policy=audit_policy,
            system_template_version=system_template_version,
            compiler_version=compiler_version,
        )

    def fork_published(
        self,
        revision_id: str,
        *,
        principal: Principal,
        ruleset_id: str = "",
    ) -> dict:
        revision = self.get_published(revision_id, principal=principal)
        content = RuleSetContent.model_validate(self._without_legacy_sources(revision))
        return self.store.create_draft(
            content=content,
            actor_id=principal.id,
            ruleset_id=ruleset_id,
        )

    @staticmethod
    def _validate_pilot_scope(content: RuleSetContent) -> None:
        if content.domain != "gambling":
            raise RuleSetValidationError(
                "RuleSet Foundation pilot only accepts domain='gambling'"
            )

    @staticmethod
    def _validate_operator_content(content: RuleSetContent) -> None:
        mappings = list(content.general_exemptions)
        if any(item.source_mappings for item in mappings):
            raise RuleSetValidationError("operator-authored RuleSets cannot declare legacy source mappings")
        for category in content.categories:
            for rule in category.rules:
                if rule.source_mappings or any(
                    item.source_mappings for item in rule.rule_exemptions
                ):
                    raise RuleSetValidationError(
                        "operator-authored RuleSets cannot declare legacy source mappings"
                    )

    @staticmethod
    def _without_legacy_sources(revision: dict) -> dict:
        content = {
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
        for exemption in content.get("general_exemptions") or []:
            exemption["source_mappings"] = []
        for category in content.get("categories") or []:
            for rule in category.get("rules") or []:
                rule["source_mappings"] = []
                for exemption in rule.get("rule_exemptions") or []:
                    exemption["source_mappings"] = []
        return content
