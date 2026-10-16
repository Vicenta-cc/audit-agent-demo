import { expect, test } from "@playwright/test";

for (const view of ["/crawler-accounts", "/investigation"]) {
  test(`history loading failure remains scoped at ${view}`, async ({ page }) => {
    await page.route("**/api/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/config") return route.fulfill({ json: { enabled: false } });
      if (path === "/api/historical-report-workspaces") return route.fulfill({ status: 503, json: { detail: "历史归档不可用" } });
      return route.fulfill({ json: { items: [], has_more: false } });
    });
    await page.goto(view);
    if (view === "/investigation") {
      await expect(page.getByText("历史报告读取失败，请刷新后重试。")).toBeVisible();
    } else {
      await expect(page.getByText("采集账号管理").first()).toBeVisible();
      await expect(page.getByText("历史报告读取失败，请刷新后重试。")).toHaveCount(0);
      await expect(page).toHaveURL(/\/crawler-accounts$/);
    }
  });
}

for (const view of ["/crawler-accounts", "/investigation", "late-navigation"]) {
  test(`loaded history only selects a report in investigation: ${view}`, async ({ page }) => {
    let releaseHistory!: () => void;
    const historyReady = new Promise<void>(resolve => { releaseHistory = resolve; });
    if (view !== "late-navigation") releaseHistory();
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/config") return route.fulfill({ json: { enabled: false } });
      if (path === "/api/historical-report-workspaces") await historyReady;
      if (path === "/api/historical-report-workspaces") return route.fulfill({ json: { items: [{
        workspace_id: "historical-report-a", run_id: "run-a", task_id: "task-a", title: "历史报告",
        report_version_id: "report-a", run_status: "PUBLISHED", import_semantics: "historical",
        draft: { task_name: "历史报告", subject: "分析", platform: "dy", search_terms: [], analysis_plan: "", analysis_description: "", recall_lexicons: [] },
        display_timeline: [], conversation: [], latest_turn: null
      }] } });
      if (path.includes("report-versions/report-a")) return route.fulfill({ json: {
        report_version_id: "report-a", report_id: "a", task_id: "task-a", version_number: 1, status: "published",
        title: "历史报告", published_at: "2026-10-10T00:00:00Z",
        presentation: { presentation_version: "human-report-v1", title: "历史报告", summary: { text: "摘要" }, key_metrics: [], sections: [], case_blocks: [], conclusion: { text: "" }, data_quality_note: { text: "" } },
        posts: [], findings: [], source_coverage: {}
      } });
      return route.fulfill({ json: { items: [], has_more: false } });
    });
    await page.goto(view === "late-navigation" ? "/investigation" : view);
    if (view === "late-navigation") {
      await page.getByRole("button", { name: "采集账号", exact: true }).click();
      releaseHistory();
    }
    if (view === "/investigation") await expect(page).toHaveURL(/\/investigation\/historical-report-a$/);
    else {
      await expect(page.getByText("历史报告").first()).toBeVisible();
      await expect(page).toHaveURL(view === "late-navigation" ? /view=crawler-accounts/ : /\/crawler-accounts$/);
    }
  });
}
