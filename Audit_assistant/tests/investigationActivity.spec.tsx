import { expect, test } from "@playwright/test";
import {
  activityEventsForTurn,
  activityTimelineSummary,
  insertActivityTimelineBeforeLatestAssistant,
  mergeInvestigationActivityEvent
} from "../src/features/investigation/investigationActivity";
import {
  applyInvestigationAnswerReset,
  mergeInvestigationAnswerDelta
} from "../src/features/investigation/investigationAnswer";
import { creationPendingLabel } from "../src/features/investigation/creationPendingPresentation";
import type { InvestigationActivityEvent } from "../src/types/investigations";

function activity(
  sequence: number,
  status: InvestigationActivityEvent["status"],
  overrides: Partial<InvestigationActivityEvent> = {}
): InvestigationActivityEvent {
  return {
    event_id: `investigation-stream-event:${String(sequence).padStart(32, "a")}`,
    turn_id: "investigation-turn:1",
    sequence,
    occurred_at: "2026-09-17T00:00:00Z",
    activity_id: "public-activity:" + "b".repeat(32),
    status,
    label: "读取报告概览",
    summary: status === "succeeded" ? "已读取报告概览。" : "正在执行该步骤。",
    result_count: null,
    ...overrides
  };
}

test("activity updates replace the matching public step without reordering it", () => {
  const started = activity(2, "running");
  const another = activity(3, "running", {
    activity_id: "public-activity:" + "c".repeat(32),
    label: "读取帖子详情"
  });
  const completed = activity(4, "succeeded");

  const first = mergeInvestigationActivityEvent([], started);
  const second = mergeInvestigationActivityEvent(first, another);
  const final = mergeInvestigationActivityEvent(second, completed);

  expect(final).toHaveLength(2);
  expect(final.map((event) => event.label)).toEqual(["读取报告概览", "读取帖子详情"]);
  expect(final[0].status).toBe("succeeded");
  expect(mergeInvestigationActivityEvent(final, started)).toBe(final);
});

test("a successful retry replaces an older failed card for the same public step", () => {
  const failed = activity(2, "failed", {
    activity_id: "public-activity:" + "d".repeat(32),
    label: "生成审核规则草案",
    summary: "该步骤未完成。"
  });
  const unrelated = activity(3, "succeeded", {
    activity_id: "public-activity:" + "e".repeat(32),
    label: "查询可用平台与审核资源",
    summary: "已读取可用平台与审核资源。"
  });
  const succeeded = activity(4, "succeeded", {
    activity_id: "public-activity:" + "f".repeat(32),
    label: "生成审核规则草案",
    summary: "审核规则草案已生成，尚未正式保存。"
  });

  const merged = [failed, unrelated, succeeded].reduce(
    (events, event) => mergeInvestigationActivityEvent(events, event),
    [] as InvestigationActivityEvent[]
  );

  expect(merged.map((event) => [event.label, event.status])).toEqual([
    ["查询可用平台与审核资源", "succeeded"],
    ["生成审核规则草案", "succeeded"]
  ]);
  expect(activityEventsForTurn([failed, unrelated, succeeded], failed.turn_id))
    .toEqual(merged);
});

test("separately named option queries remain separate public steps", () => {
  const catalog = activity(2, "succeeded", {
    activity_id: "public-activity:" + "1".repeat(32),
    label: "查询可用平台与审核资源"
  });
  const terms = activity(3, "succeeded", {
    activity_id: "public-activity:" + "2".repeat(32),
    label: "读取所选黑话库词条"
  });

  const merged = mergeInvestigationActivityEvent(
    mergeInvestigationActivityEvent([], catalog),
    terms
  );
  expect(merged.map((event) => event.label)).toEqual([
    "查询可用平台与审核资源",
    "读取所选黑话库词条"
  ]);
});

test("legacy duplicated option copy is clarified during workspace recovery", () => {
  const first = activity(2, "succeeded", {
    activity_id: "public-activity:" + "3".repeat(32),
    label: "查询可用平台、审核规则和黑话库",
    summary: "已读取可用平台、审核规则和黑话库。"
  });
  const second = activity(3, "succeeded", {
    activity_id: "public-activity:" + "4".repeat(32),
    label: "查询可用平台、审核规则和黑话库",
    summary: "已读取可用平台、审核规则和黑话库。"
  });

  expect(activityEventsForTurn([first, second], first.turn_id).map((event) => [
    event.label,
    event.summary
  ])).toEqual([
    ["查询可用平台与审核资源", "已读取可用平台与审核资源。"],
    ["读取所选审核资源详情", "已读取所选审核资源详情。"]
  ]);
});

test("completed activity timeline is inserted immediately before the answer", () => {
  const events = [activity(4, "succeeded")];
  const messages = insertActivityTimelineBeforeLatestAssistant([
    { id: "question", sender: "user", timestamp: "14:00", content: "报告结论？" },
    { id: "answer", sender: "assistant", timestamp: "14:01", content: "结论。" }
  ], "investigation-turn:1", events);

  expect(messages.map((message) => message.id)).toEqual([
    "question",
    "msg-activity-investigation-turn:1",
    "answer"
  ]);
  expect(messages[1].activityEvents).toEqual(events);
  expect(insertActivityTimelineBeforeLatestAssistant(
    messages,
    "investigation-turn:1",
    events
  )).toBe(messages);
});

test("activity timeline summary distinguishes active, successful and incomplete steps", () => {
  const events = [
    activity(2, "succeeded"),
    activity(3, "running", {
      activity_id: "public-activity:" + "c".repeat(32),
      label: "查询账号跨报告活动"
    })
  ];
  expect(activityTimelineSummary(events, true)).toBe("正在执行 · 2 个步骤");
  expect(activityTimelineSummary([events[0]], false)).toBe("已完成 1 个步骤");
  expect(activityTimelineSummary([
    events[0],
    { ...events[1], sequence: 4, status: "interrupted" }
  ], false)).toBe("已完成 · 1 个成功，1 个未完成");
});

test("answer deltas merge idempotently and a newer revision replaces the draft", () => {
  const envelope = {
    event_id: "investigation-stream-event:" + "e".repeat(32),
    turn_id: "investigation-turn:1",
    occurred_at: "2026-09-17T00:00:02Z",
    message_id: "public-answer:" + "f".repeat(32)
  };
  const first = mergeInvestigationAnswerDelta(undefined, {
    ...envelope,
    sequence: 4,
    revision: 1,
    delta: "第一段"
  });
  const second = mergeInvestigationAnswerDelta(first, {
    ...envelope,
    sequence: 5,
    revision: 1,
    delta: "第二段"
  });
  expect(second.text).toBe("第一段第二段");
  expect(mergeInvestigationAnswerDelta(second, {
    ...envelope,
    sequence: 5,
    revision: 1,
    delta: "重复事件"
  })).toBe(second);

  const reset = applyInvestigationAnswerReset(second, {
    ...envelope,
    sequence: 6,
    revision: 2
  });
  expect(reset.text).toBe("");
  expect(mergeInvestigationAnswerDelta(reset, {
    ...envelope,
    sequence: 7,
    revision: 2,
    delta: "修订后的回答"
  })).toMatchObject({ revision: 2, text: "修订后的回答", event_sequence: 7 });
});

test("active activity timeline is expanded, live and collapsible in the browser", async ({ page }) => {
  await page.route("**/api/**", (route) => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { InvestigationActivityTimeline } = await load(
      "/src/features/investigation/InvestigationActivityTimeline.tsx"
    );
    const events = [{
      event_id: "investigation-stream-event:" + "a".repeat(32),
      turn_id: "investigation-turn:1",
      sequence: 2,
      occurred_at: "2026-09-17T00:00:00Z",
      activity_id: "public-activity:" + "b".repeat(32),
      status: "succeeded",
      label: "读取报告概览",
      summary: "已读取报告概览。",
      result_count: null
    }, {
      event_id: "investigation-stream-event:" + "c".repeat(32),
      turn_id: "investigation-turn:1",
      sequence: 3,
      occurred_at: "2026-09-17T00:00:01Z",
      activity_id: "public-activity:" + "d".repeat(32),
      status: "running",
      label: "查询账号跨报告活动",
      summary: "正在执行该步骤。",
      result_count: null
    }];
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(InvestigationActivityTimeline, {
      events,
      active: true,
      activeLabel: "正在整理回答……"
    }));
  });

  const timeline = page.getByRole("region", { name: "调用过程" });
  await expect(timeline).toContainText("读取报告概览");
  await expect(timeline).toContainText("查询账号跨报告活动");
  await expect(timeline.getByRole("status")).toHaveText("正在整理回答……");
  const toggle = timeline.getByRole("button", { name: /调用过程/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(timeline.getByRole("list")).toHaveCount(0);
});

test("creation pending labels stay neutral for greetings and ordinary questions", () => {
  expect({
    planning: creationPendingLabel("planning"),
    preparing: creationPendingLabel("preparing_sources"),
    acquiring: creationPendingLabel("acquiring_source"),
    answering: creationPendingLabel("answering")
  }).toEqual({
    planning: "正在理解你的问题",
    preparing: "正在准备所需资料",
    acquiring: "正在等待相关信息返回",
    answering: "正在整理回答"
  });
});

test("creation activity and answer draft grow in the same pending assistant card", async ({ page }) => {
  await page.route("**/api/**", (route) => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { InvestigationCenterArea } = await load(
      "/src/features/investigation/InvestigationCenterArea.tsx"
    );
    const { restoreInvestigationWorkspace } = await load(
      "/src/features/investigation/workspaceRecovery.ts"
    );
    const session = restoreInvestigationWorkspace({
      workspace: {
        workspace_session_id: "creation-stream-session",
        status: "active",
        created_at: "2026-09-17T00:00:00Z",
        updated_at: "2026-09-17T00:00:01Z"
      },
      messages: [{
        message_id: "creation-question",
        turn_id: "creation-turn",
        role: "user",
        content: "请创建调查草案",
        sequence: 1,
        created_at: "2026-09-17T00:00:00Z"
      }],
      latest_turn: {
        turn_id: "creation-turn",
        status: "running",
        stage: "answering",
        answer: "",
        safe_message: "",
        retryable: false,
        updated_at: "2026-09-17T00:00:01Z"
      },
      draft_artifact: null,
      run: null,
      report_messages: [],
      latest_report_turn: null,
      activity_events: [{
        event_id: "investigation-stream-event:" + "a".repeat(32),
        turn_id: "creation-turn",
        sequence: 2,
        occurred_at: "2026-09-17T00:00:01Z",
        activity_id: "public-activity:" + "b".repeat(32),
        status: "succeeded",
        label: "查询可用平台与审核资源",
        summary: "已读取可用平台与审核资源。",
        result_count: null
      }],
      creation_answer_draft: {
        message_id: "public-answer:" + "c".repeat(32),
        revision: 1,
        text: "调查方案正在生成，尚未启动任务。",
        event_sequence: 3
      }
    });
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    const noop = () => {};
    const { mountPresentation } = await load("/tests/presentationMount.tsx");
    mountPresentation(host, React.createElement(InvestigationCenterArea, {
      session,
      isSendingMessage: true,
      isSidebarCollapsed: true,
      onToggleSidebar: noop,
      onUpdateDraftKeywords: noop,
      onUpdateCreationSearchTerms: async () => {},
      onUpdateDraftPlatforms: noop,
      onGenerateTaskConfig: noop,
      onStartAgentExecution: noop,
      onPhaseChange: noop,
      onSendMessage: noop,
      onOpenDrawer: noop,
      onOpenReportSupport: noop,
      onExamplePromptSelect: noop
    }));
  });

  const pending = page.locator('[aria-label="调查建议正在生成"]');
  await expect(pending).toHaveText("调查方案正在生成，尚未启动任务。");
  await expect(pending.locator("i")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "调用过程" }))
    .toContainText("查询可用平台与审核资源");
  await expect(page.locator(".inv-creation-pending")).toHaveCount(1);
});
