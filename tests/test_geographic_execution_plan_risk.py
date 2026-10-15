"""Offline risk inputs preserve the production engine's exact scoring context."""
import json
import os
from pathlib import Path
import tempfile
import socket
from dataclasses import asdict

import pytest

from backend.audit_agent import triage
from backend.audit_agent.triage_matcher import terms_from_rows
from experiments.geographic_execution_plan.frozen_risk import (
    FrozenLexiconStore, build_frozen_engine, freeze_risk_inputs, validate_risk_inputs,
    engine_fingerprint,
)
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


class ManualClient:
    def __init__(self):
        self.calls = []
        self.responses = []

    def audit_text(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        response = {"suspicion": "strong", "risk_score": 85, "reason": "人工固定响应", "matched_terms": []}
        self.responses.append(dict(response))
        return response


class SourceLexicon:
    def triage_terms(self, categories):
        return [dict(keyword=word, match_type="模糊", category_id="fixture", risk_level=level,
                     entry_kind=kind, entry_group=group)
                for word, level, kind, group in [("包过", "高", "main", "包过"),
                    ("保过", "高", "variant", "包过"), ("欠薪", "中", "main", "欠薪")]]

    def search_word_groups(self, categories):
        return {"克拉玛依市包过": "包过", "包过": "包过", "保过": "包过", "北京路欠薪": "欠薪"}


def snapshot():
    source = SourceLexicon()
    return freeze_risk_inputs(category_ids=["fixture"], terms=terms_from_rows(source.triage_terms([])),
        search_groups=source.search_word_groups([]), rules={"audit_goal": "固定审核规则", "evidence_rules": "人工测试"},
        model={"name": "fixture-manual", "max_comments": 2, "request_timeout": 10},
        prompt={"version": "p1-triage-031e0cd", "engine_sha256": engine_fingerprint()},
        scoring={"rule_score_high": 300, "rule_score_medium": 100, "rule_score_low": 50,
                 "rule_score_cap": 900, "model_scores": dict(triage.MODEL_SCORES),
                 "prompt_hit_limit": 5, "discard_score": -1000},
        identity={"max_followers_personal_verified": 5, "max_followers_unverified": 10,
                  "official_verify_patterns": list(triage.OFFICIAL_VERIFY_PATTERNS)})


def run(engine, rules, *, context_trace=None):
    terms = engine.terms_for(["fixture"])
    assert terms is engine.terms_for(["fixture"])
    scores = []
    # Same post under two queries retains rank/comment/search context separately.
    contexts = [("dy:same", "克拉玛依市包过", 1, [{"comment_id": "a", "content": "欠薪"}], {}),
                ("dy:same", "北京路欠薪", 7, [], {}),
                ("dy:same", "克拉玛依市包过", 9, [{"comment_id": "a", "content": "欠薪"}], {}),
                ("official", "克拉玛依市包过", 2, [], {"enterprise_verify_reason": "公安官方"}),
                ("followers", "克拉玛依市包过", 3, [], {"follower_count": 11}),
                ("personal", "克拉玛依市包过", 4, [], {"custom_verify": "认证", "follower_count": 6})]
    for key, query, rank, comments, extra in contexts:
        item = {"title": "克拉玛依市 包过 保过 欠薪", "source_keyword": query, **extra}
        if context_trace is not None:
            context_trace.append(dict(content_key=key, search_keyword=query, rank=rank,
                item=dict(item), comments=json.loads(json.dumps(comments))))
        scores.append(engine.score(key, rank, item, comments, terms, search_keyword=query, rules=rules))
    return [asdict(s) for s in scores], [asdict(s) for s in triage.rank_candidates(scores)]


def test_existing_engine_risk_unchanged_before_after_geography_and_restore(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network forbidden"))
    frozen = snapshot()
    baseline_client = ManualClient()
    baseline = triage.TriageEngine(SourceLexicon(), baseline_client, model="fixture-manual", max_comments=2,
        request_timeout=10, max_followers_personal_verified=5, max_followers_unverified=10)
    original_contexts = []
    expected = run(baseline, frozen["rules"], context_trace=original_contexts)
    assert [s["score"] for s in expected[0]] == [400, 800, 400, -1000, -1000, -1000]
    assert "命中 2 处不计分" in expected[0][0]["reason"]
    assert [h["keyword"] for h in expected[0][0]["hits"]] == ["欠薪", "欠薪"]
    assert [h["keyword"] for h in expected[0][1]["hits"]] == ["包过", "保过"]
    restored = json.loads(json.dumps(frozen, ensure_ascii=False))
    from experiments.geographic_execution_plan import p3_bridge as bridge
    from test_geographic_execution_plan_p3 import state, candidate, response
    def blocked(*args, **kwargs):
        pytest.fail("network/model entry forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    for module in (bridge.names, bridge.shared):
        monkeypatch.setattr(module, "evaluate_relevance", blocked)
    profile = bridge.freeze_p3_inputs(model="fixture-manual", model_config_source={"origin": "fixture", "reference": "risk-invariance"})
    eligibility = []
    semantic_prompts = []
    after_geography = []
    geo_candidate = candidate(title=original_contexts[0]["item"]["title"], id="same")
    geo_candidate["content_key"] = original_contexts[0]["content_key"]
    geo_candidate["comments"] = original_contexts[0]["comments"]
    for code, tier in [("650200", "local"), ("440300", "other_region")]:
        request = bridge.prepare(state(profile, code), profile, geo_candidate)
        decision = bridge.consume(request, response())
        assert decision["geo_tier"] == tier
        semantic_prompts.append(request["prompt"])
        client = ManualClient()
        restored_contexts = []
        result = run(build_frozen_engine(restored, client), restored["rules"], context_trace=restored_contexts)
        assert restored_contexts == original_contexts
        assert result == expected
        assert client.calls == baseline_client.calls
        original_risk = json.loads(json.dumps(result[0][0]))
        eligibility.append(bridge.consume_policy(decision, target_level="city", selection_policy="local_only",
            risk={**result[0][0], "valid": True, "strength": result[0][0]["band"]})["eligible"])
        assert result[0][0] == original_risk
        after_geography.append(dict(target_code=code, geo_tier=decision["geo_tier"],
            p3_candidate=geo_candidate, p3_decision=decision, fixture_eligible=eligibility[-1], contexts=restored_contexts,
            risk_requests=client.calls, manual_responses=client.responses,
            risk_results=result[0], risk_order=result[1]))
    assert eligibility == [True, False]
    assert semantic_prompts[0] == semantic_prompts[1]
    # Same post/query/comments: rank remains 1 vs 9, tie ordering favors 9.
    assert expected[0][0]["rank"] == 1 and expected[0][2]["rank"] == 9
    assert [s["rank"] for s in expected[1][:3]] == [7, 9, 1]
    assert len(baseline_client.calls) == 3  # identities excluded before model invocation
    if output := os.environ.get("GEO_OFFLINE_OUTPUT"):
        directory = Path(output).resolve()
        experiment_root = Path(__file__).resolve().parents[1] / "experiments/geographic_execution_plan"
        assert directory.is_relative_to(experiment_root) or directory.is_relative_to(Path(tempfile.gettempdir()).resolve())
        directory.mkdir(parents=True, exist_ok=True)
        artifact = dict(schema_version="p2-risk-comparison-fixture-v1", fixture_only=True,
            model_semantic_quality_evidence=False, frozen_risk_inputs=frozen,
            before_geography=dict(contexts=original_contexts, risk_requests=baseline_client.calls,
                manual_responses=baseline_client.responses, risk_results=expected[0], risk_order=expected[1]),
            after_geography=after_geography,
            identity_filtered=[dict(content_key=s["content_key"], reason=s["reason"], score=s["score"])
                for s in expected[0] if s["band"] == "discard"],
            assertions=dict(identical_complete_risk_inputs=True, identical_requests=True,
                identical_identity_exclusions=True, identical_hits_and_self_hit_exclusions=True,
                identical_scores_and_risk_order=True, target_changes_only_fixture_eligibility=True))
        (directory / "risk-comparison.json").write_text(json.dumps(artifact, ensure_ascii=False,
            sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def test_frozen_store_and_validation_return_defensive_copies():
    value = snapshot()
    store = FrozenLexiconStore(value)
    rows = store.triage_terms(["fixture"])
    rows[0]["keyword"] = "changed"
    value["rules"]["audit_goal"] = "changed"
    assert store.triage_terms(["fixture"])[0]["keyword"] == "包过"
    with pytest.raises(ValueError):
        validate_risk_inputs(value)
    with pytest.raises(ValueError):
        store.triage_terms(["other"])


@pytest.mark.parametrize("section,key,bad", [("model", "max_comments", True),
    ("model", "request_timeout", 0), ("identity", "max_followers_unverified", -1),
    ("scoring", "rule_score_high", float("nan")), ("prompt", "engine_sha256", "wrong")])
def test_freeze_rejects_invalid_parameters(section, key, bad):
    value = snapshot()
    del value["digest"]
    value.pop("schema_version")
    value[section][key] = bad
    with pytest.raises(ValueError):
        freeze_risk_inputs(**value)


def test_empty_actual_library_context_stays_empty():
    value = snapshot()
    core = {k: v for k, v in value.items() if k not in {"schema_version", "digest"}}
    core.update(category_ids=[], terms=[], search_groups={})
    frozen = freeze_risk_inputs(**core)
    engine = build_frozen_engine(frozen, ManualClient())
    assert engine.terms_for([]) == []
    for key, nonempty in [("terms", value["terms"]), ("search_groups", value["search_groups"])]:
        with pytest.raises(ValueError):
            freeze_risk_inputs(**{**core, key: nonempty})


@pytest.mark.parametrize("formal_rules", [False, True])
@pytest.mark.parametrize("formal_lexicon", [False, True])
def test_actual_resolver_capture_sqlite_restore_preserves_risk(
        creation_stack, content, tmp_path, formal_rules, formal_lexicon):
    """The actual P1 resolver, not a hand-authored rules fixture, is the oracle.

    In particular rule_snapshot is a rule identity, whereas the running P1
    pipeline passes prompt_profile_snapshot to TriageEngine.score(). Artificial
    model responses cannot conceal a different request because complete request
    strings and kwargs are compared independently of the resulting scores.
    """
    from backend.audit_agent.config import settings
    from experiments.geographic_execution_plan.storage import save_plan, load_plan
    from test_geographic_execution_plan import make_authority, build, expected
    from test_geographic_draft_configuration import no_effects
    from test_unified_draft_configuration import P

    stack = creation_stack
    authority = make_authority(stack, content, formal_rules=formal_rules,
        formal_lexicon=formal_lexicon)
    app = stack["app_service"]
    draft = app.get_draft(authority["identity"]["draft_id"], principal=P)
    with app.resource_service.confirmation_fence() as conn:
        resolution = app.resource_service.resolve_confirmation(draft, principal=P,
            resource_connection=conn)
    execution = resolution.execution
    # Mirror _run_triaged_search's real category and rules selection. No frozen
    # adapter is involved in the baseline's terms_for() or group association.
    library_ids = [str(value).strip() for value in execution.library_ids if str(value).strip()]
    category = str(execution.lexicon_category or "").strip()
    categories = library_ids or ([category] if category else [])
    rules = dict(execution.prompt_profile_snapshot)
    assert rules["audit_goal"]
    assert "审核目标：" in triage.TriageEngine._rules_text(rules)
    baseline_client = ManualClient()
    baseline = triage.TriageEngine(app.resource_service.lexicon_store, baseline_client,
        model=settings.triage_model, max_comments=settings.triage_candidate_comments,
        request_timeout=settings.triage_request_timeout,
        max_followers_personal_verified=settings.triage_max_followers_personal_verified,
        max_followers_unverified=settings.triage_max_followers_unverified,
        official_verify_patterns=settings.triage_official_verify_patterns,
        **{name: getattr(settings, "triage_" + name) for name in
            ("rule_score_high", "rule_score_medium", "rule_score_low", "rule_score_cap")})
    terms = baseline.terms_for(categories)
    assert bool(terms) is formal_lexicon
    if not formal_lexicon:
        assert execution.library_ids == categories == [] and terms == []
    queries = list(resolution.resolved_search_terms)
    assert queries
    title = "克拉玛依市 维吾尔族文化 维族文化 文化讨论 包过 欠薪"
    contexts = [dict(content_key="dy:same", rank=rank, search_keyword=queries[0],
        item={"title": title, "source_keyword": queries[0]}, comments=comments)
        for rank, comments in [(1, [{"comment_id":"a", "content":"维吾尔族文化 文化讨论"}]),
            (9, [{"comment_id":"a", "content":"维吾尔族文化 文化讨论"}]), (7, [])]]
    for key, extra in [("official", {"enterprise_verify_reason":"公安官方"}),
            ("followers", {"follower_count": settings.triage_max_followers_unverified + 1}),
            ("personal", {"custom_verify":"认证", "follower_count": settings.triage_max_followers_personal_verified + 1})]:
        contexts.append(dict(content_key=key, rank=len(contexts)+1, search_keyword=queries[0],
            item={"title":title, "source_keyword":queries[0], **extra}, comments=[]))

    def evaluate(engine, selected_rules):
        selected_terms = engine.terms_for(categories)
        scores = [engine.score(row["content_key"], row["rank"], row["item"], row["comments"],
            selected_terms, search_keyword=row["search_keyword"], rules=selected_rules) for row in contexts]
        return dict(terms=[asdict(term) for term in selected_terms],
            search_groups=engine._search_groups_for(selected_terms),
            results=[asdict(score) for score in scores],
            risk_order=[asdict(score) for score in triage.rank_candidates(scores)])

    original = evaluate(baseline, rules)
    plan = build(authority)
    plan_digest = save_plan(tmp_path / "captured-risk.sqlite3", plan)
    restored = load_plan(tmp_path / "captured-risk.sqlite3", expected_identity=expected(authority),
        expected_digest=plan_digest)
    restored_risk = restored.inputs.risk_inputs
    restored_client = ManualClient()
    after = evaluate(build_frozen_engine(restored_risk, restored_client), restored_risk["rules"])
    from experiments.geographic_execution_plan import p3_bridge as bridge
    from test_geographic_execution_plan_p3 import state, response
    geographic_checks = []
    profile = restored.inputs.p3_inputs
    geo_candidate = dict(platform="dy", content_key=contexts[0]["content_key"],
        post={"id":"same", **contexts[0]["item"]}, comments=contexts[0]["comments"],
        source_queries=queries, raw_ref="fixture:actual-captured-risk")
    for code, tier in [("650200", "local"), ("440300", "other_region")]:
        request = bridge.prepare(state(profile, code), profile, geo_candidate)
        decision = bridge.consume(request, response())
        assert decision["geo_tier"] == tier
        client = ManualClient()
        result = evaluate(build_frozen_engine(restored_risk, client), restored_risk["rules"])
        eligible = bridge.consume_policy(decision, target_level="city", selection_policy="local_only",
            risk={**result["results"][0], "valid":True, "strength":result["results"][0]["band"]})["eligible"]
        geographic_checks.append(dict(target_code=code, geo_tier=tier, fixture_eligible=eligible,
            requests=client.calls, risk_results=result,
            identical_risk_requests=client.calls == baseline_client.calls,
            identical_risk_results=result == original))
    assert [row["fixture_eligible"] for row in geographic_checks] == [True, False]
    checks = dict(identical_rules=restored_risk["rules"] == rules,
        identical_queries=[q.query for q in restored.execution_queries] == queries,
        identical_complete_requests=restored_client.calls == baseline_client.calls,
        original_audit_goal_in_every_request=all(rules["audit_goal"] in call[0] for call in baseline_client.calls),
        restored_audit_goal_in_every_request=all(rules["audit_goal"] in call[0] for call in restored_client.calls),
        identical_terms_and_groups=after["terms"] == original["terms"] and after["search_groups"] == original["search_groups"],
        identical_results_and_order=after == original,
        target_changes_only_fixture_eligibility=all(row["identical_risk_requests"] and
            row["identical_risk_results"] for row in geographic_checks))
    case = ("formal" if formal_rules else "temporary") + "-rules_" + ("formal" if formal_lexicon else "temporary") + "-lexicon"
    if output := os.environ.get("GEO_OFFLINE_OUTPUT"):
        evidence_path = Path(output) / "captured-risk-comparison.json"
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else dict(
            schema_version="p2-captured-risk-comparison-v1", fixture_only=True,
            model_semantic_quality_evidence=False, cases={})
        evidence["cases"][case] = dict(expected_identity=expected(authority), plan_digest=plan_digest,
            baseline_rules_source="actual resolution.execution.prompt_profile_snapshot",
            baseline_lexicon_source="actual resource_service.lexicon_store via TriageEngine.terms_for",
            queries=queries, categories=categories, contexts=contexts, original_rules=rules,
            audit_goal_characters=dict(original=len(rules["audit_goal"]),
                restored=len(restored_risk["rules"].get("audit_goal", ""))),
            frozen_risk_inputs=restored_risk,
            before=dict(**original, requests=baseline_client.calls, manual_responses=baseline_client.responses),
            after=dict(**after, requests=restored_client.calls, manual_responses=restored_client.responses),
            after_geography=geographic_checks,
            assertions=checks)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True,
            indent=2, allow_nan=False) + "\n", encoding="utf-8")
    no_effects(stack)
    assert len(baseline_client.calls) == len(restored_client.calls) == 3
    assert [row["band"] for row in original["results"][-3:]] == ["discard"] * 3
    if formal_lexicon:
        assert any("不计分" in row["reason"] for row in original["results"][:3])
        assert any(row["hits"] for row in original["results"][:3])
    assert all(checks.values()), checks
