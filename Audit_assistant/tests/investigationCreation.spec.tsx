import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import {
  buildConfirmationCardView,
  buildConfirmationIdempotencyKey,
  formatConfirmationBlockerMessage,
  parseInvestigationSearchTerms
} from "../src/features/investigation/confirmationView";
import { mapInvestigationRunState } from "../src/features/investigation/investigationRunState";
import {
  loadM3AnalysisRecords,
  mapM3PostToAnalysisRecord,
  mapReportEvidence,
  readRunAnalysisCounts
} from "../src/features/investigation/m3AnalysisRecords";
import { buildDraftSuggestionPlanView } from "../src/features/investigation/InvestigationContextDrawer";
import {
  buildSuggestionBlockerLink,
  selectSingleSuggestionPlatform,
} from "../src/features/investigation/TaskSuggestionCard";
import {
  buildNewInvestigationWorkspaceSession,
  buildWorkspaceRecoveryErrorSession,
  restoreInvestigationWorkspace
} from "../src/features/investigation/workspaceRecovery";
import { ApiError } from "../src/services/apiClient";
import {
  confirmAndQueueInvestigation,
  createInvestigationWorkspace,
  getInvestigationWorkspaceState,
  getInvestigationWorkspace,
  getInvestigationWorkspacePublishedReport,
  generateInvestigationConfirmationPreview,
  listInvestigationWorkspaceMessages,
  resumeInvestigationWorkspaceReportTurn,
  sendInvestigationWorkspaceReportTurn,
  updateInvestigationDraft
} from "../src/services/investigationCreation";
import type { TaskDraft } from "../src/types/investigation";
import type {
  ConfirmationPreview,
  InvestigationDraftConfiguration,
  InvestigationRunProjection,
  InvestigationWorkspaceState,
  PublicInvestigationDraft
} from "../src/types/investigationCreation";

const draft: TaskDraft = {
  taskName: "世界杯博彩引流调查",
  taskType: "风险调查",
  subject: "博彩引流",
  platforms: ["xhs"],
  keywords: ["世界杯博彩"],
  matchedRuleSet: "博彩风险规则",
  ruleSetDescription: "",
  status: "等待确认",
  confirmed: false
};

const preview: ConfirmationPreview = {
  draft_id: "draft-1",
  draft_revision: 3,
  title: "世界杯博彩引流调查",
  objective: "识别世界杯期间的小红书博彩引流",
  mode: "search",
  platform: "xhs",
  resolved_search_terms: ["世界杯博彩", "看球下注"],
  creator_url: "",
  recall_plan: {
    strategy: "temporary_terms",
    lexicon_id: "",
    lexicon_title: "",
    enabled_main_term_count: 0,
    enabled_main_terms: [],
    temporary_terms: ["世界杯博彩", "看球下注"],
    source_lexicon_ids: []
  },
  audit_policy: {
    id: "policy-1",
    name: "博彩引流审核策略",
    description: "",
    published_version: "v1",
    ruleset_revision_id: "ruleset-rev-1",
    ruleset_version: 7,
    domain: "gambling"
  },
  ruleset_revision: {
    id: "ruleset-rev-1",
    ruleset_id: "ruleset-1",
    name: "博彩风险规则",
    domain: "gambling",
    version: 7,
    enabled_rule_count: 4
  },
  max_notes: 1,
  blockers: [],
  can_confirm: true
};

const configuration: InvestigationDraftConfiguration = {
  schema_version: "investigation-draft-config-v3",
  platform: "xhs",
  investigation: {
    mode: "search",
    recall_plan: {
      strategy: "temporary_terms",
      terms: ["世界杯博彩"],
      source_lexicon_ids: []
    }
  },
  audit_policy: null
};

const publicDraft: PublicInvestigationDraft = {
  id: "draft-1",
  status: "DRAFT",
  current_revision: 3,
  title: preview.title,
  objective: preview.objective,
  configuration,
  created_at: "2026-08-28T00:00:00Z",
  updated_at: "2026-08-28T00:01:00Z",
  confirmed_revision: null,
  confirmed_at: ""
};

const suggestion = {
  title: preview.title,
  objective: preview.objective,
  mode: "search" as const,
  creator_url: "",
  platform_options: [
    { id: "dy" as const, name: "抖音", available: true },
    { id: "xhs" as const, name: "小红书", available: true },
    { id: "ks" as const, name: "快手", available: true }
  ],
  selected_platform: "xhs" as const,
  search_terms: ["世界杯博彩", "看球下注"],
  audit_policy: preview.audit_policy,
  ruleset_revision: preview.ruleset_revision,
  recall_lexicons: [{
    id: "lexicon-1",
    title: "博彩引流词库",
    risk_label: "博彩引流",
    enabled_main_term_count: 8,
    runtime_content_hash: "a".repeat(64),
    enabled_main_terms: ["世界杯博彩", "看球下注"],
    available: true
  }]
};

function workspaceState(
  overrides: Partial<InvestigationWorkspaceState> = {}
): InvestigationWorkspaceState {
  return {
    workspace: {
      workspace_session_id: "investigation-session:workspace-1",
      status: "active",
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:01:00Z"
    },
    messages: [{
      message_id: "message-user-1",
      turn_id: "turn-1",
      role: "user",
      content: "先生成方案，不要开始采集",
      sequence: 1,
      created_at: "2026-08-28T00:00:00Z"
    }, {
      message_id: "message-assistant-1",
      turn_id: "turn-1",
      role: "assistant",
      content: "这段自然语言故意不包含任何可解析的任务字段。",
      artifact: {
        artifact_type: "investigation_draft",
        presentation_stage: "suggestion",
        draft_id: publicDraft.id,
        draft_revision: publicDraft.current_revision,
        draft: publicDraft,
        confirmation_preview: preview,
        suggestion
      },
      sequence: 2,
      created_at: "2026-08-28T00:01:00Z"
    }],
    latest_turn: {
      session_id: "investigation-session:workspace-1",
      turn_id: "turn-1",
      status: "completed",
      stage: "completed",
      answer: "调查方案已生成。",
      safe_message: "",
      retryable: false,
      updated_at: "2026-08-28T00:01:00Z"
    },
    draft_artifact: {
      artifact_type: "investigation_draft",
      presentation_stage: "suggestion",
      draft_id: publicDraft.id,
      draft_revision: publicDraft.current_revision,
      draft: publicDraft,
      confirmation_preview: preview,
      suggestion
    },
    run: null,
    report_messages: [],
    latest_report_turn: null,
    ...overrides
  };
}

function run(status: InvestigationRunProjection["status"], overrides = {}): InvestigationRunProjection {
  return {
    run_id: "run-1",
    draft_id: "draft-1",
    draft_revision: 3,
    status,
    job_id: "job-1",
    crawl_status: "pending",
    analysis_status: "pending",
    task_stats: {},
    audit_results: [],
    report_status: "pending",
    report_version_id: "",
    error_code: "",
    error_message: "",
    created_at: "2026-08-28T00:00:00Z",
    updated_at: "2026-08-28T00:00:00Z",
    started_at: "",
    completed_at: "",
    ...overrides
  };
}

test.beforeEach(() => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      XHS_AUDIT_API_BASE: "http://api.test",
      location: { origin: "http://ui.test" }
    }
  });
});

test("renders real Draft and ConfirmationPreview fields", () => {
  const view = buildConfirmationCardView(draft, preview);
  expect(view).toMatchObject({
    platform: "小红书",
    objective: preview.objective,
    auditPolicy: "博彩引流审核策略",
    ruleSet: "博彩风险规则 · v7",
    recallStrategy: "本次临时搜索词",
    maxNotes: 1,
    termsOrCreator: "世界杯博彩、看球下注",
    canConfirm: true
  });
  expect(parseInvestigationSearchTerms("世界杯博彩、看球下注，外围盘; 世界杯博彩"))
    .toEqual(["世界杯博彩", "看球下注", "外围盘"]);
});

test("reads the durable creation workspace and public messages", async () => {
  const requestUrls: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    requestUrls.push(url);
    const body = url.endsWith("/messages")
      ? [{
          message_id: "message-1",
          turn_id: "turn-1",
          role: "assistant",
          content: "调查方案已生成。",
          artifact: null,
          sequence: 2,
          created_at: "2026-08-28T00:00:00Z"
        }]
      : {
          workspace_session_id: "workspace-1",
          status: "active",
          created_at: "2026-08-28T00:00:00Z",
          updated_at: "2026-08-28T00:00:00Z"
        };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  const workspace = await getInvestigationWorkspace("workspace-1");
  const messages = await listInvestigationWorkspaceMessages("workspace-1");
  expect(workspace.workspace_session_id).toBe("workspace-1");
  expect(messages[0]).toMatchObject({ role: "assistant", turn_id: "turn-1" });
  expect(requestUrls).toEqual([
    "http://api.test/api/investigation-workspaces/workspace-1",
    "http://api.test/api/investigation-workspaces/workspace-1/messages"
  ]);
});

test("new investigation uses the backend workspace identity as the route identity", async () => {
  let requestBody = "";
  globalThis.fetch = async (_input, init) => {
    requestBody = String(init?.body || "");
    return new Response(JSON.stringify({
      workspace_session_id: "investigation-session:stable-1",
      status: "active",
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z"
    }), { status: 201, headers: { "Content-Type": "application/json" } });
  };
  const workspace = await createInvestigationWorkspace("");
  const session = buildNewInvestigationWorkspaceSession(workspace.workspace_session_id);
  expect(JSON.parse(requestBody)).toEqual({ workspace_key: "" });
  expect(session.id).toBe("investigation-session:stable-1");
  expect(session.creationBinding?.workspaceSessionId).toBe(session.id);
});

test("restores the real suggestion card from structured artifact without parsing assistant text", () => {
  const session = restoreInvestigationWorkspace(workspaceState());
  expect(session.id).toBe("investigation-session:workspace-1");
  expect(session.messages.map((message) => message.id)).toEqual([
    "message-user-1",
    "message-assistant-1"
  ]);
  expect(session.messages[1]).toMatchObject({
    type: "task_proposal",
    proposalData: {
      taskName: preview.title,
      platformsSelected: ["xhs"]
    }
  });
  expect(session.draft.keywords).toEqual(suggestion.search_terms);
  expect(session.draft.recommendedRecallLexicons).toEqual(["博彩引流词库"]);
  expect(session.creationBinding?.presentationStage).toBe("suggestion");
  expect(session.creationBinding?.draft).toEqual(publicDraft);
  expect(session.creationBinding?.confirmationPreview).toEqual(preview);
  expect(session.creationBinding?.confirmationKey).toContain("draft-1:3");
  expect(JSON.stringify(session)).not.toContain("report_session_id");
});

test("binds a recovered current public artifact to the last assistant message", () => {
  const state = workspaceState();
  const session = restoreInvestigationWorkspace(workspaceState({
    messages: state.messages.map((message) => ({ ...message, artifact: null }))
  }));

  expect(session.messages[1]).toMatchObject({
    type: "task_proposal",
    proposalData: {
      taskName: preview.title,
      platformsSelected: ["xhs"]
    }
  });
  expect(session.messages[1].content).toBe(
    "这段自然语言故意不包含任何可解析的任务字段。"
  );
});

test("replaces a legacy suggestion-null message artifact with the current projection", () => {
  const state = workspaceState();
  const legacyMessages = state.messages.map((message) => (
    message.role === "assistant"
      ? {
          ...message,
          artifact: {
            ...state.draft_artifact!,
            suggestion: null
          }
        }
      : message
  ));
  const session = restoreInvestigationWorkspace(workspaceState({
    messages: legacyMessages
  }));

  expect(session.messages[1]).toMatchObject({
    type: "task_proposal",
    proposalData: {
      taskName: preview.title,
      platformsSelected: ["xhs"]
    }
  });
  expect(session.draft.recommendedRecallLexicons).toEqual(["博彩引流词库"]);
});

test("real analysis-plan drawer projects public resources without mock rule details", () => {
  const view = buildDraftSuggestionPlanView(suggestion);

  expect(view).toMatchObject({
    policyName: "博彩引流审核策略",
    ruleSetName: "博彩风险规则",
    ruleSetVersion: 7,
    recallLexicons: [{ title: "博彩引流词库" }],
    searchTerms: ["世界杯博彩", "看球下注"]
  });
  expect(JSON.stringify(view)).not.toContain("generalExemptions");
  expect(JSON.stringify(view)).not.toContain("categories");
});

test("restores the final confirmation card and selected platform after refresh", () => {
  const state = workspaceState();
  const confirmationArtifact = {
    ...state.draft_artifact!,
    presentation_stage: "confirmation" as const
  };
  const session = restoreInvestigationWorkspace(workspaceState({
    messages: [
      ...state.messages,
      {
        message_id: "message-user-confirmation",
        turn_id: "turn-confirmation",
        role: "user",
        content: "生成任务配置",
        sequence: 3,
        created_at: "2026-08-28T00:02:00Z"
      },
      {
        message_id: "message-assistant-confirmation",
        turn_id: "turn-confirmation",
        role: "assistant",
        content: "配置已生成。",
        artifact: confirmationArtifact,
        sequence: 4,
        created_at: "2026-08-28T00:02:01Z"
      }
    ],
    draft_artifact: confirmationArtifact
  }));

  expect(session.creationBinding?.presentationStage).toBe("confirmation");
  expect(session.messages.at(-1)?.type).toBe("task_confirmation");
  expect(session.draft.platforms).toEqual(["xhs"]);
  expect(session.status).toBe("等待确认");
});

test("restores both recovered suggestion and persisted confirmation artifacts", () => {
  const state = workspaceState();
  const confirmationArtifact = {
    ...state.draft_artifact!,
    presentation_stage: "confirmation" as const
  };
  const messages = [
    ...state.messages.map((message) => ({ ...message, artifact: null })),
    {
      message_id: "message-user-confirmation-recovery",
      turn_id: "turn-confirmation-recovery",
      role: "user" as const,
      content: "生成任务配置",
      sequence: 3,
      created_at: "2026-08-28T00:02:00Z"
    },
    {
      message_id: "message-assistant-confirmation-recovery",
      turn_id: "turn-confirmation-recovery",
      role: "assistant" as const,
      content: "配置已生成。",
      artifact: confirmationArtifact,
      sequence: 4,
      created_at: "2026-08-28T00:02:01Z"
    }
  ];
  const session = restoreInvestigationWorkspace(workspaceState({
    messages,
    draft_artifact: confirmationArtifact
  }));

  expect(session.messages.map((message) => message.type)).toEqual([
    "text",
    "task_proposal",
    "text",
    "task_confirmation"
  ]);
  expect(session.messages[1].proposalData?.platformsConfirmed).toBe(true);
  expect(session.draft.platforms).toEqual(["xhs"]);
});

test("suggestion platform buttons are single-select and contain no Weibo candidate", () => {
  expect(selectSingleSuggestionPlatform("xhs")).toEqual(["xhs"]);
  expect(selectSingleSuggestionPlatform("ks")).toEqual(["ks"]);
  expect(suggestion.platform_options.map((item) => item.id)).toEqual(["dy", "xhs", "ks"]);
  expect(JSON.stringify(suggestion.platform_options)).not.toContain("wb");
});

test("restores a pending Turn without inventing a Draft or a replacement Turn", () => {
  const pending = workspaceState({
    messages: [workspaceState().messages[0]],
    latest_turn: {
      ...workspaceState().latest_turn!,
      status: "running",
      stage: "answering",
      answer: ""
    },
    draft_artifact: null
  });
  const session = restoreInvestigationWorkspace(pending);
  expect(session.creationBinding?.pendingTurnId).toBe("turn-1");
  expect(session.creationBinding?.draft).toBeUndefined();
  expect(session.messages).toHaveLength(1);

  const interrupted = restoreInvestigationWorkspace(workspaceState({
    messages: [workspaceState().messages[0]],
    latest_turn: {
      ...workspaceState().latest_turn!,
      status: "interrupted",
      stage: "interrupted",
      answer: "",
      safe_message: "结果未知，可以安全恢复。",
      retryable: true
    },
    draft_artifact: null
  }));
  expect(interrupted.creationBinding?.pendingTurnId).toBe("turn-1");
  expect(interrupted.creationBinding?.resumeAttempted).toBe(false);
});

test("restores the same confirmed Run and published report version in one workspace", () => {
  const queued = restoreInvestigationWorkspace(workspaceState({ run: run("QUEUED") }));
  expect(queued.creationBinding?.run?.run_id).toBe("run-1");
  expect(queued.executionPhase).toBe("collection_waking");
  const published = restoreInvestigationWorkspace(workspaceState({
    run: run("PUBLISHED", {
      report_status: "published",
      report_version_id: "report-version:published-1"
    })
  }));
  expect(published.id).toBe("investigation-session:workspace-1");
  expect(published.status).toBe("报告已生成");
  expect(published.creationBinding?.run?.report_version_id).toBe("report-version:published-1");
  expect(JSON.stringify(published)).not.toContain("report_session_id");
});

test("restores an audit-completed Run without claiming a generated report", () => {
  const auditCompleted = restoreInvestigationWorkspace(workspaceState({
    run: run("AUDIT_COMPLETED", {
      crawl_status: "completed",
      analysis_status: "completed",
      task_stats: { ingested_count: 1, completed_analysis_count: 1 },
      report_status: "pending"
    })
  }));

  expect(auditCompleted.status).toBe("审核完成");
  expect(auditCompleted.executionProgress).toBe(75);
  expect(auditCompleted.executionPhase).toBe("audit_completed");
  expect(auditCompleted.creationBinding?.run?.report_version_id).toBe("");
});

test("restores report messages and a pending report Turn in the same workspace", () => {
  const published = restoreInvestigationWorkspace(workspaceState({
    run: run("PUBLISHED", {
      report_status: "published",
      report_version_id: "report-version:published-1"
    }),
    report_messages: [{
      message_id: "report-question-1",
      turn_id: "report-turn-1",
      role: "user",
      content: "报告中有哪些主要风险？",
      sequence: 1,
      created_at: "2026-08-28T00:02:00Z"
    }],
    latest_report_turn: {
      turn_id: "report-turn-1",
      status: "interrupted",
      stage: "interrupted",
      answer: "",
      safe_message: "报告问答执行已中断，可以安全恢复。",
      retryable: true,
      updated_at: "2026-08-28T00:02:00Z"
    }
  }));

  expect(published.messages.map((message) => message.id).slice(-2)).toEqual([
    "msg-report-report-version:published-1",
    "report-question-1"
  ]);
  expect(published.creationBinding?.pendingReportTurn).toMatchObject({
    turnId: "report-turn-1",
    question: "报告中有哪些主要风险？",
    stage: "interrupted",
    resumeAttempted: false
  });
  expect(JSON.stringify(published)).not.toContain("report_session_id");
});

test("workspace state API failures stay explicit and never produce a demo fallback", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: "调查工作区不存在"
  }), { status: 404, headers: { "Content-Type": "application/json" } });
  await expect(getInvestigationWorkspaceState("investigation-session:missing"))
    .rejects.toMatchObject<ApiError>({ status: 404 });
  const failed = buildWorkspaceRecoveryErrorSession(
    "investigation-session:missing",
    "调查工作区不存在"
  );
  expect(failed.id).toBe("investigation-session:missing");
  expect(failed.creationBinding?.error).toBe("调查工作区不存在");
  expect(failed.messages[0].content).toContain("恢复失败");
});

test("blockers disable confirmation and expose the management URL", () => {
  const blocked = {
    ...preview,
    audit_policy: null,
    ruleset_revision: null,
    can_confirm: false,
    blockers: [{
      code: "NO_PUBLISHED_AUDIT_POLICY",
      message: "尚无已发布审核策略",
      resource_type: "audit_policy",
      resource_id: "",
      latest_safe_summary: {},
      management_url: "/rule-assistant/rulesets?return_to=/investigation"
    }]
  } satisfies ConfirmationPreview;
  const view = buildConfirmationCardView(draft, blocked);
  expect(view.canConfirm).toBe(false);
  expect(view.auditPolicy).toBe("尚未选择审核策略");
  expect(view.rulesManagementUrl).toBe(
    "/rule-assistant/rulesets?return_to=/investigation"
  );
  expect(formatConfirmationBlockerMessage(
    blocked.blockers[0].code,
    "A valid published AuditPolicy must be selected before confirmation."
  )).toBe("当前没有已发布的审核策略，无法确认执行。");

  expect(buildSuggestionBlockerLink(blocked)).toEqual({
    label: "编辑/新增研判方案",
    managementUrl: "/rule-assistant/rulesets?return_to=/investigation"
  });
});

test("creator workspace recovery preserves the homepage URL and carries no search terms", () => {
  const creatorUrl = "https://www.douyin.com/user/MS4wLjABAAAA-valid";
  const creatorDraft: PublicInvestigationDraft = {
    ...publicDraft,
    configuration: {
      ...configuration,
      platform: "dy",
      investigation: { mode: "creator", creator_url: creatorUrl }
    }
  };
  const creatorPreview: ConfirmationPreview = {
    ...preview,
    mode: "creator",
    platform: "dy",
    resolved_search_terms: [],
    creator_url: creatorUrl,
    recall_plan: {
      strategy: "none",
      lexicon_id: "",
      lexicon_title: "",
      enabled_main_term_count: 0,
      enabled_main_terms: [],
      temporary_terms: [],
      source_lexicon_ids: []
    }
  };
  const creatorSuggestion = {
    ...suggestion,
    mode: "creator" as const,
    creator_url: creatorUrl,
    selected_platform: "dy" as const,
    platform_options: [{ id: "dy" as const, name: "抖音", available: true }],
    search_terms: [],
    recall_lexicons: []
  };
  const creatorState = workspaceState({
    draft_artifact: {
      ...workspaceState().draft_artifact!,
      draft: creatorDraft,
      confirmation_preview: creatorPreview,
      suggestion: creatorSuggestion
    },
    messages: workspaceState().messages.map((message) => (
      message.role === "assistant"
        ? {
            ...message,
            artifact: {
              ...workspaceState().draft_artifact!,
              draft: creatorDraft,
              confirmation_preview: creatorPreview,
              suggestion: creatorSuggestion
            }
          }
        : message
    ))
  });
  const restored = restoreInvestigationWorkspace(creatorState);
  expect(restored.draft.taskType).toBe("博主主页采集");
  expect(restored.draft.keywords).toEqual([]);
  expect(restored.creationBinding?.confirmationPreview?.creator_url).toBe(creatorUrl);
  expect(restored.creationBinding?.suggestion?.recall_lexicons).toEqual([]);
});

test("Draft edits use expected_revision and preserve structured 409 errors", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(JSON.stringify({
      detail: {
        code: "RESOURCE_STALE",
        message: "Revision conflict",
        details: { current_revision: 4 }
      }
    }), { status: 409, headers: { "Content-Type": "application/json" } });
  };

  await expect(updateInvestigationDraft("draft-1", {
    expectedRevision: 3,
    title: preview.title,
    objective: preview.objective,
    configuration
  })).rejects.toMatchObject<ApiError>({ status: 409, code: "RESOURCE_STALE" });
  expect(JSON.parse(String(requests[0].init?.body))).toMatchObject({ expected_revision: 3 });
});

test("generate task configuration persists a real workspace confirmation-preview Turn", async () => {
  let requestUrl = "";
  let requestBody = "";
  globalThis.fetch = async (input, init) => {
    requestUrl = String(input);
    requestBody = String(init?.body || "");
    return new Response(JSON.stringify({
      session_id: "investigation-session:workspace-1",
      turn_id: "turn-confirmation-1",
      status: "completed",
      stage: "completed",
      answer: "配置已生成。",
      safe_message: "",
      retryable: false,
      updated_at: "2026-08-28T00:02:00Z",
      artifact: {
        ...workspaceState().draft_artifact,
        presentation_stage: "confirmation"
      }
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const turn = await generateInvestigationConfirmationPreview(
    "investigation-session:workspace-1",
    {
      clientMessageId: "confirmation-message-1",
      draftId: "draft-1",
      expectedRevision: 3
    }
  );
  expect(requestUrl).toBe(
    "http://api.test/api/investigation-workspaces/"
      + "investigation-session%3Aworkspace-1/confirmation-preview"
  );
  expect(JSON.parse(requestBody)).toEqual({
    client_message_id: "confirmation-message-1",
    draft_id: "draft-1",
    expected_revision: 3
  });
  expect(turn.artifact).toMatchObject({ presentation_stage: "confirmation" });
});

test("confirmation is explicit and reuses the caller's idempotency key", async () => {
  const requests: RequestInit[] = [];
  globalThis.fetch = async (_input, init) => {
    requests.push(init || {});
    return new Response(JSON.stringify(run("QUEUED")), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };
  const key = buildConfirmationIdempotencyKey("workspace-1", "draft-1", 3);
  await confirmAndQueueInvestigation("draft-1", { expectedRevision: 3, idempotencyKey: key });
  await confirmAndQueueInvestigation("draft-1", { expectedRevision: 3, idempotencyKey: key });
  for (const request of requests) {
    expect(JSON.parse(String(request.body))).toMatchObject({ expected_revision: 3, confirmed: true });
    expect(new Headers(request.headers).get("Idempotency-Key")).toBe(key);
  }
  expect(buildConfirmationIdempotencyKey("workspace-1", "draft-1", 4)).not.toBe(key);
});

test("maps only real Run projection states, including parallel crawl and analysis", () => {
  expect(mapInvestigationRunState(run("QUEUED"))).toMatchObject({
    phase: "collection_waking", step: 1, activity: "queued"
  });
  expect(mapInvestigationRunState(run("RUNNING", {
    crawl_status: "running", analysis_status: "pending"
  }))).toMatchObject({ phase: "collection_working", step: 1, activity: "running" });
  expect(mapInvestigationRunState(run("RUNNING", {
    crawl_status: "running", analysis_status: "running"
  }))).toMatchObject({ phase: "evidence_working", step: 2, activity: "running" });
  expect(mapInvestigationRunState(run("RUNNING", {
    crawl_status: "completed", analysis_status: "pending"
  }))).toMatchObject({ phase: "evidence_handoff", step: 2, activity: "queued" });
  expect(mapInvestigationRunState(run("RUNNING", {
    crawl_status: "completed", analysis_status: "completed"
  }))).toMatchObject({ phase: "audit_completed", step: 3, activity: "queued" });
  expect(mapInvestigationRunState(run("AUDIT_COMPLETED", {
    crawl_status: "completed", analysis_status: "completed"
  }))).toMatchObject({
    phase: "audit_completed",
    label: "审核完成",
    step: 3,
    activity: "completed",
    terminal: null
  });
  expect(mapInvestigationRunState(run("REPORT_GENERATING"))).toMatchObject({
    phase: "report_generating", step: 4, activity: "running"
  });
  expect(mapInvestigationRunState(run("PUBLISHED", {
    report_status: "published", report_version_id: "report-version-1"
  }))).toMatchObject({ phase: "completed", terminal: "published", activity: "completed" });
  expect(mapInvestigationRunState(run("FAILED"))).toMatchObject({ terminal: "failed", activity: "stopped" });
  expect(mapInvestigationRunState(run("INTERRUPTED"))).toMatchObject({ terminal: "interrupted", activity: "stopped" });
  expect(mapInvestigationRunState(run("RUNNING", {
    crawl_status: "completed", analysis_status: "paused"
  }))).toMatchObject({ terminal: "paused", activity: "stopped" });
});

test("maps one real report post and its five-category Evidence without invented task output ids", () => {
  const evidence = [
    ["post_text", "文本"],
    ["ocr", "画面文字"],
    ["asr_audio", "音频"],
    ["comment_text", "评论"],
    ["visual_frame", "视觉"]
  ].map(([evidence_type, original_text], index) => ({
    evidence_ref: `evidence-${index + 1}`,
    post_ref: "post-1",
    audit_finding_ref: "finding-1",
    support_type: "direct" as const,
    evidence_type,
    original_text,
    translated_text: index === 2 ? "中文音频译文" : "",
    summary: `真实命中解释 ${index + 1}`
  }));
  const record = mapM3PostToAnalysisRecord({
    post: {
      post_ref: "post-1",
      title: "真实采集内容",
      content_summary: "真实分析摘要",
      author_display_name: "真实作者",
      audit_finding_ref: "finding-1",
      decision: "reject",
      risk_level: "high",
      audit_summary: "真实研判结论",
      investigation_finding_refs: ["finding-1"],
      direct_evidence: evidence
    },
    itemNumber: 1,
    platform: "小红书",
    analyzedAt: "2026-08-30 23:00",
    reportVersionId: "report-version-1"
  });
  expect(record).toMatchObject({
    source: "m3-report",
    itemNumber: 1,
    riskLabel: "高风险",
    summary: "真实分析摘要",
    conclusion: "真实研判结论",
    reportVersionId: "report-version-1",
    postRef: "post-1",
    findingRef: "finding-1",
    evidenceCounts: { text: 1, ocr: 1, asr: 1, comment: 1, vision: 1 }
  });
  expect(record.taskId).toBeUndefined();
  expect(record.outputId).toBeUndefined();
  expect(mapReportEvidence(evidence[2])).toMatchObject({
    type: "asr", translation: "中文音频译文", explanation: "真实命中解释 3"
  });
});

test("analysis progress counts come only from Run task statistics", () => {
  expect(readRunAnalysisCounts(run("RUNNING", {
    task_stats: {
      ingested_count: 1,
      completed_analysis_count: 1,
      batch_item_count: 10,
      total: 20
    }
  }))).toEqual({ completedCount: 1, totalCount: 1 });
  expect(readRunAnalysisCounts(run("RUNNING", { task_stats: {} })))
    .toEqual({ completedCount: 0, totalCount: 0 });
});

test("audit-completed records come directly from the current Job projection", async () => {
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("AUDIT_COMPLETED must not fetch ReportVersion data");
  };
  const result = await loadM3AnalysisRecords(run("AUDIT_COMPLETED", {
    crawl_status: "completed",
    analysis_status: "completed",
    task_stats: { ingested_count: 1, completed_analysis_count: 1 },
    report_status: "pending",
    audit_results: [{
      audit_result_id: "audit-result-1",
      content_key: "7590000000000000001",
      platform: "dy",
      content_title: "世界杯稳赚交流群",
      author_display_name: "内容作者甲",
      decision: "review",
      risk_level: "high",
      summary: "视频包含稳赚承诺和站外引流，建议复核。",
      analyzed_at: "2026-09-01T08:00:00Z",
      evidence: [{
        evidence_id: "ev-asr-1",
        evidence_type: "video_asr",
        content: "加入世界杯交流，宣称稳赚",
        translation: "",
        explanation: "命中稳赚承诺"
      }]
    }]
  }));

  expect(fetchCalls).toBe(0);
  expect(result).toMatchObject({ completedCount: 1, totalCount: 1 });
  expect(result.records[0]).toMatchObject({
    source: "m3-job",
    taskId: "job-1",
    outputId: "audit-result-1",
    platform: "抖音",
    author: "内容作者甲",
    decisionLabel: "需复核",
    riskLabel: "高风险",
    summary: "视频包含稳赚承诺和站外引流，建议复核。",
    evidenceCounts: { text: 0, ocr: 0, asr: 1, comment: 0, vision: 0 }
  });
});

test("published M3 records fail closed when the ReportVersion has no structured report", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: "ReportVersion has no structured frontend report document"
  }), {
    status: 400,
    headers: { "Content-Type": "application/json" }
  });

  await expect(loadM3AnalysisRecords(run("PUBLISHED", {
    report_status: "published",
    report_version_id: "report-version-without-structured-report",
    task_stats: { ingested_count: 1, completed_analysis_count: 1 }
  }))).rejects.toThrow("ReportVersion has no structured frontend report document");
});

test("authoritative M3 progress is server-backed and reduced motion remains static", () => {
  const cardSource = readFileSync(
    new URL("../src/features/investigation/AgentCollaborationCard.tsx", import.meta.url),
    "utf8"
  );
  const recordsPageSource = readFileSync(
    new URL("../src/features/investigation/AnalysisRecordsPage.tsx", import.meta.url),
    "utf8"
  );
  const styles = readFileSync(
    new URL("../src/styles/investigation-workspace.css", import.meta.url),
    "utf8"
  );
  expect(cardSource).toContain("loadM3AnalysisRecords(run)");
  expect(cardSource).toContain("if (authoritative) return;");
  expect(recordsPageSource).toContain("getInvestigationWorkspaceState(investigationId)");
  expect(recordsPageSource).not.toContain("readStoredAnalysisRecords(investigationId)\n        || createCompletedAnalysisRecords()");
  expect(styles).toContain(".evidence-pipeline-svg.is-done *");
  expect(styles).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*\.evidence-pipeline-svg \*/);
});

test("published report questions use the workspace/run handoff without report_session_id", async () => {
  const requestUrls: string[] = [];
  globalThis.fetch = async (input) => {
    requestUrls.push(String(input));
    return new Response(JSON.stringify({ turn_id: "turn-report-1", status: "running" }), {
      status: 202,
      headers: { "Content-Type": "application/json" }
    });
  };
  const accepted = await sendInvestigationWorkspaceReportTurn(
    "workspace-1",
    "run-1",
    { clientMessageId: "message-1", content: "报告中有哪些风险？" }
  );
  const resumed = await resumeInvestigationWorkspaceReportTurn(
    "workspace-1",
    "run-1",
    "turn-report-1"
  );
  expect(requestUrls).toEqual([
    "http://api.test/api/investigation-workspaces/workspace-1/runs/run-1/report-turns",
    "http://api.test/api/investigation-workspaces/workspace-1/runs/run-1/report-turns/turn-report-1/resume"
  ]);
  expect(JSON.stringify(accepted)).not.toContain("session_id");
  expect(JSON.stringify(resumed)).not.toContain("session_id");
});

test("published M3 report detail uses the Principal-scoped workspace/run route", async () => {
  let requestUrl = "";
  globalThis.fetch = async (input) => {
    requestUrl = String(input);
    return new Response(JSON.stringify({
      report_version_id: "report-version-1",
      report_id: "report-1",
      task_id: "run-1",
      version_number: 1,
      status: "published",
      title: "调查报告",
      published_at: "2026-08-28T00:00:00Z",
      presentation: {
        presentation_version: "human-report-v1",
        title: "调查报告",
        summary: { text: "摘要" },
        key_metrics: [],
        sections: [],
        case_blocks: [],
        conclusion: { text: "结论" },
        data_quality_note: { text: "说明" }
      }
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const report = await getInvestigationWorkspacePublishedReport("workspace-1", "run-1");
  expect(report.report_version_id).toBe("report-version-1");
  expect(requestUrl).toBe(
    "http://api.test/api/investigation-workspaces/workspace-1/runs/run-1/published-report"
  );
  expect(requestUrl).not.toContain("report_session_id");
});
