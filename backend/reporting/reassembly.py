"""Reuse verified published prose through the standard deterministic graph nodes."""
from copy import deepcopy

from backend.domain.identity import stable_hash
from backend.reporting.archive_source import ReadOnlyReportStore
from backend.reporting.errors import ReportValidationError
from backend.reporting.r31_graph import AccountOverviewReportGraph


class NoInferenceClient:
    model = "archived-content-reuse"
    prompt_version = "report-r3.1-archived-reassembly/v1"

    def generate_structured(self, **kwargs):
        raise RuntimeError("Model inference is forbidden for archived reassembly")


def reassemble_archived_report(*, source, store, checkpoint_path, on_created=None):
    original = ReadOnlyReportStore(source.db_path).get_version(source.report_version_id)
    audit = deepcopy(original["body"]["audit_model"])
    if audit["source_snapshot_hash"] != source.snapshot.snapshot_hash:
        raise ValueError("saved prose belongs to another snapshot")
    graph = AccountOverviewReportGraph(
        source=source, store=store, model_client=NoInferenceClient(),
        checkpoint_path=checkpoint_path,
    )
    generation = store.create_generation(
        source.snapshot.task_id, model=NoInferenceClient.model,
        prompt_version=NoInferenceClient.prompt_version,
    )
    state = {**generation, "warnings": audit.get("warnings") or []}
    graph._on_generation_created(generation)
    if on_created:
        on_created(generation)

    def node(name, function=None):
        state.update(graph._wrap_node(name, function or getattr(graph, "_" + name))(state))

    def restore(_state):
        deterministic = {"data-overview", "content-comment-scale", "review-risk-levels",
                         "coverage-boundaries", "investigation-findings", "account-activity-overview"}
        sections = [s for s in audit["sections"] if s["section_id"] not in deterministic
                    and not (s["section_id"] == "standalone-risk-posts"
                             and not audit.get("standalone_risk_posts") and not s.get("claims"))]
        metrics = {m["metric_key"]: m for m in state["statistics"]["metrics"]}
        for saved in audit["metrics"]:
            current = metrics.get(saved["metric_key"])
            if current is None or any(current[k] != saved[k] for k in (
                "value", "denominator", "denominator_name", "group", "filters", "percentage_basis"
            )):
                raise ReportValidationError("restore_saved_content", ["archived metric changed"])
        store.record_run_event(state["run_id"], "restore_saved_content", "reused", {
            "source_report_version_id": source.report_version_id,
            "source_content_hash": original["content_hash"],
            "saved_sections_hash": stable_hash(audit["sections"]), "provider_calls": 0,
        })
        return {
            "outline": {"report_title": original["title"]},
            "section_drafts": sections,
            "claims": [c for section in sections for c in section.get("claims", [])],
            "retained_data_sections": {s["section_id"]: s for s in audit["sections"]
                                       if s["section_id"] in deterministic - {"account-activity-overview"}},
            "investigation_findings": audit["investigation_findings"],
            "standalone_risk_posts": audit.get("standalone_risk_posts") or [],
            "risk_post_coverage_complete": audit["risk_post_coverage_complete"],
            "warnings": audit.get("warnings") or [],
        }

    def verify_preservation(_state):
        assembled = state["assembled_report"]
        after = {s["section_id"]: s for s in assembled["structured_sections"]}
        errors = []
        for before in audit["sections"]:
            if before["section_id"] == "account-activity-overview":
                continue
            section = after.get(before["section_id"], {})
            for key in ("paragraphs", "claims", "case_blocks"):
                if before.get(key, []) != section.get(key, []):
                    errors.append(f"saved {key} changed: {before['section_id']}")
        if errors:
            raise ReportValidationError("verify_preserved_content", errors)
        assembled["body_json"]["audit_model"]["reassembly"] = {
            "source_report_version_id": source.report_version_id,
            "source_content_hash": original["content_hash"],
            "preserved_sections_hash": stable_hash(audit["sections"]),
            "provider_calls": 0,
        }
        return {"assembled_report": assembled}

    try:
        for name in ("freeze_source_snapshot", "build_statistics", "build_report_account_entries", "prepare_risk_inputs"):
            node(name)
        node("restore_saved_content", restore)
        for name in ("validate_numbers", "validate_claim_support", "validate_citations", "assemble_report"):
            node(name)
        node("verify_preserved_content", verify_preservation)
        node("publish_report_version")
        return graph._result_from_state(state)
    finally:
        graph.close()
