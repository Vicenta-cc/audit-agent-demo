import type {
  ChatMessage,
  InvestigationSession,
  PlatformCode,
  TaskDraft
} from "../../types/investigation";
import type { HistoricalReportWorkspace } from "../../types/historicalReports";
import type { PublishedReportDetail } from "../../types/reports";
import { buildPublishedReportSummary } from "./publishedReportSession";
import {
  clearHistoricalPendingTurn,
  readHistoricalPendingTurn
} from "./historicalReportPending";
import {
  activityEventsForTurn,
  restoreActivityTimelines
} from "./investigationActivity";

function messageTime(createdAt: string) {
  const value = new Date(createdAt);
  return Number.isNaN(value.getTime())
    ? ""
    : value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function draftFromWorkspace(workspace: HistoricalReportWorkspace): TaskDraft {
  return {
    taskName: workspace.draft.task_name,
    taskType: "历史报告导入",
    subject: workspace.draft.subject,
    platforms: [workspace.draft.platform as PlatformCode],
    keywords: workspace.draft.search_terms,
    matchedRuleSet: workspace.draft.analysis_plan,
    analysisPlanName: workspace.draft.analysis_plan,
    ruleSetDescription: workspace.draft.analysis_description,
    recommendedRecallLexicons: workspace.draft.recall_lexicons,
    scopeDescription: workspace.draft.scope_description,
    historicalConfiguration: workspace.draft.configuration_details,
    historyNotice: workspace.draft.history_notice,
    status: "报告已生成",
    confirmed: true
  };
}

function displayMessage(
  workspace: HistoricalReportWorkspace,
  draft: TaskDraft,
  index: number
): ChatMessage {
  const message = workspace.display_timeline[index];
  const common = {
    id: message.id,
    timestamp: messageTime(message.occurred_at),
    content: message.content
  };
  if (message.kind === "user_request" || message.kind === "user_confirmation") {
    return { ...common, sender: "user" };
  }
  if (message.kind === "plan_recommendation") {
    return {
      ...common,
      sender: "assistant",
      type: "task_proposal",
      proposalData: {
        taskName: draft.taskName,
        taskType: draft.taskType,
        subject: draft.subject,
        matchedRuleSet: draft.matchedRuleSet,
        ruleSetDescription: draft.ruleSetDescription,
        platformsSelected: draft.platforms,
        platformsConfirmed: true,
        interactionMode: "platform-selection"
      }
    };
  }
  return {
    ...common,
    sender: "assistant",
    type: message.kind === "processing_update" ? "historical_progress" : "text"
  };
}

export function buildHistoricalReportSession(
  workspace: HistoricalReportWorkspace,
  report: PublishedReportDetail
): InvestigationSession {
  const draft = draftFromWorkspace(workspace);
  const messages = workspace.display_timeline.map((_, index) => (
    displayMessage(workspace, draft, index)
  ));
  messages.push({
    id: `msg-report-${report.report_version_id}`,
    sender: "assistant",
    timestamp: messageTime(report.published_at),
    type: "report_card",
    reportData: buildPublishedReportSummary(report)
  });
  const conversationMessages = workspace.conversation.map((message) => ({
    id: message.message_id,
    sender: message.role,
    timestamp: messageTime(message.created_at),
    content: message.content,
    type: message.role === "assistant" ? "grounded_answer" as const : "text" as const
  }));
  messages.push(...restoreActivityTimelines(
    conversationMessages,
    workspace.conversation,
    workspace.activity_events
  ));

  const persisted = readHistoricalPendingTurn(workspace.workspace_id);
  const pending = workspace.latest_turn
    && (workspace.latest_turn.status === "running" || workspace.latest_turn.status === "interrupted")
    ? workspace.latest_turn
    : null;
  if (!pending && persisted) {
    clearHistoricalPendingTurn(workspace.workspace_id);
  }

  return {
    id: workspace.workspace_id,
    title: workspace.title,
    status: "报告已生成",
    updatedAt: "历史报告",
    draft,
    messages,
    executionPhase: "completed",
    executionProgress: 100,
    reportBinding: {
      workspaceId: workspace.workspace_id,
      reportVersionId: report.report_version_id,
      reportId: report.report_id,
      taskId: report.task_id,
      versionNumber: report.version_number,
      publishedAt: report.published_at,
      pendingTurn: pending
        ? {
            turnId: pending.turn_id,
            clientMessageId: persisted?.turn_id === pending.turn_id
              ? persisted.client_message_id
              : "",
            stage: pending.stage,
            recovering: true,
            afterSequence: Math.max(
              pending.event_sequence,
              persisted?.turn_id === pending.turn_id ? persisted.after_sequence : 0
            ),
            activityEvents: activityEventsForTurn(
              workspace.activity_events,
              pending.turn_id
            )
          }
        : undefined
    }
  };
}
