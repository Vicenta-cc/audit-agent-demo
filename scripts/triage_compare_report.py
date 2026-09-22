"""Compare rank-1 vs triage selection outcomes across jobs run in TRIAGE_MODE=compare."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_selections(outputs_dir: Path, job_id: str) -> list[tuple[str, str]]:
    selections: list[tuple[str, str]] = []
    for path in sorted((outputs_dir / job_id / "crawler").rglob("candidates.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("selected"):
            selections.append((str(payload.get("strategy") or "triage"), str(payload["selected"])))
    return selections


def load_outcomes(job_id: str) -> dict[str, str]:
    from backend.audit_agent.ingestion import AuditResultStore

    results = AuditResultStore().list_results(job_id, limit=100000)
    items = results.get("items") if isinstance(results, dict) else results
    return {str(i.get("content_key") or ""): str(i.get("decision") or "").lower()
            for i in items or [] if i.get("content_key")}


def summarize(selections: list[tuple[str, str]], outcomes: dict[str, str]) -> dict:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"selected": 0, "reject": 0, "review": 0})
    for strategy, key in selections:
        if key not in outcomes:
            continue
        stats[strategy]["selected"] += 1
        if outcomes[key] in {"reject", "review"}:
            stats[strategy][outcomes[key]] += 1
    by_strategy = {}
    for strategy, row in stats.items():
        rate = round(row["reject"] / row["selected"], 4) if row["selected"] else 0.0
        by_strategy[strategy] = {**row, "reject_rate": rate}

    # Calculate lift from exact values (before rounding individual rates)
    triage_selected = stats.get("triage", {}).get("selected", 0)
    triage_reject = stats.get("triage", {}).get("reject", 0)
    rank1_selected = stats.get("rank1", {}).get("selected", 0)
    rank1_reject = stats.get("rank1", {}).get("reject", 0)

    triage_rate = triage_reject / triage_selected if triage_selected else 0.0
    rank1_rate = rank1_reject / rank1_selected if rank1_selected else 0.0
    lift = round(triage_rate / rank1_rate, 4) if rank1_rate else 0.0

    return {"by_strategy": by_strategy, "lift": lift}


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage compare-mode report")
    parser.add_argument("job_ids", nargs="+")
    parser.add_argument("--outputs-dir", default=None)
    args = parser.parse_args()
    from backend.audit_agent.config import settings

    outputs_dir = Path(args.outputs_dir) if args.outputs_dir else settings.outputs_dir
    selections: list[tuple[str, str]] = []
    outcomes: dict[str, str] = {}
    for job_id in args.job_ids:
        selections.extend(load_selections(outputs_dir, job_id))
        outcomes.update(load_outcomes(job_id))
    print(json.dumps(summarize(selections, outcomes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
