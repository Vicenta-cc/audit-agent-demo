"""Pure fixture adapters for province/city scopes; no draft or runtime access.

Administrative identity is separate from execution granularity: municipalities
retain their province-level codes while executing as city targets. P1-shaped
fixtures do not establish authorization to read or execute any production draft.
"""
from __future__ import annotations

import copy

from . import relevance as shared
from .relevance_ip import validate_catalog

SCHEMA_VERSION = "post-only-v2"
CITY_KINDS = {"地级市", "prefecture_city"}
MUNICIPALITY_KINDS = {"直辖市", "municipality"}


def _names(record):
    return {record["name"], *record["aliases"]}


def _region(value, record):
    if (not isinstance(value, dict) or not isinstance(value.get("name"), str)
            or value["name"].strip() not in _names(record)):
        raise shared.RelevanceInputError("SCOPE_REGION_NAME_MISMATCH")
    aliases = value.get("aliases", [])
    if (not isinstance(aliases, list)
            or any(not isinstance(a, str) or a.strip() not in _names(record) for a in aliases)):
        raise shared.RelevanceInputError("UNTRUSTED_SCOPE_ALIAS")
    if "code" in value and value["code"] != record["code"]:
        raise shared.RelevanceInputError("SCOPE_REGION_CODE_MISMATCH")
    return {"name": record["name"], "aliases": sorted(record["aliases"])}


def _supported(record):
    return record["level"] == "province" or (
        record["level"] == "prefecture" and record["kind"] in CITY_KINDS)


def _execution_level(record):
    return "city" if record["level"] == "prefecture" or record["kind"] in MUNICIPALITY_KINDS else "province"


def _binding(record, catalog):
    return {"level": record["level"], "kind": record["kind"],
            "target_code": record["code"], "province_code": record["province_code"],
            "catalog_version": catalog["version"], "catalog_digest": shared.digest(catalog)}


def ip_context(context: dict) -> dict:
    """Return a separate legacy utility container, never the execution context."""
    if not isinstance(context, dict) or context.get("schema_version") != SCHEMA_VERSION:
        raise shared.RelevanceInputError("INVALID_POST_ONLY_CONTEXT_VERSION")
    if context.get("scope_origin") != "confirmed_run":
        raise shared.RelevanceInputError("SCOPE_MUST_BE_CONFIRMED_RUN")
    policy = context.get("policy")
    if not isinstance(policy, dict):
        raise shared.RelevanceInputError("MISSING_IP_POLICY")
    scope = copy.deepcopy(context.get("scope"))
    if not isinstance(scope, dict) or scope.get("topic") is not None:
        raise shared.RelevanceInputError("POST_ONLY_TOPIC_NOT_SUPPORTED")
    # IP only concerns the target province. An independent province container
    # avoids legacy name-overlap rules (吉林市/吉林省 and municipalities) without
    # changing the caller's actual execution scope or administrative binding.
    scope["target"] = copy.deepcopy(scope.get("province"))
    return shared.normalize_context({"schema_version": shared.SCHEMA_VERSION,
        "scope_origin": "confirmed_run", "target_level": "province", "scope": scope,
        "model": context.get("model"), "invocation_mode": context.get("invocation_mode"),
        "policy": {"version": SCHEMA_VERSION, "prompt_version": SCHEMA_VERSION,
                   "comment_char_budget": 1, "max_comment_links": 1,
                   "truncation_strategy": "whole_or_head_tail", "ip": copy.deepcopy(policy.get("ip"))}})


def normalize_context(context: dict, *, admin_catalog: dict) -> dict:
    """Resolve explicit province/city level against a caller-supplied snapshot."""
    if not isinstance(context, dict) or context.get("schema_version") != SCHEMA_VERSION:
        raise shared.RelevanceInputError("INVALID_POST_ONLY_CONTEXT_VERSION")
    if context.get("scope_origin") != "confirmed_run":
        raise shared.RelevanceInputError("SCOPE_MUST_BE_CONFIRMED_RUN")
    if not isinstance(context.get("target_level"), str) or context["target_level"] not in {"province", "city"}:
        raise shared.RelevanceInputError("UNRESOLVED_TARGET_LEVEL")
    scope = context.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("target"), dict):
        raise shared.RelevanceInputError("MISSING_REGION")
    if "schema_version" in scope and scope["schema_version"] != "geo-scope-v1":
        raise shared.RelevanceInputError("INVALID_SCOPE_VERSION")
    name = scope["target"].get("name")
    if not isinstance(name, str):
        raise shared.RelevanceInputError("MISSING_REGION")
    indexed = validate_catalog(admin_catalog)
    matches = [row for row in indexed.values() if _supported(row) and name.strip() in _names(row)
               and ((context["target_level"] == "province" and row["level"] == "province")
                    or (context["target_level"] == "city" and _execution_level(row) == "city"))]
    if len(matches) != 1:
        raise shared.RelevanceInputError("UNRESOLVED_SCOPE_TARGET")
    record = matches[0]
    province = indexed[record["province_code"]]
    target_spec, province_spec = _region(scope["target"], record), _region(scope.get("province"), province)
    administrative = _binding(record, admin_catalog)
    if "administrative_target" in context and context["administrative_target"] != administrative:
        raise shared.RelevanceInputError("ADMINISTRATIVE_TARGET_BINDING_MISMATCH")
    normalized = {"schema_version": SCHEMA_VERSION, "scope_origin": "confirmed_run",
        "target_level": _execution_level(record), "administrative_target": administrative,
        "scope": {"target": target_spec, "province": province_spec, "topic": scope.get("topic"),
                  "content_unit": scope.get("content_unit"), "selection_policy": scope.get("selection_policy")},
        "policy": copy.deepcopy(context.get("policy")), "model": context.get("model"),
        "invocation_mode": context.get("invocation_mode")}
    # The shared validator enforces existing IP thresholds and scope policy only.
    # It receives an independent municipality bridge and cannot alter outer level.
    common = ip_context(normalized)
    normalized.update(model=common["model"], invocation_mode=common["invocation_mode"],
                      policy={"version": SCHEMA_VERSION, "ip": common["policy"]["ip"]})
    scope_digest = shared.digest({key: normalized[key] for key in
                                  ("scope_origin", "target_level", "scope", "administrative_target")})
    if context.get("scope_digest") is not None and context["scope_digest"] != scope_digest:
        raise shared.RelevanceInputError("DIGEST_MISMATCH:scope_digest")
    return normalized


def context_from_scope_state(state: dict, template: dict, *, admin_catalog: dict) -> dict:
    """Validate a resolved P1-shaped fixture; never load or execute a real draft."""
    if not isinstance(state, dict) or state.get("status") != "resolved":
        raise shared.RelevanceInputError("SCOPE_STATE_NOT_RESOLVED")
    indexed = validate_catalog(admin_catalog)
    code = state.get("target_code")
    record = indexed.get(code) if isinstance(code, str) else None
    if record is None or not _supported(record):
        raise shared.RelevanceInputError("UNSUPPORTED_SCOPE_STATE_TARGET")
    if (state.get("catalog_version") != admin_catalog["version"]
            or state.get("target_level") != record["level"]
            or state.get("province_code") != record["province_code"]
            or ("catalog_digest" in state and state["catalog_digest"] != shared.digest(admin_catalog))):
        raise shared.RelevanceInputError("SCOPE_STATE_BINDING_MISMATCH")
    province = indexed[record["province_code"]]
    # Resolved states carry canonical identity; aliases are verified separately.
    if (not isinstance(state.get("target"), dict) or state["target"].get("name") != record["name"]
            or not isinstance(state.get("province"), dict) or state["province"].get("name") != province["name"]):
        raise shared.RelevanceInputError("SCOPE_STATE_NAME_MISMATCH")
    if not isinstance(template, dict):
        raise shared.RelevanceInputError("INVALID_POST_ONLY_CONTEXT_VERSION")
    context = copy.deepcopy(template)
    for key in ("administrative_target", "scope_digest"):
        context.pop(key, None)
    context.update(target_level=_execution_level(record),
        scope={"target": copy.deepcopy(state["target"]), "province": copy.deepcopy(state["province"]),
               "topic": None, "selection_policy": state.get("selection_policy"), "content_unit": "parent_post_event"},
        administrative_target=_binding(record, admin_catalog))
    return normalize_context(context, admin_catalog=admin_catalog)
