"""Fixture-only, read-only risk input adapter for the unchanged P1 triage engine."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path

from backend.audit_agent import triage
from backend.audit_agent.triage_matcher import TriageTerm

SCHEMA = "geo-frozen-risk-v1"
TERM_FIELDS = set(TriageTerm.__dataclass_fields__)


def _json(value):
    # JSON normalization must not silently turn non-string map keys into strings.
    def check_keys(item):
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("risk JSON object keys must be strings")
            for nested in item.values():
                check_keys(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check_keys(nested)
    check_keys(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("risk inputs must be finite JSON") from exc


def _copy(value):
    return json.loads(_json(value))


def engine_fingerprint():
    """Bind prompt, matcher, ranking and fixed score constants to their actual code."""
    base = Path(triage.__file__).parent
    return hashlib.sha256(b"\0".join((base / name).read_bytes() for name in ("triage.py", "triage_matcher.py"))).hexdigest()


def _keys(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"invalid {label} fields")


def _integer(value, minimum, label):
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid {label}")


def _validate(core):
    _keys(core, {"schema_version", "category_ids", "terms", "search_groups", "rules", "model", "prompt", "scoring", "identity"}, "risk")
    if core["schema_version"] != SCHEMA:
        raise ValueError("unknown risk schema")
    ids = core["category_ids"]
    if type(ids) is not list or any(type(v) is not str or not v.strip() or v != v.strip() for v in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid category_ids")
    if type(core["terms"]) is not list:
        raise ValueError("invalid terms")
    for row in core["terms"]:
        _keys(row, TERM_FIELDS, "term")
        if any(type(v) is not str for v in row.values()) or not row["keyword"].strip() or row["keyword"] != row["keyword"].strip():
            raise ValueError("invalid term value")
        if row["category_id"] not in ids:
            raise ValueError("term category outside frozen categories")
    groups = core["search_groups"]
    if type(groups) is not dict or any(type(k) is not str or not k.strip() or type(v) is not str or not v.strip() for k, v in groups.items()):
        raise ValueError("invalid search_groups")
    if not ids and (core["terms"] or groups):
        raise ValueError("empty category context must have no terms or groups")
    if type(core["rules"]) is not dict:
        raise ValueError("invalid rules")
    model = core["model"]
    _keys(model, {"name", "max_comments", "request_timeout"}, "model")
    if type(model["name"]) is not str or not model["name"].strip():
        raise ValueError("invalid model name")
    _integer(model["max_comments"], 0, "max_comments")
    _integer(model["request_timeout"], 1, "request_timeout")
    prompt = core["prompt"]
    _keys(prompt, {"version", "engine_sha256"}, "prompt")
    if type(prompt["version"]) is not str or not prompt["version"].strip() or prompt["engine_sha256"] != engine_fingerprint():
        raise ValueError("incompatible risk engine/prompt")
    scoring = core["scoring"]
    _keys(scoring, {"rule_score_high", "rule_score_medium", "rule_score_low", "rule_score_cap", "model_scores", "prompt_hit_limit", "discard_score"}, "scoring")
    for key in ("rule_score_high", "rule_score_medium", "rule_score_low", "rule_score_cap"):
        _integer(scoring[key], 0, key)
    _keys(scoring["model_scores"], triage.MODEL_SCORES, "model_scores")
    for key, val in scoring["model_scores"].items():
        _integer(val, 0, key)
    if scoring["model_scores"] != triage.MODEL_SCORES or type(scoring["prompt_hit_limit"]) is not int or scoring["prompt_hit_limit"] != triage.PROMPT_HIT_LIMIT or type(scoring["discard_score"]) is not int or scoring["discard_score"] != triage.DISCARD_SCORE:
        raise ValueError("incompatible fixed scoring parameters")
    identity = core["identity"]
    _keys(identity, {"max_followers_personal_verified", "max_followers_unverified", "official_verify_patterns"}, "identity")
    for key in ("max_followers_personal_verified", "max_followers_unverified"):
        _integer(identity[key], 0, key)
    patterns = identity["official_verify_patterns"]
    if type(patterns) is not list or any(type(v) is not str for v in patterns):
        raise ValueError("invalid official verification patterns")
    try:
        re.compile("|".join(patterns))
    except Exception as exc:
        raise ValueError("invalid official verification regex") from exc


def freeze_risk_inputs(*, category_ids, terms, search_groups, rules, model, prompt, scoring, identity):
    core = dict(schema_version=SCHEMA, category_ids=category_ids,
        terms=[asdict(term) if isinstance(term, TriageTerm) else term for term in terms],
        search_groups=search_groups, rules=rules, model=model, prompt=prompt, scoring=scoring, identity=identity)
    _validate(core)
    core = _copy(core)
    return {**core, "digest": hashlib.sha256(_json(core).encode()).hexdigest()}


def validate_risk_inputs(snapshot):
    if type(snapshot) is not dict or "digest" not in snapshot:
        raise ValueError("missing risk digest")
    value = dict(snapshot)
    digest = value.pop("digest")
    _validate(value)
    value = _copy(value)
    if digest != hashlib.sha256(_json(value).encode()).hexdigest():
        raise ValueError("risk digest mismatch")
    return {**value, "digest": digest}


class FrozenLexiconStore:
    """Supply rows only through terms_for(), preserving its list/group association."""
    def __init__(self, snapshot):
        self.__snapshot = validate_risk_inputs(snapshot)

    def _check(self, category_ids):
        if sorted(category_ids) != sorted(self.__snapshot["category_ids"]):
            raise ValueError("category context differs from frozen inputs")

    def triage_terms(self, category_ids):
        self._check(category_ids)
        return _copy(self.__snapshot["terms"])

    def search_word_groups(self, category_ids):
        self._check(category_ids)
        return _copy(self.__snapshot["search_groups"])


def build_frozen_engine(snapshot, manual_client):
    """Caller supplies the client; offline callers must supply a manual-response client."""
    frozen = validate_risk_inputs(snapshot)
    model, identity, scoring = (frozen[key] for key in ("model", "identity", "scoring"))
    return triage.TriageEngine(FrozenLexiconStore(frozen), manual_client, model=model["name"],
        max_comments=model["max_comments"], request_timeout=model["request_timeout"],
        **identity, **{key: scoring[key] for key in ("rule_score_high", "rule_score_medium", "rule_score_low", "rule_score_cap")})
