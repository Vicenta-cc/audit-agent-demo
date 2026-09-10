"""Versioned, repository-owned trial profiles bound to a reserved RuleSet ID.

Normal revisions and inline authoring retain the standard compiler. A changed
trial rule book fails closed instead of silently using mismatched instructions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .errors import RuleSetValidationError

K_RULESET_ID = "ruleset.ethnic-content-review.k-trial.v1"
BUNDLE_DIR = Path(__file__).parent / "bundles" / "ethnic_k_v1"
K2_RULESET_ID = "ruleset.ethnic-content-review.k-trial.v2"
TRIAL_BUNDLES = {K_RULESET_ID: BUNDLE_DIR, K2_RULESET_ID: BUNDLE_DIR.with_name("ethnic_k_v2")}


def apply_trial_profile(revision, compiled):
    ruleset_id = revision.get("ruleset_id")
    bundle_dir = TRIAL_BUNDLES.get(ruleset_id)
    if bundle_dir is None:
        return
    from .compiler import canonical_json, content_hash, _validate_fixed_prompt_budgets

    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        if hashlib.sha256((bundle_dir / name).read_bytes()).hexdigest() != digest:
            raise RuleSetValidationError("trial profile bundle integrity mismatch")
    content = json.loads((bundle_dir / "ruleset.json").read_text())
    if revision["content_hash"] != content_hash(content):
        raise RuleSetValidationError("trial RuleSet content differs from its pinned profile")
    profile = json.loads((bundle_dir / "prompt-profile.json").read_text())
    if profile["stage_rule_ids"] != compiled.stage_routes:
        raise RuleSetValidationError("trial profile stage routing mismatch")
    if profile["comment_prompt_template"] != (bundle_dir / "comment-template.txt").read_text():
        raise RuleSetValidationError("trial comment template mismatch")
    _validate_fixed_prompt_budgets(profile)
    for key in ("system_template_version", "compiler_version"):
        profile[key] = compiled.prompt_profile_snapshot[key]
    profile["inference_settings"] = deepcopy(manifest["inference_settings"])
    profile["trial_profile"] = {
        "id": ruleset_id,
        "status": manifest["status"],
        "source_prompt_version": manifest["source_prompt_version"],
    }
    profile.pop("prompt_version", None)
    digest = hashlib.sha256(canonical_json(profile).encode()).hexdigest()[:16]
    version = "v1" if ruleset_id == K_RULESET_ID else "v2"
    profile["prompt_version"] = f"ethnic-k-trial-{version}-{digest}"
    compiled.prompt_profile_snapshot = profile
