"""Formal R3.1 read and generation entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.audit_agent.config import settings
from backend.reporting.integration_source import CanonicalReportSource
from backend.reporting.r31_graph import AccountOverviewReportGraph
from backend.reporting.pass_graph import PASS_TEMPLATE_VERSION, PassReportGraph
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
        task_id: str | None = None,
        template_version: str | None = None,
    ) -> Any:
        canonical_source = source or CanonicalReportSource(
            settings.data_dir / "audit_index.sqlite3",
            settings.outputs_dir,
        )
        factory = self.graph_factory
        if factory is AccountOverviewReportGraph:
            if template_version == PASS_TEMPLATE_VERSION:
                factory = PassReportGraph
            elif task_id is not None and template_version is None:
                rows = canonical_source.canonical_rows(task_id)
                if rows and all(row["decision"] == "pass" and row["risk_level"] == "none" for row in rows):
                    factory = PassReportGraph
        return factory(
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
        on_generation_created: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        graph = self.generation_graph(
            source=source,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
            task_id=task_id,
        )
        try:
            if on_generation_created is not None:
                graph_hook = graph._on_generation_created

                def notify(generation: dict[str, Any]) -> None:
                    graph_hook(generation)
                    on_generation_created(generation)

                graph._on_generation_created = notify
            return graph.generate(task_id)
        finally:
            graph.close()

    def resume(
        self,
        run_id: str,
        *,
        source: CanonicalReportSource | None = None,
        model_client: Any | None = None,
        checkpoint_path: Path | None = None,
    ) -> Any:
        run = self.store.get_run(run_id)
        version = self.store.get_version(run["report_version_id"]) if run else None
        graph = self.generation_graph(
            source=source,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
            template_version=str(version.get("prompt_version") or "") if version else "",
        )
        try:
            return graph.resume(run_id)
        finally:
            graph.close()
