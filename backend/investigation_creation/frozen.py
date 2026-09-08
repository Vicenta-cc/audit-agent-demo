"""M3 execution packaging and comparison; no resource lookup or new identity."""
from copy import deepcopy

from backend.rulesets import compiler


def same_payload(left, right):
    return compiler.canonical_json(left) == compiler.canonical_json(right)


def compile_temporary(judgement):
    from .contracts import TemporaryRuleSetJudgement

    source = TemporaryRuleSetJudgement.model_validate(judgement.model_dump(mode="json"))
    compiled = compiler.compile_ruleset_content(source.content)
    provenance = source.model_dump(mode="json", exclude={"content"})
    policy = {
        "policy_id": "", "policy_name": "", "policy_version": "",
        "ruleset_revision_id": "", "policy_config": {}, "library_ids": [],
        "capabilities": ["text", "ocr", "asr", "vision", "comment"],
        "scoring_template": "balanced", "thresholds": {"high": 80, "medium": 60, "review": 40},
    }
    rules = {
        "schema_version": 2, "temporary_ruleset": provenance,
        "general_exemptions": compiled.general_exemptions,
        "decision_rules": compiled.decision_rules, "scoring_rules": [],
        "thresholds": policy["thresholds"], "stage_routes": compiled.stage_routes,
    }
    result = {"schema_version": 2, "audit_policy_snapshot": policy,
              "rule_snapshot": rules, "prompt_profile_snapshot": compiled.prompt_profile_snapshot}
    result["config_hash"] = compilation_hash(result)
    return result


def compilation_hash(compiled):
    from .contracts import confirmed_configuration_hash

    rules, prompts = compiled["rule_snapshot"], compiled["prompt_profile_snapshot"]
    source = ({"temporary_ruleset": rules["temporary_ruleset"]}
              if "temporary_ruleset" in rules else {"ruleset_revision": rules["ruleset_ref"]})
    return confirmed_configuration_hash({
        "schema_version": 2, "audit_policy": compiled["audit_policy_snapshot"], **source,
        "rule_snapshot": rules, "prompt_profile_snapshot": prompts,
        "system_template_version": prompts["system_template_version"],
        "compiler_version": prompts["compiler_version"],
    })


def validate_execution_payload(execution):
    """Check duplicated frozen values and the existing compilation hash, offline."""
    rules = execution["rule_snapshot"]
    prompts = execution["prompt_profile_snapshot"]
    revision = execution["audit_config_revision"]
    for field in ("rule_snapshot", "prompt_profile_snapshot"):
        if not same_payload(execution[field], revision[field]):
            raise ValueError("frozen execution " + field + " differs from audit config")
    # Historical non-RuleSet execution retains its own compilation contract.
    if rules.get("schema_version") != 2:
        return
    config = revision["audit_config"]
    policy = {
        "policy_id": revision["source_policy_id"], "policy_name": revision["source_policy_name"],
        "policy_version": revision["source_policy_version"],
        "ruleset_revision_id": "",
        "policy_config": {}, "library_ids": [],
        "capabilities": execution["capabilities"], "scoring_template": execution["scoring_template"],
        "thresholds": rules["thresholds"],
    }
    # M3 RuleSet-direct execution uses system defaults, without AuditPolicy selection.
    if not revision["source_policy_id"]:
        expected = compilation_hash({"audit_policy_snapshot": policy,
            "rule_snapshot": rules, "prompt_profile_snapshot": prompts})
        if revision["config_hash"] != expected:
            raise ValueError("frozen compilation config hash mismatch")
    for field, value in {
        "capabilities": execution["capabilities"], "scoring_template": execution["scoring_template"],
        "source_policy_id": revision["source_policy_id"], "source_policy_name": revision["source_policy_name"],
        "source_policy_version": revision["source_policy_version"], "library_ids": execution["library_ids"],
        "thresholds": rules["thresholds"], "scoring_rules": rules["scoring_rules"],
        "prompt_version": prompts["prompt_version"],
        "system_template_version": prompts["system_template_version"],
        "compiler_version": prompts["compiler_version"],
    }.items():
        if not same_payload(config.get(field), value):
            raise ValueError("frozen audit config " + field + " mismatch")
    source_key = "temporary_ruleset" if "temporary_ruleset" in rules else "ruleset_ref"
    if not same_payload(config.get(source_key), rules[source_key]):
        raise ValueError("frozen audit source mismatch")
    if not same_payload(rules["stage_routes"], prompts["stage_rule_ids"]):
        raise ValueError("frozen stage routing mismatch")


def validate_temporary_execution(source, execution):
    """Confirm-time compiler proof from the inline source, never a Proposal."""
    expected = compile_temporary(source)
    for field in ("rule_snapshot", "prompt_profile_snapshot"):
        if not same_payload(execution[field], expected[field]):
            raise ValueError("temporary compiled " + field + " differs from inline content")
    validate_execution_payload(execution)


def verified_job_configuration(configuration, job, revision):
    """Return a detached copy of precisely the Job/revision payload just validated."""
    validate_execution_payload(configuration)
    for field, value in configuration.items():
        if field in {"audit_config_revision", "policy_id", "crawler_account_confirmed_state"}:
            continue
        if not same_payload(job.get(field), value):
            raise ValueError("M3 Job " + field + " differs from frozen Run")
    if not revision or revision.get("job_id") != job["id"]:
        raise ValueError("M3 audit config revision does not belong to Job")
    for field, value in configuration["audit_config_revision"].items():
        if not same_payload(revision.get(field), value):
            raise ValueError("M3 audit config revision " + field + " differs from frozen Run")
    result = deepcopy(configuration)
    result.update(rule_snapshot=deepcopy(job["rule_snapshot"]),
                  prompt_profile_snapshot=deepcopy(job["prompt_profile_snapshot"]),
                  audit_config_revision={k: deepcopy(revision[k]) for k in configuration["audit_config_revision"]},
                  _verified_audit_config_revision_id=revision["id"])
    return result
