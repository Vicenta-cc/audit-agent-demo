import { expect, test } from "@playwright/test";
import { riskSummaryPresentation } from "../src/features/investigation/InvestigationSidebar";

test("risk summary presentation keeps risk, review, safe and empty distinct", () => {
  expect(riskSummaryPresentation({ status: "risk", risk_count: 2, review_count: 1 })).toEqual({
    label: "风险 2 · 待复核 1",
    tone: "risk"
  });
  expect(riskSummaryPresentation({ status: "risk", risk_count: 2, review_count: 0 })).toEqual({
    label: "风险 2",
    tone: "risk"
  });
  expect(riskSummaryPresentation({ status: "review", risk_count: 0, review_count: 3 })).toEqual({
    label: "待复核 3",
    tone: "review"
  });
  expect(riskSummaryPresentation({ status: "safe", risk_count: 0, review_count: 0 })).toEqual({
    label: "未发现风险",
    tone: "safe"
  });
  expect(riskSummaryPresentation({ status: "empty", risk_count: 0, review_count: 0 })).toEqual({
    label: "暂无研判数据",
    tone: "empty"
  });
});

test("workspace sidebar renders one risk badge and preserves it after full recovery", async ({ page }) => {
  const now = "2026-11-09T10:00:00+08:00";
  const riskId = "investigation-session:risk";
  const reviewId = "investigation-session:review";
  const safeId = "investigation-session:safe";
  const emptyId = "investigation-session:empty";
  const brokenId = "investigation-session:broken";

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = decodeURIComponent(new URL(request.url()).pathname);
    if (path === "/api/auth/config" || path === "/api/me/task-quota") {
      return route.fulfill({ json: { enabled: false } });
    }
    if (path === "/api/investigation-workspaces") {
      return route.fulfill({ json: {
        items: [
          { workspace_session_id: riskId, title: "风险会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "risk", risk_count: 2, review_count: 1 } },
          { workspace_session_id: reviewId, title: "复核会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "review", risk_count: 0, review_count: 3 } },
          { workspace_session_id: safeId, title: "安全会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "safe", risk_count: 0, review_count: 0 } },
          { workspace_session_id: emptyId, title: "无数据会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "empty", risk_count: 0, review_count: 0 } },
          { workspace_session_id: brokenId, title: "异常会话", updated_at: now, run_status: "", presentation_stage: "", read_error: "WORKSPACE_READ_FAILED", risk_summary: { status: "empty", risk_count: 0, review_count: 0 } }
        ],
        has_more: false
      } });
    }
    if (path === "/api/historical-report-workspaces") {
      return route.fulfill({ json: { items: [] } });
    }
    if (path.endsWith(`/${riskId}/state`)) {
      return route.fulfill({ json: {
        workspace: { workspace_session_id: riskId, status: "active", created_at: now, updated_at: now },
        messages: [{ message_id: "question-risk", turn_id: "turn-risk", role: "user", content: "风险会话", created_at: now }],
        latest_turn: null,
        draft_artifact: null,
        run: null,
        report_messages: [],
        latest_report_turn: null,
        activity_events: []
      } });
    }
    return route.fulfill({ json: { items: [] } });
  });

  await page.goto(`/investigation/${encodeURIComponent(riskId)}`);
  const sidebar = page.getByRole("complementary", { name: "调查会话侧边栏" });
  await expect(sidebar.getByText("风险 2 · 待复核 1", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("待复核 3", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("未发现风险", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("暂无研判数据", { exact: true })).toHaveCount(1);

  const brokenSession = sidebar.locator(".inv-session-item").filter({ hasText: "异常会话" });
  await expect(brokenSession.getByText("读取异常", { exact: true })).toBeVisible();
  await expect(brokenSession.getByText("暂无研判数据", { exact: true })).toHaveCount(0);

  await expect(page.locator(".inv-msg-user-bubble").getByText("风险会话", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("风险 2 · 待复核 1", { exact: true })).toBeVisible();
});
