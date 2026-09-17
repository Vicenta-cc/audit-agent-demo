import { apiRequest } from "./apiClient";
import { waitForTurn, type InvestigationTurnWaitOptions } from "./investigations";
import type {
  HistoricalReportTurnAccepted,
  HistoricalReportTurnStatus,
  HistoricalReportWorkspace,
  HistoricalReportWorkspaceList
} from "../types/historicalReports";

function workspacePath(workspaceId: string) {
  return `/api/historical-report-workspaces/${encodeURIComponent(workspaceId)}`;
}

export function fetchHistoricalReportWorkspaces() {
  return apiRequest<HistoricalReportWorkspaceList>(
    "/api/historical-report-workspaces"
  );
}

export function fetchHistoricalReportWorkspace(workspaceId: string) {
  return apiRequest<HistoricalReportWorkspace>(workspacePath(workspaceId));
}

export function sendHistoricalReportTurn(
  workspaceId: string,
  request: { clientMessageId: string; content: string }
) {
  return apiRequest<HistoricalReportTurnAccepted>(
    `${workspacePath(workspaceId)}/turns`,
    {
      method: "POST",
      body: JSON.stringify({
        client_message_id: request.clientMessageId,
        content: request.content
      })
    }
  );
}

export function resumeHistoricalReportTurn(workspaceId: string, turnId: string) {
  return apiRequest<HistoricalReportTurnAccepted>(
    `${workspacePath(workspaceId)}/turns/${encodeURIComponent(turnId)}/resume`,
    { method: "POST" }
  );
}

export function waitForHistoricalReportTurn(
  workspaceId: string,
  turnId: string,
  options: InvestigationTurnWaitOptions = {}
) {
  const turnPath = `${workspacePath(workspaceId)}/turns/${encodeURIComponent(turnId)}`;
  return waitForTurn(
    turnId,
    { statusPath: turnPath, eventPath: `${turnPath}/events` },
    options
  ) as Promise<HistoricalReportTurnStatus>;
}
