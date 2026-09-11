"""Read an explicitly selected, hash-verified handoff without changing source jobs."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from backend.domain.identity import stable_hash
from backend.reporting.integration_source import CanonicalReportSource, source_sha256


class SelectedReportSource(CanonicalReportSource):
    def __init__(self, handoff: Path, *, task_id: str, title: str, outputs_dir: Path):
        self.handoff = handoff.resolve()
        self.aggregate_task_id = task_id
        self.title = title
        self.summary = json.loads((self.handoff / "source-summary.json").read_text())
        self.selected = json.loads((self.handoff / "selected-results.json").read_text())
        super().__init__(self.handoff / "snapshots/audit_index.sqlite3", outputs_dir)
        self.verification = self.verify()

    def verify(self):
        for relative, expected in json.loads((self.handoff / "snapshot-sha256.json").read_text()).items():
            if source_sha256(self.handoff / relative) != expected:
                raise ValueError(f"snapshot hash mismatch: {relative}")
        ids = [row["note_id"] for row in self.selected]
        if len(ids) != len(set(ids)) or set(ids) != set((self.handoff / "selected-post-ids.txt").read_text().splitlines()):
            raise ValueError("selected post whitelist mismatch")
        counts, decisions, terms, levels = Counter(), Counter(), Counter(), Counter()
        row_extensions = set()
        with self.connect() as connection:
            for item in self.selected:
                for path_key, hash_key in (("result_copy", "result_sha256"), ("raw_item_copy", "raw_sha256")):
                    if source_sha256(Path(item[path_key])) != item[hash_key]:
                        raise ValueError(f"payload hash mismatch: {item['note_id']}")
                result = json.loads(Path(item["result_copy"]).read_text())
                row = connection.execute("SELECT * FROM audit_results WHERE id=? AND job_id=?", (item["audit_result_row"]["id"], item["source_job_id"])).fetchone()
                if row is None or dict(row) != item["audit_result_row"]:
                    raise ValueError("selected audit row disagrees with SQLite snapshot")
                effective = json.loads(row["result_json"])
                # Ingestion adds normalized evidence groups and config provenance.
                if any(effective.get(key) != value for key, value in result.items()):
                    raise ValueError("result file and canonical result disagree")
                row_extensions.update(set(effective) - set(result))
                membership = connection.execute("SELECT * FROM task_contents WHERE id=?", (item["membership"]["id"],)).fetchone()
                if membership is None or membership["analyze_status"] != "completed" or membership["audit_result_id"] != row["id"] or membership["task_id"] != row["job_id"]:
                    raise ValueError("selected membership is not a completed source result")
                stats = result["comment_audit_stats"]
                if stats != item["comment_audit_stats"] or len(result["comments"]) != stats["total"]:
                    raise ValueError("comment coverage mismatch")
                for key in self.summary["comments"]:
                    counts[key] += stats[key]
                decisions[result["decision"]] += 1
                levels[result["risk_level"]] += 1
                terms[item["source_keyword"]] += 1
        if len(ids) != self.summary["selected_posts"] or dict(counts) != self.summary["comments"]:
            raise ValueError("source summary coverage mismatch")
        self.coverage = {"selected_posts": len(ids), "candidate_posts": self.summary["source_membership_total"], "excluded_failed_posts": json.loads((self.handoff / "excluded-failed-posts.json").read_text()), "comments": dict(counts), "decision": dict(decisions), "risk_level": dict(levels), "actual_source_terms": dict(terms)}
        return {"unique_posts": len(ids), "verified_payload_files": len(ids) * 2, "canonical_row_extensions": sorted(row_extensions), "coverage": self.coverage, "manifest_sha256": {name: source_sha256(self.handoff / name) for name in ("source-summary.json", "selected-results.json", "snapshot-sha256.json")}}

    def _require_task(self, task_id):
        if task_id != self.aggregate_task_id:
            raise ValueError("task outside selected report scope")

    def task_name(self, task_id):
        self._require_task(task_id)
        return self.title

    def task_status(self, task_id):
        self._require_task(task_id)
        return "completed"

    def has_explicit_creator_target(self, task_id):
        self._require_task(task_id)
        return False

    def creator_account_identity(self, task_id):
        self._require_task(task_id)
        return None

    def config(self, task_id):
        self._require_task(task_id)
        body = {"source_kind": "selected_existing_audits", "source_jobs": self.summary["configured_jobs"], "coverage": self.coverage, "verification": self.verification}
        return {"id": "aggregate-config:" + stable_hash(body), "version": 1, "config_hash": stable_hash(body), "audit_config": body, "rule_snapshot": self.summary["configured_jobs"][0]["rule_snapshot"], "prompt_profile_snapshot": {"source_profiles": [job["prompt_profile_snapshot"] for job in self.summary["configured_jobs"]]}}

    def canonical_rows(self, task_id):
        self._require_task(task_id)
        return [{**item["audit_result_row"], "tc_raw_item_path": item["raw_item_copy"], "tc_captured_at": item["membership"]["created_at"]} for item in sorted(self.selected, key=lambda x: x["audit_result_row"]["id"])]

    def provenance_for_row(self, row):
        item = next(item for item in self.selected if item["audit_result_row"]["id"] == row["id"])
        return {key: item[key] for key in ("source_job_id", "note_id", "source_keyword", "result_sha256", "raw_sha256", "prompt_version")}
