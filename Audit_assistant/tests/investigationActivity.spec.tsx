import { expect, test } from "@playwright/test";
import {
  activityTimelineSummary,
  insertActivityTimelineBeforeLatestAssistant,
  mergeInvestigationActivityEvent
} from "../src/features/investigation/investigationActivity";
import {
  applyInvestigationAnswerReset,
  mergeInvestigationAnswerDelta
} from "../src/features/investigation/investigationAnswer";
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
