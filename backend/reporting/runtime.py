"""Formal R3.1 read and generation entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.audit_agent.config import settings
from backend.reporting.integration_source import CanonicalReportSource
from backend.reporting.r31_graph import AccountOverviewReportGraph
from backend.reporting.store import ReportStore


class R31ReportRuntime:
    def __init__(
        self,
        store: ReportStore,
        *,
        graph_factory: Callable[..., Any] = AccountOverviewReportGraph,
    ) -> None:
        self.store = store
        self.graph_factory = graph_factory

    def get_frontend_report(self, report_version_id: str) -> dict[str, Any]:
        return self.store.get_frontend_report(report_version_id)

    def generation_graph(
        self,
        *,
        source: CanonicalReportSource | None = None,
        model_client: Any | None = None,
        checkpoint_path: Path | None = None,
    ) -> Any:
        canonical_source = source or CanonicalReportSource(
            settings.data_dir / "audit_index.sqlite3",
            settings.outputs_dir,
        )
        return self.graph_factory(
            source=canonical_source,
            store=self.store,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
        )

    def generate(
        self,
        task_id: str,
        *,
        source: CanonicalReportSource | None = None,
        model_client: Any | None = None,
        checkpoint_path: Path | None = None,
    ) -> Any:
        graph = self.generation_graph(
            source=source,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
        )
        try:
            return graph.generate(task_id)
        finally:
            graph.close()
