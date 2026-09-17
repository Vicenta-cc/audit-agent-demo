import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import {
  clearHistoricalPendingTurn,
  readHistoricalPendingTurn,
  storeHistoricalPendingTurn
} from "../src/features/investigation/historicalReportPending";
import { buildHistoricalReportSession } from "../src/features/investigation/historicalReportWorkspace";
import {
  fetchHistoricalReportWorkspaces,
  resumeHistoricalReportTurn,
  sendHistoricalReportTurn
} from "../src/services/historicalReports";
import type { HistoricalReportWorkspace } from "../src/types/historicalReports";
import type { PublishedReportDetail } from "../src/types/reports";

const storage = new Map<string, string>();

test.beforeEach(() => {
  storage.clear();
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      XHS_AUDIT_API_BASE: "http://api.test",
      location: { origin: "http://ui.test" },
      sessionStorage: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
        removeItem: (key: string) => storage.delete(key)
      }
    }
  });
});

function workspace(): HistoricalReportWorkspace {
  return {
    workspace_id: "historical-report-a",
    run_id: "historical-report-run-a",
    title: "我好累心好累监控任务",
    task_id: "3ad102e072f6",
    report_version_id: "report-version:8c355a5ba03f45619795813af83ac669",
    run_status: "PUBLISHED",
    import_semantics: "historical",
    draft: {
      task_name: "我好累心好累监控任务",
      subject: "调查评论风险",
      platform: "dy",
      search_terms: ["我好累心好累"],
      analysis_plan: "专题研判方案",
      analysis_description: "按冻结资料研判",
      recall_lexicons: ["召回词库"]
    },
    display_timeline: [
      { id: "display-1", kind: "user_request", occurred_at: "2026-08-25T00:00:00Z", content: "发起调查" },
      { id: "display-2", kind: "plan_recommendation", occurred_at: "2026-08-25T00:01:00Z", content: "推荐方案" },
      { id: "display-3", kind: "user_confirmation", occurred_at: "2026-08-25T00:02:00Z", content: "确认执行" },
      { id: "display-4", kind: "processing_update", occurred_at: "2026-08-25T00:03:00Z", content: "完成研判" },
      { id: "display-5", kind: "report_ready", occurred_at: "2026-08-25T00:04:00Z", content: "报告已生成" }
    ],
    conversation: [{
      message_id: "real-user-1",
      turn_id: "turn-1",
      role: "user",
      content: "真实问题",
      sequence: 1,
      created_at: "2026-08-25T00:05:00Z"
    }, {
      message_id: "real-assistant-1",
      turn_id: "turn-1",
      role: "assistant",
      content: "真实回答",
      sequence: 5,
      created_at: "2026-08-25T00:06:00Z"
    }],
    latest_turn: null
  };
}

function report(): PublishedReportDetail {
  return {
    report_version_id: "report-version:8c355a5ba03f45619795813af83ac669",
    report_id: "report:demo-a",
    task_id: "3ad102e072f6",
    version_number: 1,
    status: "published",
    title: "我好累心好累监控任务调查报告",
    published_at: "2026-08-25T00:04:00Z",
    presentation: {
      presentation_version: "human-report-v1",
      title: "我好累心好累监控任务调查报告",
      summary: { text: "冻结报告摘要" },
      key_metrics: [{ label: "分析内容", value: "201条", detail: "" }],
      sections: [{
        section_id: "finding-1",
        title: "年龄、外貌及婚史羞辱",
        paragraphs: [{ text: "报告正式发现。" }]
      }],
      case_blocks: [],
      conclusion: { text: "报告结论" },
      data_quality_note: { text: "" }
    }
  };
}

test("historical report API uses only workspace-scoped public routes", async () => {
  const requests: Array<{ url: string; method: string; body: string }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({
      url: String(input),
      method: init?.method || "GET",
      body: String(init?.body || "")
    });
    const payload = String(input).endsWith("/resume")
      || init?.method === "POST"
      ? { turn_id: "turn-1", status: "running" }
      : { items: [] };
    return new Response(JSON.stringify(payload), {
      status: init?.method === "POST" ? 202 : 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  await fetchHistoricalReportWorkspaces();
  await sendHistoricalReportTurn("historical-report-a", {
    clientMessageId: "client-1",
    content: "报告问题"
  });
  await resumeHistoricalReportTurn("historical-report-a", "turn-1");

  expect(requests.map((item) => item.url)).toEqual([
    "http://api.test/api/historical-report-workspaces",
    "http://api.test/api/historical-report-workspaces/historical-report-a/turns",
    "http://api.test/api/historical-report-workspaces/historical-report-a/turns/turn-1/resume"
  ]);
  expect(JSON.parse(requests[1].body)).toEqual({
    client_message_id: "client-1",
    content: "报告问题"
  });
  expect(JSON.stringify(requests)).not.toContain("report_session_id");
  expect(JSON.stringify(requests)).not.toContain("/api/investigation-sessions/");
});

test("Display Timeline remains separate from the real report conversation", () => {
  const session = buildHistoricalReportSession(workspace(), report());
  expect(session.messages.slice(0, 5).map((message) => message.id)).toEqual([
    "display-1", "display-2", "display-3", "display-4", "display-5"
  ]);
  expect(session.messages[5].type).toBe("report_card");
  expect(session.messages.slice(6).map((message) => message.id)).toEqual([
    "real-user-1", "real-assistant-1"
  ]);
  expect(session.reportBinding).not.toHaveProperty("investigationSessionId");
  expect(JSON.stringify(session.reportBinding)).not.toContain("report_session_id");

  const centerSource = readFileSync(
    new URL("../src/features/investigation/InvestigationCenterArea.tsx", import.meta.url),
    "utf8"
  );
  expect(centerSource).toContain('msg.type === "grounded_answer"');
  expect(centerSource).toContain("<AssistantMarkdown");
  expect(centerSource).toContain('className="inv-msg-asst-card inv-report-answer-pending-wrap"');
  expect(centerSource).toContain('<div className="inv-msg-user-bubble">{msg.content}</div>');
  expect(centerSource).toContain('behavior: showHistoricalPending ? "auto" : "smooth"');
});

test("both historical workspaces keep isolated real Assistant answer rendering", () => {
  const workspaceA = workspace();
  const workspaceB: HistoricalReportWorkspace = {
    ...workspaceA,
    workspace_id: "historical-report-b",
    run_id: "historical-report-run-b",
    title: "麦热依姆古丽监控任务2",
    task_id: "8bc179209e1e",
    report_version_id: "report-version:4e3ebeccd2ed4f0c9c9a750332d22585",
    conversation: workspaceA.conversation.map((message) => ({
      ...message,
      message_id: `${message.message_id}-b`,
      turn_id: "turn-b"
    }))
  };
  const reportA = report();
  const reportB: PublishedReportDetail = {
    ...reportA,
    report_version_id: workspaceB.report_version_id,
    report_id: "report:demo-b",
    task_id: workspaceB.task_id,
    title: "麦热依姆古丽监控任务2调查报告"
  };

  const sessionA = buildHistoricalReportSession(workspaceA, reportA);
  const sessionB = buildHistoricalReportSession(workspaceB, reportB);
  expect(sessionA.id).toBe("historical-report-a");
  expect(sessionB.id).toBe("historical-report-b");
  expect(sessionA.reportBinding?.reportVersionId).toBe(workspaceA.report_version_id);
  expect(sessionB.reportBinding?.reportVersionId).toBe(workspaceB.report_version_id);
  expect(sessionA.messages.at(-1)?.type).toBe("grounded_answer");
  expect(sessionB.messages.at(-1)?.type).toBe("grounded_answer");
});

test("refresh recovery marks the pending Turn without expanding storage", () => {
  const pendingWorkspace = workspace();
  pendingWorkspace.latest_turn = {
    turn_id: "turn-pending",
    status: "running",
    stage: "answering",
    answer: "",
    safe_message: "",
    retryable: true,
    updated_at: "2026-08-25T00:07:00Z",
    event_sequence: 3
  };
  pendingWorkspace.answer_draft = {
    message_id: "public-answer:" + "a".repeat(32),
    revision: 2,
    text: "刷新前已生成的回答",
    event_sequence: 7
  };
  storeHistoricalPendingTurn(pendingWorkspace.workspace_id, {
    client_message_id: "client-pending",
    turn_id: "turn-pending",
    after_sequence: 3
  });

  const session = buildHistoricalReportSession(pendingWorkspace, report());
  expect(session.reportBinding?.pendingTurn).toMatchObject({
    turnId: "turn-pending",
    clientMessageId: "client-pending",
    afterSequence: 7,
    recovering: true,
    answerDraft: {
      revision: 2,
      text: "刷新前已生成的回答",
      event_sequence: 7
    }
  });
  expect(Object.keys(JSON.parse([...storage.values()][0])).sort()).toEqual([
    "after_sequence", "client_message_id", "turn_id"
  ]);
});

test("sessionStorage persists exactly the three pending-turn recovery fields", () => {
  storeHistoricalPendingTurn("historical-report-a", {
    client_message_id: "client-1",
    turn_id: "turn-1",
    after_sequence: 4
  });
  expect(storage.size).toBe(1);
  const value = JSON.parse([...storage.values()][0]);
  expect(Object.keys(value).sort()).toEqual([
    "after_sequence", "client_message_id", "turn_id"
  ]);
  expect(readHistoricalPendingTurn("historical-report-a")).toEqual(value);
  clearHistoricalPendingTurn("historical-report-a");
  expect(storage.size).toBe(0);

  const pageSource = readFileSync(
    new URL("../src/features/investigation/InvestigationPage.tsx", import.meta.url),
    "utf8"
  );
  const pendingSource = readFileSync(
    new URL("../src/features/investigation/historicalReportPending.ts", import.meta.url),
    "utf8"
  );
  expect(pageSource).not.toContain("createInvestigationSession");
  expect(pageSource).not.toContain("INVESTIGATION_SESSIONS_STORAGE_KEY");
  expect(pendingSource).not.toMatch(/transcript|receipt|corpus_revision|account_ref|report_session_id/);
});
