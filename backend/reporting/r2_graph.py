from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from backend.domain.identity import stable_hash
from backend.reporting.contracts import ClaimType, ReportGraphState, SectionKind
from backend.reporting.errors import ReportGenerationError, ReportValidationError
from backend.reporting.integration_graph import IntegrationReportGraph
from backend.reporting.qwen_report_client import QwenReportClient
from backend.reporting.r2_contracts import (
    make_investigation_plan_model,
    make_risk_section_model,
    make_synthesis_conclusion_model,
    public_risk_post_input,
)
from backend.reporting.r2_prompts import (
    R2_PROMPT_VERSION,
    SERVER_BOUNDARY_NOTES,
    finding_section_messages,
    investigation_finding_messages,
    synthesis_conclusion_messages,
)


R2_NODE_ORDER = (
    "freeze_source_snapshot",
    "build_statistics",
    "prepare_risk_inputs",
    "plan_investigation_findings",
    "plan_outline",
    "draft_sections",
    "validate_numbers",
    "validate_claim_support",
    "validate_citations",
    "assemble_report",
    "publish_report_version",
)

_LOCAL_ALIAS = re.compile(r"\b(?:P|F|E|IF|M)\d+\b")


class R2QwenReportClient(QwenReportClient):
    """Provider adapter with at most one server-level semantic correction."""

    def generate_structured(self, **kwargs):
        # Earlier chapters retain the existing server-level semantic repair.
        # The final paragraph-only step additionally gets one bounded retry
        # when the provider explicitly reports a length truncation.
        kwargs["allow_repair"] = False
        response_model = kwargs.get("response_model")
        kwargs["allow_truncation_repair"] = bool(
            response_model is not None
            and getattr(response_model, "__name__", "") == "SynthesisConclusionDraft"
        )
        return super().generate_structured(**kwargs)


class RiskFindingReportGraph(IntegrationReportGraph):
    """R2 report graph: deterministic four chapters plus verified risk findings."""

    def __init__(
        self,
        *,
        source,
        store=None,
        model_client=None,
        checkpoint_path: Path | None = None,
        max_input_chars: int | None = None,
    ):
        self.r2_max_input_chars = int(
            max_input_chars
            if max_input_chars is not None
            else os.getenv("REPORT_R2_MAX_INPUT_CHARS", "900000")
        )
        super().__init__(
            source=source,
            store=store,
            model_client=model_client or R2QwenReportClient(prompt_version=R2_PROMPT_VERSION),
            checkpoint_path=checkpoint_path,
            representative_finding_limit=13,
        )

    def _provider_repair_output(self, output: dict[str, Any], response_model: type) -> dict[str, Any]:
        """Redact private refs before a semantic repair request is sent."""
        registry = getattr(self, "_current_alias_registry", {})
        reverse: dict[str, str] = {}
        for key in (
            "post_alias_to_ref",
            "evidence_alias_to_ref",
            "audit_finding_alias_to_ref",
            "metric_alias_to_ref",
        ):
            for alias, ref in (registry.get(key) or {}).items():
                reverse[str(ref)] = str(alias)

        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: sanitize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            if isinstance(value, tuple):
                return [sanitize(item) for item in value]
            if not isinstance(value, str):
                return value
            for private, alias in reverse.items():
                value = value.replace(private, alias)
            return re.sub(
                r"(?:investigation-finding|evidence:audit_result|finding:audit_result|report-version|post):[^\s,\]\}\"]+",
                "INVALID_REF",
                value,
                flags=re.IGNORECASE,
            )

        return sanitize(output)

    def _provider_repair_message(self, response_model: type, errors: list[str]) -> str:
        if response_model.__name__ == "ReportFindingPlan":
            return (
                "JSON 结构有效，但 InvestigationFinding 计划违反了服务器来源和覆盖约束。"
                "请只修正当前 JSON 中的 P/F/E/M 局部别名、membership 归属和 standalone 覆盖；"
                "不要添加 related_posts、representative_posts、paragraphs 或 claims 字段，"
                "不要输出任何 canonical ID、数据库 ID、Snapshot、report version 或 JSON 路径。"
                "statement 只是待表达草案，不能替代 AuditFinding 或 Evidence 来源。错误："
                + "; ".join(errors)
            )
        return super()._provider_repair_message(response_model, errors)

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(ReportGraphState)
        functions: dict[str, Callable[[ReportGraphState], dict[str, Any]]] = {
            "freeze_source_snapshot": self._freeze_source_snapshot,
            "build_statistics": self._build_statistics,
            "prepare_risk_inputs": self._prepare_risk_inputs,
            "plan_investigation_findings": self._plan_investigation_findings,
            "plan_outline": self._plan_outline,
            "draft_sections": self._draft_sections,
            "validate_numbers": self._validate_numbers,
            "validate_claim_support": self._validate_claim_support,
            "validate_citations": self._validate_citations,
            "assemble_report": self._assemble_report,
            "publish_report_version": self._publish_report_version,
        }
        for name in R2_NODE_ORDER:
            graph.add_node(name, self._wrap_node(name, functions[name]))
        graph.add_edge(START, R2_NODE_ORDER[0])
        for current, following in zip(R2_NODE_ORDER, R2_NODE_ORDER[1:]):
            graph.add_edge(current, following)
        graph.add_edge(R2_NODE_ORDER[-1], END)
        return graph

    def _prepare_risk_inputs(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        posts = snapshot.post_by_ref
        findings = [
            item
            for item in snapshot.findings
            if item.payload.get("decision") in {"review", "reject"}
            and item.payload.get("risk_level") in {"low", "medium", "high"}
        ]
        if not findings:
            raise ReportValidationError(
                "prepare_risk_inputs",
                ["the frozen Snapshot does not contain any review or reject risk posts"],
            )
        evidence_by_post: dict[str, list[Any]] = {}
        for item in snapshot.evidence:
            if item.support_type == "direct":
                evidence_by_post.setdefault(item.post_ref, []).append(item)
        post_alias_to_ref: dict[str, str] = {}
        evidence_alias_to_ref: dict[str, str] = {}
        finding_alias_to_ref: dict[str, str] = {}
        metric_alias_to_ref = {
            f"M{index}": item["metric_key"]
            for index, item in enumerate(state["statistics"]["metrics"], 1)
        }
        risk_inputs: list[dict[str, Any]] = []
        evidence_counter = 0
        for post_index, finding in enumerate(findings, 1):
            post_alias = f"P{post_index}"
            post_alias_to_ref[post_alias] = finding.post_ref
            finding_alias_to_ref[f"F{post_index}"] = finding.ref
            evidence_payloads: list[dict[str, Any]] = []
            for evidence in evidence_by_post.get(finding.post_ref, []):
                evidence_counter += 1
                evidence_alias = f"E{evidence_counter}"
                evidence_alias_to_ref[evidence_alias] = evidence.ref
                payload = dict(evidence.payload)
                payload["evidence_alias"] = evidence_alias
                evidence_payloads.append(payload)
            risk_inputs.append(
                public_risk_post_input(
                    post_alias=post_alias,
                    audit_finding_alias=f"F{post_index}",
                    post=posts[finding.post_ref].payload,
                    finding=finding.payload,
                    evidence=evidence_payloads,
                ).model_dump(mode="json")
            )
        serialized = json.dumps(risk_inputs, ensure_ascii=False, sort_keys=True)
        input_chars = len(serialized)
        input_tokens_estimate = math.ceil(input_chars / 4)
        config = {
            "max_input_chars": self.r2_max_input_chars,
            "risk_post_count": len(risk_inputs),
            # Snapshot-level direct Evidence can include material outside the
            # eligible risk-post set. Keep both counts explicit rather than
            # treating the model input as the entire frozen Evidence corpus.
            "direct_evidence_count": len(snapshot.evidence),
            "risk_input_direct_evidence_count": evidence_counter,
            "input_chars": input_chars,
            "input_tokens_estimate": input_tokens_estimate,
            "input_hash": stable_hash(risk_inputs),
        }
        self._current_alias_registry = {
            "post_alias_to_ref": post_alias_to_ref,
            "evidence_alias_to_ref": evidence_alias_to_ref,
            "audit_finding_alias_to_ref": finding_alias_to_ref,
            "metric_alias_to_ref": metric_alias_to_ref,
        }
        self.store.record_run_event(state["run_id"], "prepare_risk_inputs", "input_measured", config)
        if input_chars > self.r2_max_input_chars:
            raise ReportGenerationError(
                "R2 risk input exceeds configured context limit before Provider call: "
                f"{input_chars} > {self.r2_max_input_chars} characters"
            )
        return {
            "risk_inputs": risk_inputs,
            "risk_alias_registry": {
                "post_alias_to_ref": post_alias_to_ref,
                "evidence_alias_to_ref": evidence_alias_to_ref,
                "audit_finding_alias_to_ref": finding_alias_to_ref,
                "metric_alias_to_ref": metric_alias_to_ref,
            },
            "r2_configuration": config,
        }

    def _plan_investigation_findings(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        metric_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["metric_alias_to_ref"].items()
        }
        safe_statistics = {
            "task_overview": {
                key: value
                for key, value in (state["statistics"].get("task_overview") or {}).items()
                if key not in {"task_id", "source_hash", "snapshot_hash"}
            },
            "metrics": [
                {
                    "metric_alias": metric_reverse.get(item["metric_key"], ""),
                    "metric_name": item["metric_name"],
                    "label": item["label"],
                    "value": item["value"],
                    "denominator": item["denominator"],
                    "group": item.get("group") or {},
                }
                for item in state["statistics"]["metrics"]
            ],
        }
        messages = investigation_finding_messages(
            task_name=snapshot.display_name,
            statistics=safe_statistics,
            risk_inputs=state["risk_inputs"],
        )
        registry = state["risk_alias_registry"]
        response_model = make_investigation_plan_model(
            post_aliases=tuple(registry["post_alias_to_ref"]),
            finding_aliases=tuple(f"IF{i}" for i in range(1, len(state["risk_inputs"]) + 1)),
            audit_finding_aliases=tuple(registry["audit_finding_alias_to_ref"]),
            evidence_aliases=tuple(registry["evidence_alias_to_ref"]),
            metric_aliases=tuple(registry["metric_alias_to_ref"]),
        )
        result = self._model_step(
            state=state,
            node_name="plan_investigation_findings",
            section_id="",
            messages=messages,
            response_model=response_model,
            semantic_validator=lambda output: self._validate_investigation_plan(output, state),
        )
        post_aliases = registry["post_alias_to_ref"]
        evidence_aliases = registry["evidence_alias_to_ref"]
        audit_aliases = registry["audit_finding_alias_to_ref"]
        metric_refs = registry["metric_alias_to_ref"]
        materialized: list[dict[str, Any]] = []
        for ordinal, item in enumerate(result.output["investigation_findings"], 1):
            memberships = []
            for membership in item["post_memberships"]:
                memberships.append(
                    {
                        "post_ref": post_aliases[membership["post_ref"]],
                        "audit_finding_ref": audit_aliases[membership["audit_finding_ref"]],
                        "is_representative": bool(membership.get("is_representative")),
                        "membership_evidence_refs": [
                            evidence_aliases[alias]
                            for alias in membership.get("membership_evidence_refs") or []
                        ],
                    }
                )
            materialized.append(
                {
                    "finding_ref": f"investigation-finding:{state['report_version_id']}:{item['finding_alias']}",
                    "alias": item["finding_alias"],
                    "display_ordinal": ordinal,
                    "title": item["title"],
                    "statement": item["statement"],
                    "post_memberships": memberships,
                    "metric_refs": [metric_refs[ref] for ref in item["metric_refs"]],
                    "boundary_notes": list(item["boundary_notes"]),
                }
            )
        standalone = [
            {
                "post_ref": post_aliases[item["post_ref"]],
                "audit_finding_ref": audit_aliases[item["audit_finding_ref"]],
                "disposition_note": item["disposition_note"],
            }
            for item in result.output.get("standalone_risk_posts") or []
        ]
        covered = {
            membership["post_ref"]
            for item in materialized
            for membership in item["post_memberships"]
        }
        standalone_refs = {item["post_ref"] for item in standalone}
        coverage_complete = covered | standalone_refs == set(post_aliases.values())
        self.store.record_run_event(
            state["run_id"], "plan_investigation_findings", "validated",
            {
                "finding_count": len(materialized),
                "standalone_count": len(standalone),
                "risk_post_coverage": len(covered | standalone_refs),
                "risk_post_coverage_complete": coverage_complete,
            },
        )
        return {
            "investigation_findings": materialized,
            "standalone_risk_posts": standalone,
            "risk_post_coverage_complete": coverage_complete,
        }

    def _validate_investigation_plan(
        self, output: dict[str, Any], state: ReportGraphState
    ) -> list[str]:
        expected_posts = {item["post_alias"] for item in state["risk_inputs"]}
        metric_refs = set(state["risk_alias_registry"]["metric_alias_to_ref"])
        post_map = state["risk_alias_registry"]["post_alias_to_ref"]
        audit_map = state["risk_alias_registry"]["audit_finding_alias_to_ref"]
        evidence_map = state["risk_alias_registry"]["evidence_alias_to_ref"]
        snapshot = self._snapshot(state["report_version_id"])
        finding_by_ref = snapshot.finding_by_ref
        evidence_by_ref = snapshot.evidence_by_ref
        findings = output.get("investigation_findings") or []
        errors: list[str] = []
        aliases = [item.get("finding_alias") for item in findings]
        expected_aliases = [f"IF{i}" for i in range(1, len(findings) + 1)]
        if aliases != expected_aliases:
            errors.append("InvestigationFinding aliases must be continuous IF1..IFn")
        covered: set[str] = set()
        for item in findings:
            alias = item.get("finding_alias")
            memberships = list(item.get("post_memberships") or [])
            membership_posts: set[str] = set()
            for membership in memberships:
                post_alias = membership.get("post_ref")
                audit_alias = membership.get("audit_finding_ref")
                if post_alias not in post_map:
                    errors.append(f"{alias} references unknown Post alias: {post_alias}")
                    continue
                if post_alias in membership_posts:
                    errors.append(f"{alias} repeats post membership: {post_alias}")
                membership_posts.add(post_alias)
                if audit_alias not in audit_map:
                    errors.append(f"{alias} references unknown AuditFinding alias: {audit_alias}")
                    continue
                post_ref = post_map[post_alias]
                finding_ref = audit_map[audit_alias]
                finding = finding_by_ref.get(finding_ref)
                if finding is None or finding.post_ref != post_ref:
                    errors.append(f"{alias} AuditFinding does not belong to {post_alias}")
                membership_evidence_aliases = list(membership.get("membership_evidence_refs") or [])
                if len(membership_evidence_aliases) != len(set(membership_evidence_aliases)):
                    errors.append(f"{alias} repeats Evidence in membership {post_alias}")
                for evidence_alias in membership_evidence_aliases:
                    evidence_ref = evidence_map.get(evidence_alias)
                    evidence = evidence_by_ref.get(evidence_ref) if evidence_ref else None
                    if evidence is None or evidence.support_type != "direct":
                        errors.append(f"{alias} references non-direct/unknown Evidence: {evidence_alias}")
                    elif evidence.post_ref != post_ref or evidence.finding_ref != finding_ref:
                        errors.append(f"{alias} Evidence does not belong to membership {post_alias}/{audit_alias}")
            covered.update(membership_posts)
            if set(item.get("metric_refs") or []) - metric_refs:
                errors.append(f"{alias} references an unknown metric")
            for field in ("title", "statement", "boundary_notes"):
                value = item.get(field)
                values = value if isinstance(value, list) else [value]
                for text in values:
                    if _LOCAL_ALIAS.search(str(text or "")):
                        errors.append(f"{alias} exposes a local alias in {field}")
        standalone = list(output.get("standalone_risk_posts") or [])
        standalone_posts: set[str] = set()
        for item in standalone:
            post_alias = item.get("post_ref")
            audit_alias = item.get("audit_finding_ref")
            if post_alias not in post_map or audit_alias not in audit_map:
                errors.append(f"standalone references unknown local alias: {post_alias}/{audit_alias}")
                continue
            if post_alias in standalone_posts:
                errors.append(f"standalone repeats Post alias: {post_alias}")
            standalone_posts.add(post_alias)
            finding = finding_by_ref.get(audit_map[audit_alias])
            if finding is None or finding.post_ref != post_map[post_alias]:
                errors.append(f"standalone AuditFinding does not belong to {post_alias}")
            if _LOCAL_ALIAS.search(str(item.get("disposition_note") or "")):
                errors.append(f"standalone {post_alias} exposes a local alias")
        overlap = covered & standalone_posts
        if overlap:
            errors.append(f"standalone overlaps Finding membership: {sorted(overlap)}")
        missing = expected_posts - (covered | standalone_posts)
        if missing:
            errors.append(f"risk Posts omitted from plan: {sorted(missing)}")
        return errors

    def _plan_outline(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        all_audit_findings = [
            membership["audit_finding_ref"]
            for item in state["investigation_findings"]
            for membership in item["post_memberships"]
        ]
        sections = [
            {
                "section_id": "overview",
                "section_kind": SectionKind.OVERVIEW.value,
                "title": "调查概况",
                "purpose": "呈现当前调查任务的确定性范围和统计",
                "finding_ids": [], "metric_refs": [item["metric_key"] for item in state["statistics"]["metrics"]],
                "is_report_category": False,
            }
        ]
        for item in state["investigation_findings"]:
            sections.append(
                {
                    "section_id": f"investigation-{item['alias'].lower()}",
                    "section_kind": SectionKind.RISK_ANALYSIS.value,
                    "title": item["title"],
                    "purpose": "归纳已验证的相关风险帖子和审核依据",
                    "finding_ids": list(dict.fromkeys(
                        membership["audit_finding_ref"] for membership in item["post_memberships"]
                    )),
                    "metric_refs": item["metric_refs"],
                    "is_report_category": True,
                }
            )
        if state.get("standalone_risk_posts"):
            sections.append(
                {
                    "section_id": "standalone-risk-posts",
                    "section_kind": SectionKind.RISK_ANALYSIS.value,
                    "title": "其他独立风险事项",
                    "purpose": "展示已研判但未归入共性调查发现的风险帖子",
                    "finding_ids": list(dict.fromkeys(
                        item["audit_finding_ref"] for item in state["standalone_risk_posts"]
                    )),
                    "metric_refs": [],
                    "is_report_category": False,
                }
            )
        sections.extend(
            [
                {
                    "section_id": "synthesis",
                    "section_kind": SectionKind.SYNTHESIS.value,
                    "title": "综合研判",
                    "purpose": "仅归纳已验证的调查发现",
                    "finding_ids": list(dict.fromkeys(all_audit_findings)),
                    "metric_refs": [], "is_report_category": False,
                },
                {
                    "section_id": "conclusion",
                    "section_kind": SectionKind.CONCLUSION.value,
                    "title": "调查结论与建议",
                    "purpose": "说明当前材料支持的结论和边界",
                    "finding_ids": list(dict.fromkeys(all_audit_findings)),
                    "metric_refs": [], "is_report_category": False,
                },
            ]
        )
        self.store.record_run_event(state["run_id"], "plan_outline", "validated", {"category_count": len(state["investigation_findings"])})
        return {"outline": {"report_title": f"{snapshot.display_name}调查报告", "executive_summary_focus": "当前调查材料中的审核结论", "sections": sections}}

    def _draft_sections(self, state: ReportGraphState) -> dict[str, Any]:
        metrics = state["statistics"]["metrics"]
        total = next(item for item in metrics if item["metric_name"] == "scalar" and item["label"] == "任务内容总数")
        reviewed = next(item for item in metrics if item["metric_name"] == "scalar" and item["label"] == "审核结果总数")
        pass_metric = next(item for item in metrics if item.get("group") == {"decision": "pass"} and item["metric_name"] == "count")
        review_metric = next(item for item in metrics if item.get("group") == {"decision": "review"} and item["metric_name"] == "count")
        risk_metrics = {
            item["group"]["risk_level"]: item
            for item in metrics
            if item.get("group", {}).get("risk_level") is not None and item["metric_name"] == "count"
        }
        direct_finding_metric = next(
            item for item in metrics
            if item["metric_name"] == "scalar" and item["label"] == "包含直接 Evidence 的 Finding 数"
        )
        direct_evidence_metric = next(
            item for item in metrics
            if item["metric_name"] == "scalar" and item["label"] == "直接 Evidence 总数"
        )
        overview_metric_refs = [
            total["metric_key"], reviewed["metric_key"], pass_metric["metric_key"], review_metric["metric_key"],
            *(risk_metrics[level]["metric_key"] for level in ("none", "low", "medium", "high")),
            direct_finding_metric["metric_key"], direct_evidence_metric["metric_key"],
        ]
        overview_claim = {
            "claim_id": "overview-statistics",
            "claim_type": ClaimType.NUMERIC.value,
            "text": (
                f"当前调查任务纳入{int(total['value'])}篇已完成审核内容，形成{int(reviewed['value'])}条有效审核结果；"
                f"决定分布为pass {int(pass_metric['value'])}篇、review {int(review_metric['value'])}篇；"
                f"风险等级分布为none {int(risk_metrics['none']['value'])}篇、low {int(risk_metrics['low']['value'])}篇、"
                f"medium {int(risk_metrics['medium']['value'])}篇、high {int(risk_metrics['high']['value'])}篇；"
                f"冻结资料包含{int(direct_evidence_metric['value'])}条direct Evidence，覆盖{int(direct_finding_metric['value'])}条Finding。"
            ),
            "finding_ids": [], "evidence_ids": [],
            "metric_refs": overview_metric_refs,
            "support_type": "aggregate",
        }
        overview_boundary_claim = {
            "claim_id": "overview-boundary",
            "claim_type": ClaimType.METHODOLOGY.value,
            "text": (
                "当前冻结调查材料范围仅覆盖本次已完成审核内容及其冻结审核资料；不代表任务全部采集成员。"
                "间接材料和反证材料不作为本报告风险依据，未记录内容不据此判断为不存在、安全或无风险。"
            ),
            "finding_ids": [], "evidence_ids": [], "metric_refs": [], "support_type": "aggregate",
        }
        overview = {
            "section_id": "overview", "section_kind": SectionKind.OVERVIEW.value,
            "title": "调查概况", "purpose": "呈现确定性统计", "is_report_category": False,
            "paragraphs": [
                {"text": overview_claim["text"], "claim_ids": [overview_claim["claim_id"]]},
                {"text": overview_boundary_claim["text"], "claim_ids": [overview_boundary_claim["claim_id"]]},
            ],
            "claims": [overview_claim, overview_boundary_claim], "case_blocks": [],
            "body": overview_claim["text"] + "\n\n" + overview_boundary_claim["text"],
        }
        drafts = [overview]
        task_name = self._snapshot(state["report_version_id"]).display_name
        risk_by_alias = {item["alias"]: item for item in state["investigation_findings"]}
        post_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["post_alias_to_ref"].items()
        }
        evidence_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["evidence_alias_to_ref"].items()
        }
        metric_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["metric_alias_to_ref"].items()
        }
        metric_registry = {item["metric_key"]: item for item in metrics}
        for section in state["outline"]["sections"]:
            if section["section_kind"] != SectionKind.RISK_ANALYSIS.value:
                continue
            if section["section_id"] == "standalone-risk-posts":
                drafts.append(self._draft_standalone_section(state, section))
                continue
            alias = section["section_id"].split("-")[-1].upper()
            item = risk_by_alias[alias]
            posts = []
            for membership in item["post_memberships"]:
                post_alias = self._post_alias_for_ref(state, membership["post_ref"])
                candidate = next(
                    candidate for candidate in state["risk_inputs"]
                    if candidate["post_alias"] == post_alias
                )
                evidence_allowed = {
                    evidence_reverse[ref]
                    for ref in membership.get("membership_evidence_refs") or []
                    if ref in evidence_reverse
                }
                projected = dict(candidate)
                projected["direct_evidence"] = [
                    evidence for evidence in candidate.get("direct_evidence") or []
                    if evidence.get("evidence_alias") in evidence_allowed
                ]
                projected["audit_finding_alias"] = self._audit_alias_for_ref(
                    state, membership["audit_finding_ref"]
                )
                posts.append(projected)
            allowed_metrics = [
                {
                    "metric_alias": metric_reverse[ref],
                    "metric_name": metric_registry[ref]["metric_name"],
                    "label": metric_registry[ref]["label"],
                    "value": metric_registry[ref]["value"],
                    "denominator": metric_registry[ref]["denominator"],
                    "group": metric_registry[ref].get("group") or {},
                }
                for ref in item["metric_refs"]
                if ref in metric_registry
            ]
            model_item = {
                "finding_alias": item["alias"],
                "title": item["title"],
                "statement": item["statement"],
                "post_memberships": [
                    {
                        "post_ref": post_reverse[membership["post_ref"]],
                        "audit_finding_ref": self._audit_alias_for_ref(
                            state, membership["audit_finding_ref"]
                        ),
                        "is_representative": bool(membership["is_representative"]),
                        "membership_evidence_refs": [
                            evidence_reverse[ref]
                            for ref in membership.get("membership_evidence_refs") or []
                        ],
                    }
                    for membership in item["post_memberships"]
                ],
                "metric_refs": [metric_reverse[ref] for ref in item["metric_refs"]],
            }
            model_section = {
                "section_id": section["section_id"],
                "title": section["title"],
                "purpose": section["purpose"],
                "metric_refs": [metric_reverse[ref] for ref in section.get("metric_refs") or [] if ref in metric_reverse],
            }
            messages = finding_section_messages(
                task_name=task_name,
                section=model_section,
                investigation_finding=model_item,
                posts=posts,
                metrics=allowed_metrics,
            )
            allowed_evidence_aliases = tuple(
                evidence_reverse[membership_evidence_ref]
                for membership in item["post_memberships"]
                for membership_evidence_ref in membership.get("membership_evidence_refs") or []
                if membership_evidence_ref in evidence_reverse
            )
            allowed_audit_aliases = tuple(
                self._audit_alias_for_ref(state, membership["audit_finding_ref"])
                for membership in item["post_memberships"]
            )
            response_model = make_risk_section_model(
                section_id=section["section_id"],
                title=section["title"],
                finding_aliases=(item["alias"],),
                evidence_aliases=allowed_evidence_aliases,
                audit_finding_aliases=allowed_audit_aliases,
                metric_aliases=tuple(metric_reverse[ref] for ref in item["metric_refs"] if ref in metric_reverse),
            )
            result = self._model_step(
                state=state,
                node_name="draft_sections",
                section_id=section["section_id"],
                messages=messages,
                response_model=response_model,
                semantic_validator=lambda output, current=section, current_item=item: self._validate_risk_section(output, current, current_item, state),
            )
            drafts.append(self._map_risk_section(result.output, section, item, state))
        drafts.extend(self._draft_synthesis_and_conclusion({**state, "section_drafts": drafts}))
        claims = [claim for draft in drafts for claim in draft.get("claims") or []]
        self.store.record_run_event(state["run_id"], "draft_sections", "validated", {"section_count": len(drafts)})
        return {"section_drafts": drafts, "claims": claims}

    def _draft_standalone_section(self, state: ReportGraphState, section: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        posts = snapshot.post_by_ref
        findings = snapshot.finding_by_ref
        evidence = snapshot.evidence_by_ref
        paragraphs = []
        claims = []
        for ordinal, item in enumerate(state.get("standalone_risk_posts") or [], 1):
            post = posts[item["post_ref"]]
            finding = findings[item["audit_finding_ref"]]
            evidence_ids = [
                candidate.ref
                for candidate in evidence.values()
                if candidate.finding_ref == finding.ref and candidate.support_type == "direct"
            ]
            title = str(post.payload.get("title") or post.payload.get("caption") or "未命名帖子")
            summary = str(finding.payload.get("summary") or finding.payload.get("risk_basis") or "当前审核记录未提供摘要。")
            claim_id = f"standalone-{ordinal}"
            text = f"{title}：当前审核记录为{finding.payload.get('decision') or '未知决定'}，风险等级为{finding.payload.get('risk_level') or '未知'}；审核摘要：{summary}"
            claims.append({
                "claim_id": claim_id,
                "claim_type": ClaimType.DOMAIN_FACT.value,
                "text": text,
                "finding_ids": [finding.ref],
                "evidence_ids": evidence_ids,
                "metric_refs": [],
                "support_type": "direct",
            })
            paragraphs.append({"text": text, "claim_ids": [claim_id]})
        return {
            "section_id": section["section_id"],
            "section_kind": SectionKind.RISK_ANALYSIS.value,
            "title": section["title"],
            "purpose": section["purpose"],
            "paragraphs": paragraphs or [{"text": "当前没有独立风险事项。", "claim_ids": []}],
            "claims": claims,
            "case_blocks": [],
            "is_report_category": False,
            "body": "\n\n".join(item["text"] for item in paragraphs),
        }

    def _post_alias_for_ref(self, state: ReportGraphState, ref: str) -> str:
        for alias, candidate in state["risk_alias_registry"]["post_alias_to_ref"].items():
            if candidate == ref:
                return alias
        raise ReportValidationError("draft_sections", [f"unknown frozen Post ref {ref}"])

    def _audit_alias_for_ref(self, state: ReportGraphState, ref: str) -> str:
        for alias, candidate in state["risk_alias_registry"]["audit_finding_alias_to_ref"].items():
            if candidate == ref:
                return alias
        raise ReportValidationError("draft_sections", [f"unknown frozen AuditFinding ref {ref}"])

    def _validate_risk_section(self, output: dict[str, Any], section: dict[str, Any], item: dict[str, Any], state: ReportGraphState) -> list[str]:
        evidence_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["evidence_alias_to_ref"].items()
        }
        allowed_evidence_aliases = {
            evidence_reverse[evidence_ref]
            for membership in item.get("post_memberships") or []
            for evidence_ref in membership.get("membership_evidence_refs") or []
            if evidence_ref in evidence_reverse
        }
        audit_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["audit_finding_alias_to_ref"].items()
        }
        allowed_audits = {
            audit_reverse[membership["audit_finding_ref"]]
            for membership in item.get("post_memberships") or []
            if membership["audit_finding_ref"] in audit_reverse
        }
        finding_alias = item["alias"]
        errors: list[str] = []
        if output.get("section_id") != section["section_id"] or output.get("title") != section["title"]:
            errors.append("section_id and title must match the frozen outline")
        claim_ids = {claim.get("claim_id") for claim in output.get("claims") or []}
        referenced = set()
        metric_reverse = {
            ref: alias
            for alias, ref in state["risk_alias_registry"]["metric_alias_to_ref"].items()
        }
        allowed_metrics = {
            metric_reverse[ref]
            for ref in item.get("metric_refs") or []
            if ref in metric_reverse
        }
        for paragraph in output.get("paragraphs") or []:
            referenced.update(paragraph.get("claim_ids") or [])
            if _LOCAL_ALIAS.search(str(paragraph.get("text") or "")):
                errors.append("paragraph exposes a local alias")
        if referenced - claim_ids or claim_ids - referenced:
            errors.append("every Risk Claim must be referenced by a paragraph")
        for claim in output.get("claims") or []:
            if _LOCAL_ALIAS.search(str(claim.get("text") or "")):
                errors.append(f"Claim {claim.get('claim_id')} exposes a local alias")
            if set(claim.get("evidence_refs") or []) - allowed_evidence_aliases:
                errors.append(f"Claim {claim.get('claim_id')} references unknown Evidence alias")
            if set(claim.get("investigation_finding_refs") or []) - {finding_alias}:
                errors.append(f"Claim {claim.get('claim_id')} references unknown InvestigationFinding alias")
            if set(claim.get("audit_finding_refs") or []) - allowed_audits:
                errors.append(f"Claim {claim.get('claim_id')} references unknown AuditFinding alias")
            if set(claim.get("metric_refs") or []) - allowed_metrics:
                errors.append(f"Claim {claim.get('claim_id')} references unknown metric")
            if claim.get("claim_type") in {ClaimType.DOMAIN_FACT.value, ClaimType.SYNTHESIS.value}:
                if finding_alias not in set(claim.get("investigation_finding_refs") or []):
                    errors.append(f"Claim {claim.get('claim_id')} must cite {finding_alias}")
                if not claim.get("audit_finding_refs"):
                    errors.append(f"Claim {claim.get('claim_id')} must cite an AuditFinding")
                if not claim.get("evidence_refs") and allowed_evidence_aliases:
                    errors.append(f"Claim {claim.get('claim_id')} must cite allowed direct Evidence")
        return errors

    def _map_risk_section(self, output: dict[str, Any], section: dict[str, Any], item: dict[str, Any], state: ReportGraphState) -> dict[str, Any]:
        evidence_map = state["risk_alias_registry"]["evidence_alias_to_ref"]
        audit_map = state["risk_alias_registry"]["audit_finding_alias_to_ref"]
        metric_map = state["risk_alias_registry"]["metric_alias_to_ref"]
        claims = []
        representative_refs = [
            membership["post_ref"]
            for membership in item.get("post_memberships") or []
            if membership.get("is_representative")
        ]
        snapshot = self._snapshot(state["report_version_id"])
        representative_titles = [
            str(snapshot.post_by_ref[ref].payload.get("title") or snapshot.post_by_ref[ref].payload.get("caption") or "未命名帖子")
            for ref in representative_refs
        ]
        if representative_titles:
            claims.append({
                "claim_id": f"{output['section_id']}-representatives",
                "claim_type": ClaimType.METHODOLOGY.value,
                "text": "本节代表帖子：" + "；".join(representative_titles),
                "finding_ids": [],
                "evidence_ids": [],
                "metric_refs": [],
                "support_type": "aggregate",
            })
        for claim in output["claims"]:
            finding_ids = [audit_map[ref] for ref in claim.get("audit_finding_refs") or []]
            evidence_ids = [evidence_map[ref] for ref in claim.get("evidence_refs") or []]
            claims.append({
                "claim_id": claim["claim_id"],
                "claim_type": claim["claim_type"],
                "text": claim["text"],
                "finding_ids": finding_ids,
                "evidence_ids": evidence_ids,
                "metric_refs": [metric_map[ref] for ref in claim.get("metric_refs") or []],
                "support_type": claim.get("support_type") or "direct",
            })
        draft = {
            "section_id": output["section_id"], "section_kind": SectionKind.RISK_ANALYSIS.value,
            "title": output["title"], "purpose": section["purpose"],
            "paragraphs": (
                ([{"text": "本节代表帖子：" + "；".join(representative_titles), "claim_ids": [f"{output['section_id']}-representatives"]}]
                 if representative_titles else [])
                + [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in output["paragraphs"]]
            ),
            "claims": claims, "case_blocks": list(output.get("case_blocks") or []),
            "is_report_category": True,
        }
        draft["body"] = "\n\n".join(item["text"] for item in draft["paragraphs"])
        return draft

    def _summary_context(self, state: ReportGraphState) -> dict[str, Any]:
        """Project only sources actually used by validated risk sections."""
        registry = state["risk_alias_registry"]
        audit_reverse = {ref: alias for alias, ref in registry["audit_finding_alias_to_ref"].items()}
        evidence_reverse = {ref: alias for alias, ref in registry["evidence_alias_to_ref"].items()}
        metric_reverse = {ref: alias for alias, ref in registry["metric_alias_to_ref"].items()}
        claims: list[dict[str, Any]] = []
        for section in state.get("section_drafts") or []:
            if section.get("section_id") in {"overview", "synthesis", "conclusion"}:
                continue
            for claim in section.get("claims") or []:
                audit_refs = [ref for ref in claim.get("finding_ids") or [] if ref in audit_reverse]
                evidence_refs = [ref for ref in claim.get("evidence_ids") or [] if ref in evidence_reverse]
                metric_refs = [ref for ref in claim.get("metric_refs") or [] if ref in metric_reverse]
                # Representative/methodology Claims intentionally have no
                # formal source and cannot be used by the final model step.
                if not (audit_refs or evidence_refs or metric_refs):
                    continue
                claims.append({
                    "claim_type": claim.get("claim_type"),
                    "text": claim.get("text", ""),
                    "audit_finding_refs": [audit_reverse[ref] for ref in audit_refs],
                    "evidence_refs": [evidence_reverse[ref] for ref in evidence_refs],
                    "metric_refs": [metric_reverse[ref] for ref in metric_refs],
                })
        used_metric_aliases = {
            metric_reverse[ref]
            for claim in claims
            for ref in claim.get("metric_refs") or []
            if ref in metric_reverse
        }
        metrics = [
            {
                "metric_alias": alias,
                "metric_name": item["metric_name"],
                "label": item["label"],
                "value": item["value"],
                "denominator": item["denominator"],
                "group": item.get("group") or {},
            }
            for alias, ref in registry["metric_alias_to_ref"].items()
            for item in state["statistics"]["metrics"]
            if item["metric_key"] == ref and alias in used_metric_aliases
        ]
        return {
            "verified_section_claims": claims,
            "metrics": metrics,
            "boundary_notes": list(SERVER_BOUNDARY_NOTES),
            "allowed_audit_finding_aliases": tuple(sorted({ref for claim in claims for ref in claim["audit_finding_refs"]})),
            "allowed_evidence_aliases": tuple(sorted({ref for claim in claims for ref in claim["evidence_refs"]})),
            "allowed_metric_aliases": tuple(sorted({ref for claim in claims for ref in claim["metric_refs"]})),
        }

    def _draft_synthesis_and_conclusion(self, state: ReportGraphState) -> list[dict[str, Any]]:
        context = self._summary_context(state)
        registry = state["risk_alias_registry"]
        response_model = make_synthesis_conclusion_model(
            audit_finding_aliases=context["allowed_audit_finding_aliases"],
            evidence_aliases=context["allowed_evidence_aliases"],
            metric_aliases=context["allowed_metric_aliases"],
        )
        messages = synthesis_conclusion_messages(
            task_name=self._snapshot(state["report_version_id"]).display_name,
            verified_section_claims=context["verified_section_claims"],
            metrics=context["metrics"],
            boundary_notes=context["boundary_notes"],
        )
        result = self._model_step(
            state=state,
            node_name="draft_synthesis_conclusion",
            section_id="",
            messages=messages,
            response_model=response_model,
            semantic_validator=lambda output: self._validate_summary_output(output, state),
            max_tokens=1800,
        )
        return [self._map_summary_section(result.output, key, state) for key in ("synthesis", "conclusion")]

    def _validate_summary_output(self, output: dict[str, Any], state: ReportGraphState) -> list[str]:
        registry = state["risk_alias_registry"]
        context = self._summary_context(state)
        allowed_audits = set(context["allowed_audit_finding_aliases"])
        allowed_evidence = set(context["allowed_evidence_aliases"])
        allowed_metrics = set(context["allowed_metric_aliases"])
        audit_map = registry["audit_finding_alias_to_ref"]
        evidence_map = registry["evidence_alias_to_ref"]
        snapshot = self._snapshot(state["report_version_id"])
        errors: list[str] = []
        for section_id, paragraphs_key, maximum in (
            ("synthesis", "synthesis_paragraphs", 3),
            ("conclusion", "conclusion_paragraphs", 2),
        ):
            paragraphs = output.get(paragraphs_key) or []
            if not 1 <= len(paragraphs) <= maximum:
                errors.append(f"{paragraphs_key} must contain 1..{maximum} paragraphs")
            for paragraph in paragraphs:
                if _LOCAL_ALIAS.search(str(paragraph.get("text") or "")):
                    errors.append(f"{section_id} paragraph exposes a local alias")
                audit_refs = set(paragraph.get("audit_finding_refs") or [])
                evidence_refs = set(paragraph.get("evidence_refs") or [])
                metric_refs = set(paragraph.get("metric_refs") or [])
                if not (audit_refs or evidence_refs or metric_refs):
                    errors.append(f"{section_id} paragraph lacks an allowed source")
                if audit_refs - allowed_audits:
                    errors.append(f"{section_id} references an AuditFinding not used by a validated section")
                if evidence_refs - allowed_evidence:
                    errors.append(f"{section_id} references Evidence not used by a validated section")
                if metric_refs - allowed_metrics:
                    errors.append(f"{section_id} references a Metric not used by a validated section")
                for evidence_alias in evidence_refs:
                    evidence = snapshot.evidence_by_ref.get(evidence_map.get(evidence_alias, ""))
                    if evidence is None or evidence.support_type != "direct":
                        errors.append(f"{section_id} references non-direct/unknown Evidence: {evidence_alias}")
                    elif not audit_refs or evidence.finding_ref not in {audit_map.get(alias) for alias in audit_refs}:
                        errors.append(f"{section_id} Evidence parent is not cited by the same paragraph: {evidence_alias}")
        return errors

    def _map_summary_section(self, output: dict[str, Any], section_id: str, state: ReportGraphState) -> dict[str, Any]:
        registry = state["risk_alias_registry"]
        paragraphs_key = f"{section_id}_paragraphs"
        paragraphs = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in output[paragraphs_key]]
        claims = []
        for ordinal, paragraph in enumerate(paragraphs, 1):
            claim_id = f"{section_id}-paragraph-{ordinal}"
            claims.append({
                "claim_id": claim_id,
                "claim_type": ClaimType.SYNTHESIS.value,
                "text": paragraph["text"],
                "finding_ids": [registry["audit_finding_alias_to_ref"][ref] for ref in paragraph.get("audit_finding_refs") or []],
                "evidence_ids": [registry["evidence_alias_to_ref"][ref] for ref in paragraph.get("evidence_refs") or []],
                "metric_refs": [registry["metric_alias_to_ref"][ref] for ref in paragraph.get("metric_refs") or []],
                "support_type": "direct",
            })
            paragraph["claim_ids"] = [claim_id]
        return {
            "section_id": section_id,
            "section_kind": SectionKind.SYNTHESIS.value if section_id == "synthesis" else SectionKind.CONCLUSION.value,
            "title": "综合研判" if section_id == "synthesis" else "调查结论与建议",
            "purpose": "基于已验证调查发现、确定性指标和资料边界",
            "paragraphs": paragraphs,
            "claims": claims,
            "case_blocks": [],
            "is_report_category": False,
            "body": "\n\n".join(item["text"] for item in paragraphs),
        }

    def _validate_claim_support(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        findings = snapshot.finding_by_ref
        evidence = snapshot.evidence_by_ref
        errors: list[str] = []
        for claim in state["claims"]:
            finding_ids = set(claim.get("finding_ids") or [])
            evidence_ids = set(claim.get("evidence_ids") or [])
            if claim.get("claim_type") == ClaimType.DOMAIN_FACT.value:
                if not finding_ids and not evidence_ids:
                    errors.append(f"Claim lacks frozen Finding or direct Evidence: {claim.get('claim_id')}")
                if not evidence_ids and not all(
                    not any(item.finding_ref == finding_id for item in evidence.values())
                    for finding_id in finding_ids
                ):
                    errors.append(f"Claim lacks direct Evidence for a Finding that has frozen Evidence: {claim.get('claim_id')}")
            elif claim.get("claim_type") == ClaimType.SYNTHESIS.value:
                if not finding_ids and not evidence_ids and not claim.get("metric_refs"):
                    errors.append(f"Synthesis Claim lacks an allowed frozen source: {claim.get('claim_id')}")
                if finding_ids and not evidence_ids and not all(
                    not any(item.finding_ref == finding_id for item in evidence.values())
                    for finding_id in finding_ids
                ):
                    errors.append(f"Synthesis Claim lacks direct Evidence for a Finding that has frozen Evidence: {claim.get('claim_id')}")
            if finding_ids - set(findings):
                errors.append(f"Claim references Finding outside frozen Snapshot: {claim.get('claim_id')}")
            for evidence_id in evidence_ids:
                item = evidence.get(evidence_id)
                if item is None or item.support_type != "direct":
                    errors.append(f"Claim references non-direct/outside Evidence: {claim.get('claim_id')}")
                elif item.finding_ref not in finding_ids:
                    errors.append(f"Evidence parent does not match Claim Finding: {evidence_id}")
        if errors:
            raise ReportValidationError("validate_claim_support", errors)
        return {"claim_validation_errors": []}

    def _validate_citations(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        citation_details: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for claim in state["claims"]:
            finding_ids = set(claim.get("finding_ids") or [])
            for evidence_id in claim.get("evidence_ids") or []:
                item = snapshot.evidence_by_ref.get(evidence_id)
                if item is None or item.support_type != "direct":
                    errors.append(f"Evidence is outside direct frozen Snapshot: {evidence_id}")
                    continue
                if item.finding_ref not in finding_ids:
                    errors.append(f"Evidence parent mismatch: {evidence_id}")
                    continue
                payload = item.payload
                excerpt = str(payload.get("original_text") or payload.get("translated_text") or payload.get("summary") or "")
                if not excerpt and not payload.get("asset_path"):
                    errors.append(f"Evidence has no usable frozen content: {evidence_id}")
                    continue
                citation_details[evidence_id] = {
                    "finding_ids": [item.finding_ref],
                    "citation_excerpt": excerpt[:1000],
                    "structured_content_available": bool(excerpt),
                    "asset_available": bool(payload.get("asset_path")),
                    "asset_status": "available" if payload.get("asset_path") else "not_applicable",
                    "availability": "available",
                }
        if errors:
            raise ReportValidationError("validate_citations", errors)
        return {"citation_validation_errors": [], "citation_details": citation_details}

    def _assemble_report(self, state: ReportGraphState) -> dict[str, Any]:
        manifest = self.store.get_source_snapshot(state["report_version_id"]) or {}
        if manifest.get("task_status") == "interrupted":
            warnings = list(state.get("warnings") or [])
            if not any(item.get("code") == "task_interrupted" for item in warnings):
                warnings.append({
                    "code": "task_interrupted",
                    "message": "当前调查任务状态为interrupted；报告仅覆盖当前冻结的已完成审核内容。",
                })
            state["warnings"] = warnings
        result = super()._assemble_report(state)
        snapshot = self._snapshot(state["report_version_id"])
        finding_map = snapshot.finding_by_ref
        categories = []
        for ordinal, item in enumerate(state["investigation_findings"], 1):
            section_id = f"investigation-{item['alias'].lower()}"
            categories.append({
                "category_ref": f"category:{item['alias'].lower()}",
                "display_ordinal": ordinal,
                "title": item["title"],
                "membership_scope": "report_displayed_posts",
                "membership_complete": False,
                "source_section_id": section_id,
                "members": [
                    {
                        "display_ordinal": index,
                        "post_ref": membership["post_ref"],
                        "finding_ref": membership["audit_finding_ref"],
                    }
                    for index, membership in enumerate(
                        [
                            membership for membership in item["post_memberships"]
                            if membership.get("is_representative")
                        ],
                        1,
                    )
                ],
            })
        body_json = result["assembled_report"]["body_json"]
        body_json["audit_model"]["investigation_findings"] = state["investigation_findings"]
        body_json["audit_model"]["standalone_risk_posts"] = state.get("standalone_risk_posts") or []
        body_json["audit_model"]["risk_post_coverage_complete"] = bool(state.get("risk_post_coverage_complete"))
        body_json["audit_model"]["r2_configuration"] = state["r2_configuration"]
        body_json["audit_model"]["categories"] = categories
        assembled = result["assembled_report"]
        assembled["body_json"] = body_json
        forbidden = set(state["risk_alias_registry"]["post_alias_to_ref"]) | set(state["risk_alias_registry"]["evidence_alias_to_ref"])
        forbidden |= set(state["risk_alias_registry"]["audit_finding_alias_to_ref"])
        forbidden |= {item.ref for item in snapshot.posts} | {item.ref for item in snapshot.findings} | {item.ref for item in snapshot.evidence}
        # The audit_model is a private navigation projection and necessarily
        # contains frozen refs.  Only the rendered human Markdown is checked
        # for leakage into user-visible prose.
        visible = assembled["body_markdown"]
        leaked = [ref for ref in forbidden if ref and ref in visible]
        if leaked:
            raise ReportValidationError("assemble_report", [f"internal reference leaked into presentation: {leaked[:3]}"])
        return {"assembled_report": assembled}

    def _publish_report_version(self, state: ReportGraphState) -> dict[str, Any]:
        if len(state.get("investigation_findings") or []) == 0:
            raise ReportValidationError("publish_report_version", ["no InvestigationFindings"])
        covered = {
            membership["post_ref"]
            for item in state["investigation_findings"]
            for membership in item["post_memberships"]
        }
        covered.update(item["post_ref"] for item in state.get("standalone_risk_posts") or [])
        if not state.get("risk_post_coverage_complete") or covered != set(
            state["risk_alias_registry"]["post_alias_to_ref"].values()
        ):
            raise ReportValidationError("publish_report_version", ["not all risk Posts are covered"])
        assembled = state["assembled_report"]
        categories = assembled["body_json"]["audit_model"]["categories"]
        version = self.store.publish_version(
            report_version_id=state["report_version_id"],
            title=assembled["title"], body_markdown=assembled["body_markdown"],
            body_json=assembled["body_json"], sections=state["section_drafts"],
            citation_details=state.get("citation_details") or [], categories=categories,
            investigation_findings=state["investigation_findings"],
            standalone_risk_posts=state.get("standalone_risk_posts") or [],
        )
        self.store.update_run(state["run_id"], status="completed", current_node="publish_report_version", warnings=state.get("warnings") or [])
        self.store.record_run_event(state["run_id"], "publish_report_version", "published", {"report_version_id": state["report_version_id"]})
        assembled["content_hash"] = version["content_hash"]
        return {"assembled_report": assembled}
