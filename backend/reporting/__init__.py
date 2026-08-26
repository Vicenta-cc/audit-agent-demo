"""Versioned, traceable report generation over DomainQueryService."""

from importlib import import_module
from typing import Any

from backend.reporting.store import ReportStore


_LAZY_GRAPH_EXPORTS = {
    "ReportGenerationGraph": "backend.reporting.graph",
    "RiskFindingReportGraph": "backend.reporting.r2_graph",
    "AccountEntryReportGraph": "backend.reporting.r3_graph",
    "AccountOverviewReportGraph": "backend.reporting.r31_graph",
}

__all__ = [
    "AccountEntryReportGraph",
    "AccountOverviewReportGraph",
    "ReportGenerationGraph",
    "RiskFindingReportGraph",
    "ReportStore",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_GRAPH_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
