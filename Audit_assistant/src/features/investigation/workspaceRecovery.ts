import type {
  ChatMessage,
  InvestigationSession,
  PlatformCode,
  TaskDraft
} from "../../types/investigation";
import type {
  InvestigationDraftArtifact,
  InvestigationWorkspaceMessage,
  InvestigationWorkspaceState
} from "../../types/investigationCreation";
import { buildConfirmationIdempotencyKey } from "./confirmationView";
import { mapInvestigationRunState } from "./investigationRunState";

function messageTime(createdAt: string) {
  const value = new Date(createdAt);
  return Number.isNaN(value.getTime())
    ? ""
    : value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function publicMessage(
  message: InvestigationWorkspaceMessage,
  confirmationVisible: boolean
): ChatMessage {
  const artifact = message.artifact?.artifact_type === "investigation_draft"
    ? message.artifact
    : null;
  if (artifact?.presentation_stage === "suggestion" && artifact.suggestion) {
    const suggestion = artifact.suggestion;
    return {
      id: message.message_id,
      sender: message.role,
      timestamp: messageTime(message.created_at),
      content: message.content,
      type: "task_proposal",
      proposalData: {
        taskName: suggestion.title,
        taskType: "平台话题采集",
        subject: suggestion.objective,
        matchedRuleSet: suggestion.ruleset_revision?.name || "尚未绑定规则集",
        ruleSetDescription: suggestion.audit_policy?.description || "等待有效审核策略后方可确认。",
        platformsSelected: [suggestion.selected_platform],
        platformsConfirmed: confirmationVisible,
        interactionMode: "platform-selection"
      }
    };
  }
  if (artifact?.presentation_stage === "confirmation") {
    return {
      id: message.message_id,
      sender: message.role,
      timestamp: messageTime(message.created_at),
      content: message.content,
      type: "task_confirmation"
    };
  }
  return {
    id: message.message_id,
    sender: message.role,
    timestamp: messageTime(message.created_at),
    content: message.content,
    type: "text"
  };
}

function taskDraftFromArtifact(artifact: InvestigationDraftArtifact): TaskDraft {
  const preview = artifact.confirmation_preview;
  const suggestion = artifact.suggestion;
  return {
    taskName: artifact.draft.title,
    taskType: "平台话题采集",
    subject: artifact.draft.objective,
    platforms: [(suggestion?.selected_platform || preview.platform) as PlatformCode],
    keywords: suggestion?.search_terms || preview.resolved_search_terms,
    matchedRuleSet: suggestion?.ruleset_revision?.name
      || preview.ruleset_revision?.name
      || "尚未绑定规则集",
    analysisPlanName: suggestion?.audit_policy?.name
      || preview.audit_policy?.name
      || "尚未选择审核策略",
    ruleSetDescription: suggestion?.audit_policy?.description
      || preview.audit_policy?.description
      || "等待有效审核策略后方可确认。",
    recommendedRecallLexicons: suggestion?.recall_lexicons.map((lexicon) => lexicon.title) || [],
    status: artifact.presentation_stage === "confirmation" ? "等待确认" : "配置中",
    confirmed: false
  };
}

function emptyDraft(): TaskDraft {
  return {
    taskName: "自定义巡查任务",
    taskType: "平台话题采集",
    subject: "待确定",
    platforms: [],
    keywords: [],
    matchedRuleSet: "待选择规则集",
    ruleSetDescription: "等待调查方案生成。",
    status: "配置中",
    confirmed: false
  };
}

export function buildWorkspaceRecoveryErrorSession(
  workspaceSessionId: string,
  message: string
): InvestigationSession {
  return {
    id: workspaceSessionId,
    title: "调查工作区读取失败",
    status: "配置中",
    updatedAt: "刚刚",
    draft: emptyDraft(),
    messages: [{
      id: `workspace-recovery-error:${workspaceSessionId}`,
      sender: "assistant",
      timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
      content: `调查工作区恢复失败：${message}`,
      type: "text"
    }],
    executionPhase: "idle",
    executionProgress: 0,
    creationBinding: { workspaceSessionId, error: message }
  };
}

export function buildNewInvestigationWorkspaceSession(
  workspaceSessionId: string
): InvestigationSession {
  return {
    id: workspaceSessionId,
    title: "新调查需求",
    status: "配置中",
    updatedAt: "刚刚",
    draft: emptyDraft(),
    executionPhase: "idle",
    executionProgress: 0,
    messages: [],
    creationBinding: { workspaceSessionId }
  };
}

export function restoreInvestigationWorkspace(
  state: InvestigationWorkspaceState
): InvestigationSession {
  const workspaceSessionId = state.workspace.workspace_session_id;
  const artifact = state.draft_artifact;
  const preview = artifact?.confirmation_preview;
  const run = state.run;
  const runView = run ? mapInvestigationRunState(run) : null;
  const confirmationVisible = artifact?.presentation_stage === "confirmation";
  const hasSuggestionMessageArtifact = state.messages.some(
    (message) => message.artifact?.artifact_type === "investigation_draft"
      && message.artifact.presentation_stage === "suggestion"
      && message.artifact.suggestion !== null
  );
  let artifactMessageIndex = -1;
  if (artifact && !hasSuggestionMessageArtifact) {
    const confirmationMessageIndex = state.messages.findIndex(
      (message) => message.artifact?.artifact_type === "investigation_draft"
        && message.artifact.presentation_stage === "confirmation"
    );
    state.messages.forEach((message, index) => {
      if (
        message.role === "assistant"
        && (confirmationMessageIndex < 0 || index < confirmationMessageIndex)
      ) artifactMessageIndex = index;
    });
  }
  const recoveredSuggestionArtifact = artifact
    ? { ...artifact, presentation_stage: "suggestion" as const }
    : null;
  const messages = state.messages.map((message, index) => publicMessage(
    index === artifactMessageIndex
      ? { ...message, artifact: recoveredSuggestionArtifact }
      : message,
    confirmationVisible
  ));
  if (run) {
    messages.push({
      id: `workspace-run:${run.run_id}`,
      sender: "assistant",
      timestamp: messageTime(run.created_at),
      type: "agent_collaboration"
    });
  }
  if (run?.status === "PUBLISHED" && run.report_version_id) {
    messages.push({
      id: `msg-report-${run.report_version_id}`,
      sender: "assistant",
      timestamp: messageTime(run.updated_at),
      type: "report_card"
    });
  }
  const reportMessages = state.report_messages || [];
  messages.push(...reportMessages.map((message) => publicMessage(message, false)));

  const draft: TaskDraft = artifact && preview
    ? {
        ...taskDraftFromArtifact(artifact),
        status: run ? "已创建" : artifact.presentation_stage === "confirmation" ? "等待确认" : "配置中",
        confirmed: Boolean(run)
      }
    : emptyDraft();
  const latestTurn = state.latest_turn;
  const pendingTurn = latestTurn
    && (latestTurn.status === "running" || latestTurn.status === "interrupted")
    ? latestTurn
    : null;
  const latestReportTurn = state.latest_report_turn;
  const pendingReportTurn = latestReportTurn
    && (latestReportTurn.status === "running" || latestReportTurn.status === "interrupted")
    ? latestReportTurn
    : null;
  const pendingReportQuestion = pendingReportTurn
      ? reportMessages.find((message) => (
        message.turn_id === pendingReportTurn.turn_id && message.role === "user"
      ))?.content || ""
    : "";

  return {
    id: workspaceSessionId,
    title: artifact?.draft.title || "新调查需求",
    status: run?.status === "PUBLISHED"
      ? "报告已生成"
      : run
        ? "研判中"
        : artifact
          ? artifact.presentation_stage === "confirmation" ? "等待确认" : "配置中"
          : "配置中",
    updatedAt: "刚刚",
    draft,
    messages,
    executionPhase: runView?.phase || "idle",
    executionProgress: run?.status === "PUBLISHED" ? 100 : 0,
    creationBinding: {
      workspaceSessionId,
      draft: artifact?.draft,
      confirmationPreview: preview,
      suggestion: artifact?.suggestion || undefined,
      presentationStage: artifact?.presentation_stage,
      pendingTurnId: pendingTurn?.turn_id,
      resumeAttempted: false,
      run: run || undefined,
      confirmationKey: artifact
        ? buildConfirmationIdempotencyKey(
            workspaceSessionId,
            artifact.draft_id,
            artifact.draft_revision
          )
        : undefined,
      pendingReportTurn: pendingReportTurn
        ? {
            turnId: pendingReportTurn.turn_id,
            clientMessageId: "",
            question: pendingReportQuestion,
            stage: pendingReportTurn.stage,
            resumeAttempted: false
          }
        : undefined,
      error: latestTurn?.status === "error"
        || (latestTurn?.status === "interrupted" && !latestTurn.retryable)
          ? latestTurn.safe_message
          : latestReportTurn?.status === "error"
            || (latestReportTurn?.status === "interrupted" && !latestReportTurn.retryable)
            ? latestReportTurn.safe_message
            : run?.error_message || undefined
    }
  };
}
