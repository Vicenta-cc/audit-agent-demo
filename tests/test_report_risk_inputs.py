from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.reporting.r31_graph import AccountOverviewReportGraph


def prepare_graph(decisions):
    """Exercise R3.1's real input/planning methods without a provider or database."""
    findings = [
        SimpleNamespace(
            ref=f"finding-{decision}",
            post_ref=f"post-{decision}",
            payload={
                "decision": decision,
                "risk_level": "none" if decision == "pass" else "high",
                "summary": f"Audit conclusion: {decision}",
            },
        )
        for decision in decisions
    ]
    evidence = [
        SimpleNamespace(
            ref=f"evidence-{decision}-{support}",
            post_ref=f"post-{decision}",
            finding_ref=f"finding-{decision}",
            support_type=support,
            payload={"evidence_type": "audio", "original_text": f"{decision} {support}"},
        )
        for decision in decisions
        if decision != "pass"
        for support in ("direct", "indirect", "counter_evidence")
    ]
    snapshot = SimpleNamespace(
        display_name="Risk input regression",
        findings=findings,
        post_by_ref={item.post_ref: SimpleNamespace(payload={"title": item.post_ref}) for item in findings},
        finding_by_ref={item.ref: item for item in findings},
        evidence=evidence,
        evidence_by_ref={item.ref: item for item in evidence},
    )
    graph = object.__new__(AccountOverviewReportGraph)
    graph._snapshot = Mock(return_value=snapshot)
    graph.store = Mock()
    graph.r2_max_input_chars = 100_000
    state = {
        "report_version_id": "regression-version",
        "run_id": "regression-run",
        "statistics": {"task_overview": {"audit_result_count": len(decisions)}, "metrics": []},
    }
    state.update(graph._prepare_risk_inputs(state))
    return graph, state


def finding_plan(risk_inputs):
    return {
        "investigation_findings": [{
            "finding_alias": "IF1",
            "title": "Existing audit findings",
            "statement": "These posts require attention according to their audit results.",
            "post_memberships": [
                {
                    "post_ref": item["post_alias"],
                    "audit_finding_ref": item["audit_finding_alias"],
                    "is_representative": True,
                    "membership_evidence_refs": [evidence["evidence_alias"] for evidence in item["direct_evidence"]],
                }
                for item in risk_inputs
            ],
            "metric_refs": [],
            "boundary_notes": [],
        }],
        "standalone_risk_posts": [],
    }


@pytest.mark.parametrize("decisions", [
    ("reject",),
    ("review", "reject"),
    ("pass", "review", "reject"),
])
def test_r31_clustering_receives_reject_posts_and_preserves_evidence(decisions):
    graph, state = prepare_graph(decisions)
    expected = [decision for decision in decisions if decision != "pass"]

    def model_step(**kwargs):
        # Inspect the actual clustering prompt, then validate the provider response
        # through the production schema and semantic checks.
        payload = json.loads(kwargs["messages"][1]["content"].split("\n输入：\n", 1)[1])
        assert payload["statistics"]["task_overview"]["audit_result_count"] == len(decisions)
        assert [item["audit_finding"]["decision"] for item in payload["risk_posts"]] == expected
        for item, decision in zip(payload["risk_posts"], expected):
            assert [evidence["original_text"] for evidence in item["direct_evidence"]] == [f"{decision} direct"]
        output = kwargs["response_model"].model_validate(finding_plan(payload["risk_posts"])).model_dump(mode="json")
        assert kwargs["semantic_validator"](output) == []
        return SimpleNamespace(output=output)

    graph._model_step = Mock(side_effect=model_step)
    result = graph._plan_investigation_findings(state)
    graph._model_step.assert_called_once()
    assert result["risk_post_coverage_complete"] is True
    memberships = result["investigation_findings"][0]["post_memberships"]
    assert [item["post_ref"] for item in memberships] == [f"post-{decision}" for decision in expected]
    for item, decision in zip(memberships, expected):
        assert item["audit_finding_ref"] == f"finding-{decision}"
        assert item["membership_evidence_refs"] == [f"evidence-{decision}-direct"]


def test_r31_clustering_cannot_silently_omit_reject_post():
    graph, state = prepare_graph(("review", "reject"))
    review_only = [item for item in state["risk_inputs"] if item["audit_finding"]["decision"] == "review"]
    errors = graph._validate_investigation_plan(finding_plan(review_only), state)
    assert errors == ["risk Posts omitted from plan: ['P2']"]
