"""Historical published-report demo workspaces."""

from .catalog import HISTORICAL_REPORT_SPECS, HistoricalReportSpec
from .service import HistoricalReportDemoService
from .store import HistoricalReportWorkspaceStore

__all__ = [
    "HISTORICAL_REPORT_SPECS",
    "HistoricalReportDemoService",
    "HistoricalReportSpec",
    "HistoricalReportWorkspaceStore",
]
