from __future__ import annotations

from collections.abc import Iterable, Mapping


def referenced_library_ids(config: object) -> tuple[str, ...]:
    """Return the library ids referenced by one AuditPolicy config."""
    if not isinstance(config, Mapping):
        return ()
    values: list[str] = []

    def add(value: object) -> None:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)

    add(config.get("lexicon_category"))
    library_ids = config.get("library_ids")
    if isinstance(library_ids, Iterable) and not isinstance(library_ids, (str, bytes)):
        for value in library_ids:
            add(value)
    snapshot = config.get("rule_snapshot")
    rules = snapshot.get("scoring_rules") if isinstance(snapshot, Mapping) else None
    if isinstance(rules, Iterable) and not isinstance(rules, (str, bytes)):
        for rule in rules:
            if isinstance(rule, Mapping):
                add(rule.get("library_id"))
    return tuple(values)


def config_references_library(config: object, library_id: str) -> bool:
    target = str(library_id or "").strip()
    return bool(target) and target in referenced_library_ids(config)


def policy_reference_scopes(
    *,
    policy_id: object,
    policy_name: object,
    draft_config: object,
    published_config: object,
    library_id: str,
) -> dict | None:
    scopes: list[str] = []
    if config_references_library(draft_config, library_id):
        scopes.append("draft")
    if config_references_library(published_config, library_id):
        scopes.append("published")
    if not scopes:
        return None
    return {
        "policy_id": str(policy_id),
        "policy_name": str(policy_name),
        "scopes": scopes,
    }
