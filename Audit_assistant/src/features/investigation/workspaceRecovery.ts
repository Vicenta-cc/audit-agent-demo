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
import { formatCreationErrorMessage } from "./confirmationView";

const technicalCreationContent = /(schema_version|draft[_\s-]?id|toolresult|\[object object\]|\/rule-assistant\/|https?:\/\/|\|\s*(?:项目|草稿|状态)\s*\||[A-Z]{3,}(?:_[A-Z0-9]+)+|```|\{\s*"|database|revision)/i;

export function presentCreationAssistantContent(content: string) {
  const trimmed = content.trim();
  if (!trimmed) return "正在整理本次调查建议。";
  if (!technicalCreationContent.test(trimmed)) return trimmed;
  return formatCreationErrorMessage(trimmed);
}

function messageTime(createdAt: string) {
  const value = new Date(createdAt);
  return Number.isNaN(value.getTime())
    ? ""
    : value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function publicMessage(
  message: InvestigationWorkspaceMessage,
  confirmationVisible: boolean,
  sanitizeAssistant = true
): ChatMessage {
  const presentation = message.role === "assistant" && message.artifact?.proposal_presentations?.length
    ? { content: message.content, authoritativeProposalPresentation: true, rulePresentations: message.artifact.proposal_presentations }
    : {};
  const artifact = message.artifact?.artifact_type === "investigation_draft"
    ? message.artifact
    : null;
  if (artifact?.presentation_stage === "suggestion" && artifact.suggestion) {
    const suggestion = artifact.suggestion;
    return {
      id: message.message_id,
      sender: message.role,
      timestamp: messageTime(message.created_at),
      content: presentCreationAssistantContent(message.content),
      ...presentation,
      type: "task_proposal",
      proposalData: {
        taskName: suggestion.title,
        taskType: "平台话题采集",
        subject: suggestion.objective,
        matchedRuleSet: suggestion.temporary_ruleset ? `本次使用临时规则：${suggestion.temporary_ruleset.content.name}` : suggestion.ruleset_revision?.name || "尚未绑定审核规则",
        ruleSetDescription: suggestion.ruleset_revision
          ? `已选择发布版本 v${suggestion.ruleset_revision.version}，包含 ${suggestion.ruleset_revision.enabled_rule_count} 条启用规则。`
          : suggestion.temporary_ruleset ? "本次使用临时规则，确认后随任务冻结执行。" : "等待选择已发布的研判规则。",
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
      content: presentCreationAssistantContent(message.content),
      ...presentation,
      type: "task_confirmation"
    };
  }
  return {
    id: message.message_id,
    sender: message.role,
    timestamp: messageTime(message.created_at),
    content: message.role === "assistant" && sanitizeAssistant
      ? presentCreationAssistantContent(message.content)
      : message.content,
    ...presentation,
    type: "text"
  };
}

function taskDraftFromArtifact(artifact: InvestigationDraftArtifact): TaskDraft {
  const preview = artifact.confirmation_preview;
  const suggestion = artifact.suggestion;
  return {
    taskName: artifact.draft.title,
    taskType: preview.mode === "creator" ? "博主主页采集" : "平台话题采集",
    subject: artifact.draft.objective,
    platforms: [(suggestion?.selected_platform || preview.platform) as PlatformCode],
    keywords: suggestion?.search_terms || preview.resolved_search_terms,
    matchedRuleSet: suggestion?.ruleset_revision?.name
      || preview.ruleset_revision?.name
      || (preview.temporary_ruleset ? `本次使用临时规则：${preview.temporary_ruleset.content.name}` : "")
      || "尚未绑定审核规则",
    analysisPlanName: suggestion?.ruleset_revision?.name
      || preview.ruleset_revision?.name
      || (preview.temporary_ruleset ? `本次使用临时规则：${preview.temporary_ruleset.content.name}` : "")
      || "尚未选择研判规则",
    ruleSetDescription: preview.ruleset_revision
      ? `已选择发布版本 v${preview.ruleset_revision.version}，包含 ${preview.ruleset_revision.enabled_rule_count} 条启用规则。`
      : preview.temporary_ruleset ? "本次使用临时规则，确认后随任务冻结执行。" : "等待选择已发布的研判规则。",
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
    matchedRuleSet: "待选择审核规则",
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
      content: "当前调查暂时无法恢复，请返回调查列表后重试。",
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
    index === artifactMessageIndex && !message.artifact?.proposal_presentations?.length
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
  messages.push(...reportMessages.map((message) => publicMessage(message, false, false)));

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
    title: artifact?.draft.title || state.messages.find(message => message.role === "user")?.content.slice(0, 48) || "新调查需求",
    status: run?.status === "PUBLISHED"
      ? "报告已生成"
      : run?.status === "AUDIT_COMPLETED"
        ? "审核完成"
      : run?.status === "FAILED"
        ? "调查失败"
      : run?.status === "INTERRUPTED"
        ? "调查已中断"
      : run
        ? "研判中"
        : artifact
          ? artifact.presentation_stage === "confirmation" ? "等待确认" : "配置中"
          : "配置中",
    updatedAt: "刚刚",
    draft,
    messages,
    executionPhase: runView?.phase || "idle",
    executionProgress: run?.status === "PUBLISHED"
      ? 100
      : run?.status === "AUDIT_COMPLETED"
        ? 75
        : 0,
    creationBinding: {
      workspaceSessionId,
      draft: artifact?.draft,
      confirmationPreview: preview,
      suggestion: artifact?.suggestion || undefined,
      presentationStage: artifact?.presentation_stage,
      pendingTurnId: pendingTurn?.turn_id,
      pendingTurnStage: pendingTurn?.stage,
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
