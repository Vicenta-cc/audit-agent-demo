"""Fixture-only helpers retained for deterministic planner oracle validation.

This module deliberately contains no planner/runtime implementation. It only
expands the grouped JSON shape used by the archived shadow evaluation fixtures.
"""

from __future__ import annotations

from typing import Any


def expanded_suites(fixture: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    expanded: dict[str, list[dict[str, Any]]] = {}
    for suite_name, payload in fixture["suites"].items():
        if isinstance(payload, list):
            expanded[suite_name] = [dict(case) for case in payload]
            continue
        groups = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            raise ValueError(f"suite {suite_name} must be a case list or grouped suite")
        cases: list[dict[str, Any]] = []
        for group in groups:
            base_context = dict(group["context"])
            base_expected = dict(group["expected"])
            base_tags = [str(tag) for tag in group.get("tags", [])]
            for item in group["cases"]:
                cases.append(
                    {
                        "id": item["id"],
                        "message": item["message"],
                        "slice": str(group["slice"]),
                        "context": {**base_context, **item.get("context", {})},
                        "expected": {**base_expected, **item.get("expected", {})},
                        "tags": sorted(
                            set(base_tags + [str(tag) for tag in item.get("tags", [])])
                        ),
                    }
                )
        expanded[suite_name] = cases
    return expanded


_expanded_suites = expanded_suites

