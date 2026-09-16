from __future__ import annotations

from collections import Counter
import itertools
from pathlib import Path
import runpy
from unittest.mock import Mock

import pytest

from backend.reporting.unified_graph import classify_unified_findings


seed = runpy.run_path(str(Path(__file__).with_name("test_pass_report.py")))


def _verdict_cases():
    cases = []
    for count in range(1, 6):
        for mask in range(1 << count):
            verdicts = []
            risk_index = 0
            for index in range(count):
                if mask & (1 << index):
                    decision = "review" if risk_index % 2 == 0 else "reject"
                    verdicts.append((decision, "medium" if decision == "review" else "high"))
                    risk_index += 1
                else:
                    verdicts.append(("pass", "none"))
            cases.append(pytest.param(verdicts, id=f"n{count}-mask{mask:0{count}b}"))
    return cases


@pytest.mark.parametrize("verdicts", _verdict_cases())
def test_new_tasks_publish_one_deterministic_unified_report_for_every_safe_risk_layout(
    tmp_path, verdicts
):
    source, store = seed["seed_audit"](tmp_path, verdicts=verdicts)
    provider = Mock()
    provider.generate_structured.side_effect = AssertionError(
        "unified report generation must not invoke Qwen"
    )

    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        model_client=provider,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )

    provider.generate_structured.assert_not_called()
    version = store.get_version(result.report_version_id)
    assert version["status"] == "published"
    assert version["model"] == "deterministic"
    assert version["prompt_version"] == "report-unified-audit/v1"

    reopened = seed["ReportStore"](store.db_path)
    document = reopened.get_frontend_report(result.report_version_id)
    assert document == store.get_frontend_report(result.report_version_id)
    assert document["template_kind"] == "unified_audit"
    assert len(document["posts"]) == len(verdicts)

    expected = Counter(decision for decision, _risk in verdicts)
    view = reopened.get_presentation_projection(result.report_version_id)
    assert view["statistics"]["decision"] == {
        "pass": expected["pass"],
        "review": expected["review"],
        "reject": expected["reject"],
    }

    sections = {item["section_number"]: item for item in document["ordered_sections"]}
    assert sections["1"]["title"] == "报告概览"
    assert "任务：单条帖子审核演示" in sections["1"]["paragraphs"][0]["text"]
    assert "平台：抖音" in sections["1"]["paragraphs"][0]["text"]
    assert sections["5"]["title"] == "账号关联分析"
    assert sections["6"]["title"] == "审核建议"
    assert sections["7"]["title"] == "方法、范围与限制"
    assert sections["8"]["title"] == "附录"
    assert ("2" in sections) is bool(expected["review"] or expected["reject"])
    assert ("3" in sections) is bool(expected["pass"])
    assert "4" not in sections

    grouped_refs = [
        ref
        for number in ("2", "3", "4")
        for claim in sections.get(number, {}).get("claims", [])
        for ref in claim.get("audit_finding_refs", [])
    ]
    expected_refs = [item["audit_finding_ref"] for item in document["audit_findings"]]
    assert Counter(grouped_refs) == Counter(expected_refs)
    assert len(grouped_refs) == len(set(grouped_refs)) == len(verdicts)

    appendix = reopened.get_presentation_appendix(
        result.report_version_id, view="posts", limit=100
    )
    assert appendix["matched_count"] == len(verdicts)
    assert len(appendix["items"]) == len(verdicts)
    with reopened._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM report_provider_exchanges "
            "WHERE report_version_id = ?",
            (result.report_version_id,),
        ).fetchone()[0] == 0


def test_unified_report_supports_all_pass_review_reject_decision_combinations(tmp_path):
    # Exercise the full 1..5 decision space without multiplying heavyweight
    # generation runs: section membership is the deterministic core contract.
    for count in range(1, 6):
        for decisions in itertools.product(("pass", "review", "reject"), repeat=count):
            findings = [
                {
                    "ref": f"finding-{index}",
                    "decision": decision,
                    "risk_level": "none" if decision == "pass" else "high",
                }
                for index, decision in enumerate(decisions)
            ]
            groups = classify_unified_findings(findings)
            assigned = [ref for values in groups.values() for ref in values]
            assert Counter(assigned) == Counter(item["ref"] for item in findings)
            assert len(assigned) == len(set(assigned)) == count


@pytest.mark.parametrize("count", range(1, 6))
@pytest.mark.parametrize(
    "decision,risk,section_number",
    [("review", "medium", "2"), ("reject", "high", "2")],
)
def test_unified_report_publishes_all_review_or_all_reject_layouts(
    tmp_path, count, decision, risk, section_number
):
    source, store = seed["seed_audit"](
        tmp_path, verdicts=[(decision, risk)] * count
    )
    provider = Mock()
    provider.generate_structured.side_effect = AssertionError(
        "unified report generation must not invoke Qwen"
    )

    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        model_client=provider,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )

    provider.generate_structured.assert_not_called()
    document = seed["ReportStore"](store.db_path).get_frontend_report(
        result.report_version_id
    )
    sections = {
        item["section_number"]: item for item in document["ordered_sections"]
    }
    finding_refs = [
        ref
        for claim in sections[section_number]["claims"]
        for ref in claim["audit_finding_refs"]
    ]
    assert len(finding_refs) == len(set(finding_refs)) == count
    assert "3" not in sections
    assert "4" not in sections


def test_inconsistent_pass_risk_result_is_isolated_as_pending(tmp_path):
    source, store = seed["seed_audit"](
        tmp_path, verdicts=[("pass", "low"), ("pass", "none")]
    )

    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )

    document = store.get_frontend_report(result.report_version_id)
    sections = {
        item["section_number"]: item for item in document["ordered_sections"]
    }
    safe_refs = [
        ref
        for claim in sections["3"]["claims"]
        for ref in claim["audit_finding_refs"]
    ]
    pending_refs = [
        ref
        for claim in sections["4"]["claims"]
        for ref in claim["audit_finding_refs"]
    ]
    assert len(safe_refs) == len(pending_refs) == 1
    assert set(safe_refs).isdisjoint(pending_refs)
    assert "不能直接归为安全" in " ".join(
        paragraph["text"] for paragraph in sections["4"]["paragraphs"]
    )


def test_unified_report_rejects_more_than_five_posts(tmp_path):
    source, store = seed["seed_audit"](
        tmp_path, verdicts=[("pass", "none")] * 6
    )
    with pytest.raises(Exception, match="1 to 5 audited posts"):
        seed["R31ReportRuntime"](store).generate(
            "new-search-task",
            source=source,
            checkpoint_path=tmp_path / "checkpoints.sqlite3",
        )
