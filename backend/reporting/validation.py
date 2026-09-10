from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.audit_agent.config import settings
from backend.domain.query_service import DomainQueryService
from backend.reporting.errors import PublishedReportImmutableError, ReportGenerationError
from backend.reporting.contracts import HumanReportDTO
from backend.reporting.graph import (
    CONCLUSION_ABSENCE_PATTERNS,
    FORBIDDEN_PRESENTATION_PATTERNS,
    NODE_ORDER,
    ReportGenerationGraph,
    UNEXPLAINED_ENGLISH_PATTERN,
    UNSUPPORTED_INTENT_PATTERNS,
)
from backend.reporting.store import ReportStore


DEFAULT_TASK_ID = "2272c3692807"


class Stage2AcceptanceRunner:
    def __init__(
        self,
        *,
        query_service: DomainQueryService | None = None,
        store: ReportStore | None = None,
        checkpoint_path: Path | None = None,
        output_dir: Path | None = None,
    ):
        self.query_service = query_service or DomainQueryService()
        self.store = store or ReportStore()
        self.checkpoint_path = checkpoint_path or (
            settings.data_dir / "report_checkpoints.sqlite3"
        )
        self.output_dir = output_dir or (settings.outputs_dir / "reports")

    def run(self, task_id: str, *, generate: bool, fail_section_index: int = 3) -> dict[str, Any]:
        generation_log: list[dict[str, Any]] = []
        first_hash_before_second = ""
        if generate:
            published = self._published_versions(task_id)
            if not published:
                first = self._resume_draft_or_generate(task_id)
                generation_log.append(
                    {"action": "published_first_version", **first.model_dump(mode="json")}
                )
                published = self._published_versions(task_id)
            if len(published) == 1:
                first_hash_before_second = str(published[0]["content_hash"])
                second = self._generate_with_recovery(task_id, fail_section_index)
                generation_log.append(
                    {
                        "action": "published_second_version_after_recovery",
                        **second.model_dump(mode="json"),
                    }
                )
            elif len(published) > 2:
                generation_log.append(
                    {
                        "action": "reused_existing_versions",
                        "published_version_count": len(published),
                    }
                )

        result = self.validate(task_id)
        if generation_log:
            result["generation_log"].extend(generation_log)
        if first_hash_before_second:
            first_after = self._published_versions(task_id)[0]
            result["immutability"]["version_1_hash_before_version_2"] = first_hash_before_second
            result["immutability"]["version_1_hash_after_version_2"] = first_after[
                "content_hash"
            ]
            result["immutability"]["unchanged_during_second_generation"] = (
                first_hash_before_second == first_after["content_hash"]
            )
            if not result["immutability"]["unchanged_during_second_generation"]:
                result["errors"].append("Version 1 changed while Version 2 was generated")
                result["status"] = "failed"

        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_paths = []
        for version in self._published_versions(task_id):
            path = self.output_dir / f"{task_id}-v{version['version_number']}.md"
            path.write_text(str(version["body_markdown"]), encoding="utf-8")
            report_paths.append(str(path.resolve()))
        result["report_paths"] = report_paths
        validation_path = self.output_dir / f"{task_id}-stage2-validation.json"
        result["validation_path"] = str(validation_path.resolve())
        validation_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return result

    def validate(self, task_id: str) -> dict[str, Any]:
        errors: list[str] = []
        report = self.store.get_report_for_task(task_id)
        if report is None:
            raise ReportGenerationError(f"logical Report does not exist for task {task_id}")
        versions = self.store.list_versions(report["id"])
        published = [item for item in versions if item["status"] == "published"]
        if len(published) < 2:
            raise ReportGenerationError(
                f"two published versions are required, found {len(published)}"
            )

        live_snapshot = self.query_service.get_task_snapshot(task_id).data
        expected = {
            "content_count": live_snapshot.content_count,
            "audit_result_count": live_snapshot.audit_result_count,
            "decision_distribution": dict(live_snapshot.decision_distribution),
            "risk_level_distribution": dict(live_snapshot.risk_level_distribution),
            "evidence_count": sum(
                len(item.evidence_ids) for item in live_snapshot.statistic_inputs
            ),
        }
        version_results = []
        all_chains = []
        full_versions: list[dict[str, Any]] = []
        for version in published:
            full = self.store.get_full_version(version["id"])
            if full is None:
                errors.append(f"version disappeared: {version['id']}")
                continue
            full_versions.append(full)
            version_result, chains, version_errors = self._validate_version(
                full, live_snapshot, expected
            )
            version_results.append(version_result)
            all_chains.extend(chains)
            errors.extend(version_errors)

        immutable_enforced = False
        try:
            self.store.assert_published_immutable(published[0]["id"])
        except PublishedReportImmutableError:
            immutable_enforced = True
        if not immutable_enforced:
            errors.append("published Version 1 accepted an in-place UPDATE")

        second_run = self.store.get_run(published[1]["generation_run_id"])
        recovery = self._recovery_result(second_run)
        if not recovery["passed"]:
            errors.append("Version 2 recovery trace does not prove checkpoint/model-step reuse")

        randomizer = random.Random(2272)
        sample_size = min(5, len(all_chains))
        sampled_chains = randomizer.sample(all_chains, sample_size) if sample_size else []
        if sample_size < 3:
            errors.append(f"fewer than three traceable domain Claim chains: {sample_size}")

        first_before_second = published[0]["published_at"] < published[1]["published_at"]
        distinct_hashes = published[0]["content_hash"] != published[1]["content_hash"]
        if not first_before_second:
            errors.append("Version 1 was not published before Version 2")
        if not distinct_hashes:
            errors.append("Version 1 and Version 2 unexpectedly have the same content hash")

        latest_full = full_versions[-1] if full_versions else None
        human_readability = self._validate_human_report(latest_full)
        if human_readability["status"] == "failed":
            errors.extend(human_readability["errors"])

        generation_log = []
        for version in published:
            run = self.store.get_run(version["generation_run_id"]) or {}
            steps = self.store.list_model_steps(version["id"])
            token_usage = Counter()
            for step in steps:
                if step["status"] != "succeeded":
                    continue
                for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = step["usage"].get(field)
                    if isinstance(value, (int, float)):
                        token_usage[field] += value
            generation_log.append(
                {
                    "report_version_id": version["id"],
                    "version_number": version["version_number"],
                    "run_id": version["generation_run_id"],
                    "run_status": run.get("status", ""),
                    "model": version["model"],
                    "prompt_version": version["prompt_version"],
                    "successful_model_steps": sum(
                        item["status"] == "succeeded" for item in steps
                    ),
                    "failed_model_step_records": sum(
                        item["status"] == "failed" for item in steps
                    ),
                    "recorded_token_usage": dict(token_usage),
                    "published_at": version["published_at"],
                    "content_hash": version["content_hash"],
                }
            )

        return {
            "status": "passed" if not errors else "failed",
            "task_id": task_id,
            "logical_report_id": report["id"],
            "logical_report_count_for_task": 1,
            "graph_nodes": list(NODE_ORDER),
            "expected_domain_statistics": expected,
            "published_version_count": len(published),
            "versions_checked": version_results,
            "sampled_claim_chains": sampled_chains,
            "immutability": {
                "database_trigger_enforced": immutable_enforced,
                "version_1_published_before_version_2": first_before_second,
                "version_hashes_distinct": distinct_hashes,
                "version_1_content_hash": published[0]["content_hash"],
                "version_2_content_hash": published[1]["content_hash"],
            },
            "recovery": recovery,
            "human_readability": human_readability,
            "generation_log": generation_log,
            "errors": errors,
        }

    def _validate_version(
        self,
        full: dict[str, Any],
        live_snapshot: Any,
        expected: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        errors = []
        snapshot = full["source_snapshot"]
        if snapshot["task_id"] != live_snapshot.task_id:
            errors.append(f"snapshot task mismatch in {full['id']}")
        if len(snapshot["finding_ids"]) != live_snapshot.audit_result_count:
            errors.append(f"snapshot Finding count mismatch in {full['id']}")
        if len(snapshot["evidence_ids"]) != expected["evidence_count"]:
            errors.append(f"snapshot Evidence count mismatch in {full['id']}")

        missing_findings = []
        for finding_id in snapshot["finding_ids"]:
            try:
                self.query_service.get_finding_detail(finding_id)
            except Exception as exc:
                missing_findings.append({"finding_id": finding_id, "error": str(exc)})
        missing_evidence = []
        evidence_availability = Counter()
        evidence_quality = Counter()
        for evidence_id in snapshot["evidence_ids"]:
            try:
                detail = self.query_service.get_evidence_detail(evidence_id).data
            except Exception as exc:
                missing_evidence.append({"evidence_id": evidence_id, "error": str(exc)})
                continue
            evidence_availability[detail.availability.value] += 1
            evidence_quality.update(item.value for item in detail.data_quality)
        if missing_findings:
            errors.append(f"missing Findings in {full['id']}: {len(missing_findings)}")
        if missing_evidence:
            errors.append(f"missing Evidence in {full['id']}: {len(missing_evidence)}")

        audit_body = full["body"].get("audit_model") or full["body"]
        metric_comparison = self._compare_metrics(audit_body.get("metrics") or [], expected)
        if not metric_comparison["passed"]:
            errors.extend(
                f"{full['id']}: {item}" for item in metric_comparison["errors"]
            )

        findings_by_claim: dict[str, list[str]] = {}
        for row in full["claim_findings"]:
            findings_by_claim.setdefault(row["claim_id"], []).append(row["finding_id"])
        evidence_by_claim: dict[str, list[str]] = {}
        for row in full["claim_evidence"]:
            evidence_by_claim.setdefault(row["claim_id"], []).append(row["evidence_id"])
        claims_by_id = {item["id"]: item for item in full["claims"]}

        chains = []
        for claim_id, evidence_ids in evidence_by_claim.items():
            for evidence_id in evidence_ids:
                for finding_id in findings_by_claim.get(claim_id, []):
                    link = self.query_service.validate_finding_evidence_link(
                        finding_id, evidence_id
                    ).data
                    if not link.valid:
                        continue
                    detail = self.query_service.get_evidence_detail(evidence_id).data
                    claim = claims_by_id[claim_id]
                    chains.append(
                        {
                            "report_version_id": full["id"],
                            "report_claim_id": claim_id,
                            "local_claim_id": claim["local_claim_id"],
                            "claim_text": claim["text"],
                            "finding_id": finding_id,
                            "evidence_id": evidence_id,
                            "evidence_type": detail.evidence_type.value,
                            "evidence_availability": detail.availability.value,
                            "evidence_preview": (
                                detail.original_text
                                or detail.translated_text
                                or detail.summary
                            )[:240],
                            "link_valid": True,
                        }
                    )
                    break

        required_claims = [
            claim
            for claim in full["claims"]
            if claim["claim_type"] in {"domain_fact", "synthesis"}
        ]
        traced_claim_ids = {item["report_claim_id"] for item in chains}
        untraced_claims = [item["id"] for item in required_claims if item["id"] not in traced_claim_ids]
        if untraced_claims:
            errors.append(
                f"untraceable domain Claims in {full['id']}: {len(untraced_claims)}"
            )

        warnings = list(snapshot["data_quality_warnings"])
        warnings.extend(audit_body.get("warnings") or [])
        quality_codes = sorted(
            {
                str(item.get("code") or "")
                for item in warnings
                if isinstance(item, dict) and item.get("code")
            }
        )
        return (
            {
                "report_version_id": full["id"],
                "version_number": full["version_number"],
                "status": full["status"],
                "content_hash": full["content_hash"],
                "source_snapshot_id": snapshot["id"],
                "source_snapshot_hash": snapshot["snapshot_hash"],
                "source_hash_matches_current_domain_view": (
                    snapshot["source_hash"] == live_snapshot.source_hash
                ),
                "finding_count": len(snapshot["finding_ids"]),
                "evidence_count": len(snapshot["evidence_ids"]),
                "section_count": len(full["sections"]),
                "claim_count": len(full["claims"]),
                "domain_claim_count": len(required_claims),
                "traceable_chain_count": len(chains),
                "missing_findings": missing_findings,
                "missing_evidence": missing_evidence,
                "metric_comparison": metric_comparison,
                "evidence_availability": dict(sorted(evidence_availability.items())),
                "evidence_data_quality": dict(sorted(evidence_quality.items())),
                "data_quality_warning_codes": quality_codes,
                "degraded_data_present": any(
                    item != "complete" for item in evidence_quality
                ),
            },
            chains,
            errors,
        )

    @staticmethod
    def _validate_human_report(full: dict[str, Any] | None) -> dict[str, Any]:
        if not full:
            return {"status": "not_available", "errors": []}
        raw = full.get("body", {}).get("human_report")
        if not isinstance(raw, dict):
            return {
                "status": "not_available",
                "report_version_id": full.get("id", ""),
                "errors": [],
            }

        errors = []
        try:
            report = HumanReportDTO.model_validate(raw)
        except Exception as exc:
            return {
                "status": "failed",
                "report_version_id": full.get("id", ""),
                "errors": [f"Human Report DTO is invalid: {exc}"],
            }

        markdown = str(full.get("body_markdown") or "")
        for pattern in FORBIDDEN_PRESENTATION_PATTERNS:
            if pattern.search(markdown):
                errors.append(
                    f"human report Markdown exposes internal token: {pattern.pattern}"
                )
        for forbidden in (
            "可核验声明",
            "## 典型证据说明",
            "## 数据质量/覆盖说明",
            "## 来源与数据质量",
        ):
            if forbidden in markdown:
                errors.append(f"human report Markdown contains engineering block: {forbidden}")
        for match in re.finditer(r"(?<![A-Za-z0-9_:])\d+\.(\d+)%?", markdown):
            decimals = match.group(1)
            if len(decimals) > 1 or set(decimals) == {"0"}:
                errors.append(
                    f"human report Markdown contains non-human numeric format: {match.group(0)}"
                )
        for pattern in CONCLUSION_ABSENCE_PATTERNS:
            if pattern.search(report.conclusion.text):
                errors.append("human report conclusion contradicts its supplied findings")
                break
        if re.sub(r"\s+", "", report.conclusion.text) == re.sub(
            r"\s+", "", report.summary.text
        ):
            errors.append("human report conclusion copies the overview")
        if not re.search(
            r"建议|后续|应当|应重点|需重点|持续关注", report.conclusion.text
        ):
            errors.append("human report conclusion has no concrete follow-up focus")
        if UNEXPLAINED_ENGLISH_PATTERN.search(markdown):
            errors.append("human report Markdown contains unexplained English prose")
        for pattern in UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(markdown):
                errors.append("human report infers publisher intent beyond its sources")
                break
        if not report.summary.text.strip():
            errors.append("human report summary is empty")
        if not report.conclusion.text.strip():
            errors.append("human report conclusion is empty")
        if not report.case_blocks:
            errors.append("human report has no case blocks")
        if report.case_blocks and "【查看相关证据】" not in markdown:
            errors.append("human report case blocks have no evidence action label")

        dto_claim_ids = set(report.summary.claim_ids)
        dto_claim_ids.update(report.conclusion.claim_ids)
        dto_claim_ids.update(report.data_quality_note.claim_ids)
        for section in report.sections:
            for paragraph in section.paragraphs:
                dto_claim_ids.update(paragraph.claim_ids)
        action_claim_ids = set()
        action_count = 0
        for case in report.case_blocks:
            dto_claim_ids.update(case.claim_ids)
            action_claim_ids.update(item.claim_id for item in case.citation_actions)
            action_count += len(case.citation_actions)
        stored_claim_ids = {item["id"] for item in full.get("claims") or []}
        missing_claim_ids = sorted(dto_claim_ids - stored_claim_ids)
        if missing_claim_ids:
            errors.append(
                f"Human Report DTO references missing Claims: {missing_claim_ids}"
            )
        if not action_claim_ids:
            errors.append("human report has no evidence drawer Claim actions")
        for claim_id in stored_claim_ids:
            if claim_id in markdown:
                errors.append("human report Markdown exposes a persisted Claim ID")
                break

        return {
            "status": "passed" if not errors else "failed",
            "report_version_id": full.get("id", ""),
            "presentation_version": report.presentation_version,
            "section_count": len(report.sections),
            "case_block_count": len(report.case_blocks),
            "key_metric_count": len(report.key_metrics),
            "referenced_claim_count": len(dto_claim_ids),
            "evidence_action_count": action_count,
            "evidence_action_claim_count": len(action_claim_ids),
            "errors": errors,
        }

    @staticmethod
    def _compare_metrics(metrics: list[dict[str, Any]], expected: dict[str, Any]) -> dict[str, Any]:
        errors = []
        scalar_by_label = {
            item["label"]: item["value"]
            for item in metrics
            if item.get("metric_name") == "scalar"
        }
        if scalar_by_label.get("任务内容总数") != float(expected["content_count"]):
            errors.append("content_count metric mismatch")
        if scalar_by_label.get("审核结果总数") != float(expected["audit_result_count"]):
            errors.append("audit_result_count metric mismatch")

        decision = {
            item["group"]["decision"]: int(item["value"])
            for item in metrics
            if item.get("metric_name") == "count" and "decision" in (item.get("group") or {})
        }
        risk = {
            item["group"]["risk_level"]: int(item["value"])
            for item in metrics
            if item.get("metric_name") == "count" and "risk_level" in (item.get("group") or {})
        }
        if decision != expected["decision_distribution"]:
            errors.append(f"decision distribution mismatch: {decision}")
        if risk != expected["risk_level_distribution"]:
            errors.append(f"risk level distribution mismatch: {risk}")
        return {
            "passed": not errors,
            "actual": {
                "content_count": scalar_by_label.get("任务内容总数"),
                "audit_result_count": scalar_by_label.get("审核结果总数"),
                "decision_distribution": decision,
                "risk_level_distribution": risk,
            },
            "expected": expected,
            "errors": errors,
        }

    def _recovery_result(self, run: dict[str, Any] | None) -> dict[str, Any]:
        if not run:
            return {"passed": False, "reason": "Version 2 generation run is missing"}
        events = self.store.list_run_events(run["id"])
        node_starts = Counter(
            item["node_name"] for item in events if item["event"] == "started"
        )
        reused_sections = [
            item["detail"].get("section_id", "")
            for item in events
            if item["event"] == "model_step_reused"
        ]
        failed_sections = [
            item for item in events if item["event"] == "failed" and item["node_name"] == "draft_sections"
        ]
        deterministic_once = all(node_starts[name] == 1 for name in NODE_ORDER[:4])
        passed = (
            run["status"] == "completed"
            and deterministic_once
            and node_starts["draft_sections"] == 2
            and len(reused_sections) >= 3
            and bool(failed_sections)
        )
        return {
            "passed": passed,
            "run_id": run["id"],
            "run_status": run["status"],
            "node_start_counts": dict(node_starts),
            "failed_draft_attempts": len(failed_sections),
            "reused_section_ids": reused_sections,
            "deterministic_prefix_executed_once": deterministic_once,
        }

    def _resume_draft_or_generate(self, task_id: str):
        run = self.store.get_latest_run_for_task(task_id)
        if run and run["status"] == "recoverable":
            graph = ReportGenerationGraph(
                query_service=self.query_service,
                store=self.store,
                checkpoint_path=self.checkpoint_path,
            )
            try:
                return graph.resume(run["id"])
            finally:
                graph.close()
        graph = ReportGenerationGraph(
            query_service=self.query_service,
            store=self.store,
            checkpoint_path=self.checkpoint_path,
        )
        try:
            return graph.generate(task_id)
        finally:
            graph.close()

    def _generate_with_recovery(self, task_id: str, fail_section_index: int):
        graph = ReportGenerationGraph(
            query_service=self.query_service,
            store=self.store,
            checkpoint_path=self.checkpoint_path,
            fail_once_section_index=fail_section_index,
        )
        try:
            try:
                graph.generate(task_id)
            except ReportGenerationError as exc:
                if "injected one-time failure" not in str(exc):
                    raise
            else:
                raise ReportGenerationError("failure injection did not interrupt Version 2")
            run = self.store.get_latest_run_for_task(task_id)
            if not run or run["status"] != "recoverable":
                raise ReportGenerationError("recoverable Version 2 run was not persisted")
            return graph.resume(run["id"])
        finally:
            graph.close()

    def _published_versions(self, task_id: str) -> list[dict[str, Any]]:
        report = self.store.get_report_for_task(task_id)
        if not report:
            return []
        return [
            item
            for item in self.store.list_versions(report["id"])
            if item["status"] == "published"
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate Stage 2 reports")
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--fail-section-index", type=int, default=3)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    runner = Stage2AcceptanceRunner(output_dir=args.output_dir)
    result = runner.run(
        args.task_id,
        generate=args.generate,
        fail_section_index=args.fail_section_index,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "task_id": result["task_id"],
                "published_version_count": result["published_version_count"],
                "report_paths": result["report_paths"],
                "validation_path": result["validation_path"],
                "errors": result["errors"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
