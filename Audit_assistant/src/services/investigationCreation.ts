import { apiRequest, withQuery } from "./apiClient";
import { waitForTurn, type InvestigationTurnWaitOptions } from "./investigations";
import type {
  ConfirmationPreview,
  InvestigationDraftConfiguration,
  InvestigationRunProjection,
  InvestigationWorkspaceMessage,
  InvestigationWorkspaceSession,
  InvestigationWorkspaceState,
  PublicInvestigationDraft
} from "../types/investigationCreation";
import type {
  InvestigationTurnAcceptedResponse,
  InvestigationTurnResponse
} from "../types/investigations";
import type { PublishedReportDetail } from "../types/reports";

export interface InvestigationWorkspaceListItem {
  workspace_session_id: string;
  run_id?: string;
  title: string;
  updated_at: string;
  run_status: string;
  presentation_stage: string;
}

export async function listInvestigationWorkspaces() {
  const items: InvestigationWorkspaceListItem[] = [];
  let hasMore = true;
  while (hasMore) {
    const page = await apiRequest<{ items: InvestigationWorkspaceListItem[]; has_more: boolean }>(
      withQuery("/api/investigation-workspaces", { limit: 50, offset: items.length })
    );
    items.push(...page.items);
    hasMore = page.has_more && page.items.length > 0;
  }
  return items;
}

export function createInvestigationWorkspace(workspaceKey: string) {
  return apiRequest<InvestigationWorkspaceSession>("/api/investigation-workspaces", {
    method: "POST",
    body: JSON.stringify({ workspace_key: workspaceKey })
  });
}

export function getInvestigationWorkspace(workspaceSessionId: string) {
  return apiRequest<InvestigationWorkspaceSession>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}`
  );
}

export function listInvestigationWorkspaceMessages(workspaceSessionId: string) {
  return apiRequest<InvestigationWorkspaceMessage[]>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}/messages`
  );
}

export function getInvestigationWorkspaceState(workspaceSessionId: string) {
  return apiRequest<InvestigationWorkspaceState>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}/state`
  );
}

export function sendInvestigationCreationTurn(
  workspaceSessionId: string,
  request: { clientMessageId: string; content: string }
) {
  return apiRequest<InvestigationTurnAcceptedResponse>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}/turns`,
    {
      method: "POST",
      body: JSON.stringify({
        client_message_id: request.clientMessageId,
        content: request.content
      })
    }
  );
}

export function waitForInvestigationCreationTurn(
  turnId: string,
  options: InvestigationTurnWaitOptions = {}
) {
  return waitForTurn(
    turnId,
    {
      statusPath: `/api/investigation-workspace-turns/${encodeURIComponent(turnId)}`,
      eventPath: `/api/investigation-workspace-turns/${encodeURIComponent(turnId)}/events`
    },
    options
  );
}

export function resumeInvestigationCreationTurn(turnId: string) {
  return apiRequest<InvestigationTurnAcceptedResponse>(
    `/api/investigation-workspace-turns/${encodeURIComponent(turnId)}/resume`,
    { method: "POST" }
  );
}

export function queryInvestigationOptions(query: {
  domainHint?: string;
  mode?: "search" | "creator";
  platform?: string;
} = {}) {
  return apiRequest(withQuery("/api/investigation-options", {
    domain_hint: query.domainHint,
    mode: query.mode,
    platform: query.platform
  }));
}

export function getInvestigationDraft(draftId: string) {
  return apiRequest<PublicInvestigationDraft>(
    `/api/investigation-drafts/${encodeURIComponent(draftId)}`
  );
}

export function getConfirmationPreview(draftId: string) {
  return apiRequest<ConfirmationPreview>(
    `/api/investigation-drafts/${encodeURIComponent(draftId)}/confirmation-preview`
  );
}

export function generateInvestigationConfirmationPreview(
  workspaceSessionId: string,
  request: { clientMessageId: string; draftId: string; expectedRevision: number }
) {
  return apiRequest<InvestigationTurnResponse>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}/confirmation-preview`,
    {
      method: "POST",
      body: JSON.stringify({
        client_message_id: request.clientMessageId,
        draft_id: request.draftId,
        expected_revision: request.expectedRevision
      })
    }
  );
}

export function updateInvestigationDraft(
  draftId: string,
  request: {
    expectedRevision: number;
    title: string;
    objective: string;
    configuration: InvestigationDraftConfiguration;
  }
) {
  return apiRequest<PublicInvestigationDraft>(
    `/api/investigation-drafts/${encodeURIComponent(draftId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: request.expectedRevision,
        title: request.title,
        objective: request.objective,
        configuration: request.configuration
      })
    }
  );
}

export function confirmAndQueueInvestigation(
  draftId: string,
  request: { expectedRevision: number; idempotencyKey: string; taskSettingsRevision?: number }
) {
  return apiRequest<InvestigationRunProjection>(
    `/api/investigation-drafts/${encodeURIComponent(draftId)}/confirm-and-queue`,
    {
      method: "POST",
      headers: { "Idempotency-Key": request.idempotencyKey },
      body: JSON.stringify({
        expected_revision: request.expectedRevision,
        expected_task_settings_revision: request.taskSettingsRevision,
        confirmed: true
      })
    }
  );
}

export function getInvestigationRun(runId: string) {
  return apiRequest<InvestigationRunProjection>(
    `/api/investigation-runs/${encodeURIComponent(runId)}`
  );
}

export function getInvestigationWorkspacePublishedReport(
  workspaceSessionId: string,
  runId: string
) {
  return apiRequest<PublishedReportDetail>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}`
      + `/runs/${encodeURIComponent(runId)}/published-report`
  );
}

export function sendInvestigationWorkspaceReportTurn(
  workspaceSessionId: string,
  runId: string,
  request: { clientMessageId: string; content: string }
) {
  return apiRequest<{ turn_id: string; status: "running"; updated_at: string }>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}`
      + `/runs/${encodeURIComponent(runId)}/report-turns`,
    {
      method: "POST",
      body: JSON.stringify({
        client_message_id: request.clientMessageId,
        content: request.content
      })
    }
  );
}

export function waitForInvestigationWorkspaceReportTurn(
  workspaceSessionId: string,
  runId: string,
  turnId: string,
  options: InvestigationTurnWaitOptions = {}
) {
  const base = `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}`
    + `/runs/${encodeURIComponent(runId)}/report-turns/${encodeURIComponent(turnId)}`;
  return waitForTurn(
    turnId,
    { statusPath: base, eventPath: `${base}/events` },
    options
  );
}

export function resumeInvestigationWorkspaceReportTurn(
  workspaceSessionId: string,
  runId: string,
  turnId: string
) {
  return apiRequest<{ turn_id: string; status: "running" }>(
    `/api/investigation-workspaces/${encodeURIComponent(workspaceSessionId)}`
      + `/runs/${encodeURIComponent(runId)}/report-turns/${encodeURIComponent(turnId)}/resume`,
    { method: "POST" }
  );
}
