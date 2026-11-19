import { expect, test } from "@playwright/test";
import { riskSummaryPresentation } from "../src/features/investigation/InvestigationSidebar";

test("risk summary presentation always returns high, medium and low counts", () => {
  expect(riskSummaryPresentation({ status: "risk", high_count: 1, medium_count: 0, low_count: 2 })).toEqual([
    { label: "高", count: 1, tone: "high" },
    { label: "中", count: 0, tone: "medium" },
    { label: "低", count: 2, tone: "low" }
  ]);
  expect(riskSummaryPresentation({ status: "safe", high_count: 0, medium_count: 0, low_count: 0 })).toEqual([
    { label: "高", count: 0, tone: "high" },
    { label: "中", count: 0, tone: "medium" },
    { label: "低", count: 0, tone: "low" }
  ]);
});

test("workspace sidebar renders risk-level circles without review copy and preserves them after recovery", async ({ page }) => {
  const now = "2026-11-09T10:00:00+08:00";
  const riskId = "investigation-session:risk";
  const mediumId = "investigation-session:medium";
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
          { workspace_session_id: riskId, title: "风险会话", updated_at: now, run_status: "FAILED", presentation_stage: "", risk_summary: { status: "risk", high_count: 1, medium_count: 0, low_count: 2 } },
          { workspace_session_id: mediumId, title: "中风险会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "risk", high_count: 0, medium_count: 3, low_count: 0 } },
          { workspace_session_id: safeId, title: "安全会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "safe", high_count: 0, medium_count: 0, low_count: 0 } },
          { workspace_session_id: emptyId, title: "无数据会话", updated_at: now, run_status: "AUDIT_COMPLETED", presentation_stage: "", risk_summary: { status: "empty", high_count: 0, medium_count: 0, low_count: 0 } },
          { workspace_session_id: brokenId, title: "异常会话", updated_at: now, run_status: "", presentation_stage: "", read_error: "WORKSPACE_READ_FAILED", risk_summary: { status: "empty", high_count: 0, medium_count: 0, low_count: 0 } }
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
        run: {
          run_id: "run-risk",
          draft_id: "draft-risk",
          draft_revision: 1,
          status: "FAILED",
          job_id: "job-risk",
          crawl_status: "stopped",
          analysis_status: "failed",
          task_stats: {},
          audit_results: [],
          report_status: "cancelled",
          report_version_id: "",
          error_code: "audit_failed",
          error_message: "",
          created_at: now,
          updated_at: now,
          started_at: now,
          completed_at: now
        },
        report_messages: [],
        latest_report_turn: null,
        activity_events: []
      } });
    }
    return route.fulfill({ json: { items: [] } });
  });

  await page.goto(`/investigation/${encodeURIComponent(riskId)}`);
  const sidebar = page.getByRole("complementary", { name: "调查会话侧边栏" });
  const riskSession = sidebar.locator(".inv-session-item").filter({ hasText: "风险会话" }).first();
  await expect(riskSession.getByText("调查失败", { exact: true })).toBeVisible();
  await expect(riskSession.locator(".inv-risk-level-label")).toHaveCount(0);
  await expect(riskSession.locator(".inv-risk-level-count")).toHaveText(["1", "0", "2"]);
  await expect(riskSession.locator(".inv-risk-level-count").nth(0)).toHaveAttribute("aria-label", "高风险 1");
  await expect(riskSession.locator(".inv-risk-level-count").nth(1)).toHaveAttribute("aria-label", "中风险 0");
  await expect(riskSession.locator(".inv-risk-level-count").nth(2)).toHaveAttribute("aria-label", "低风险 2");
  await expect(riskSession.locator(".inv-risk-level-count.is-zero")).toHaveCount(1);
  await expect(riskSession.locator(".inv-risk-level-count.is-zero")).toHaveCSS("background-color", "rgb(184, 190, 198)");
  await expect(riskSession.locator(".inv-risk-level-count.is-zero")).toHaveCSS("color", "rgb(255, 255, 255)");
  await expect(riskSession.locator(".inv-risk-levels")).toHaveCSS("border-radius", "999px");
  await expect(riskSession.locator(".inv-risk-level-count").first()).toHaveCSS("width", "14px");
  await expect(riskSession.locator(".inv-risk-level-count").first()).toHaveCSS("height", "14px");
  await expect(riskSession.locator(".inv-risk-level-number").first()).toHaveCSS("font-size", "9px");
  await expect(riskSession.locator(".inv-risk-level-number").first()).toHaveCSS("transform", "matrix(1, 0, 0, 1, 0, 0.35)");
  await expect(riskSession.locator(".inv-risk-level.is-high .inv-risk-level-count")).toHaveCSS("background-color", "rgb(240, 107, 122)");

  const mediumSession = sidebar.locator(".inv-session-item").filter({ hasText: "中风险会话" });
  await expect(mediumSession.locator(".inv-risk-level-count")).toHaveText(["0", "3", "0"]);
  await expect(mediumSession.locator(".inv-risk-level-count.is-zero")).toHaveCount(2);

  for (const title of ["安全会话", "无数据会话"]) {
    const session = sidebar.locator(".inv-session-item").filter({ hasText: title });
    await expect(session.locator(".inv-risk-level-count")).toHaveText(["0", "0", "0"]);
    await expect(session.locator(".inv-risk-level-count.is-zero")).toHaveCount(3);
  }
  await expect(sidebar.getByText("待复核", { exact: true })).toHaveCount(0);

  const brokenSession = sidebar.locator(".inv-session-item").filter({ hasText: "异常会话" });
  await expect(brokenSession.getByText("读取异常", { exact: true })).toBeVisible();
  await expect(brokenSession.locator(".inv-risk-levels")).toHaveCount(0);

  await expect(page.locator(".inv-msg-user-bubble").getByText("风险会话", { exact: true })).toBeVisible();
  await expect(riskSession.locator(".inv-risk-level-count")).toHaveText(["1", "0", "2"]);
});
