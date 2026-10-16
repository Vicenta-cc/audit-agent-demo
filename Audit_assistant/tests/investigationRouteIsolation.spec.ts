import { expect, test } from "@playwright/test";
const a = "investigation-session:route-a", b = "investigation-session:route-b";
const now = "2026-10-16T12:00:00Z";
const state = (id: string) => ({
  workspace: { workspace_session_id: id, status: "active", created_at: now, updated_at: now },
  messages: [{ message_id: `question-${id}`, turn_id: "completed", role: "user", content: id === b ? "全国服务调查需求已保存" : "地域任务A", created_at: now }],
  latest_turn: null, draft_artifact: null, run: null, report_messages: [], latest_report_turn: null, activity_events: []
});
for (const delayed of ["old-workspace-404", "initial-history"]) for (const selectB of [true, false]) {
  test(`${selectB ? "explicit B remains selected across tabs" : "current route still recovers"} after delayed ${delayed} response`, async ({ page, context }) => {
    let release!: () => void, requested = false, replied = false;
    const wait = new Promise<void>(resolve => { release = resolve; });
    await context.route("**/api/**", async route => {
      const req = route.request(), path = decodeURIComponent(new URL(req.url()).pathname);
      if (path === "/api/auth/config" || path === "/api/me/task-quota") return route.fulfill({ json: { enabled: false } });
      if (path === "/api/investigation-workspaces" && req.method() === "POST") return route.fulfill({ json: state(b).workspace });
      if (path === "/api/investigation-workspaces") return route.fulfill({ json: { items: [a, b].map(id => ({
        workspace_session_id: id, title: id === b ? "待确认B" : "运行任务A", updated_at: now, run_status: "", presentation_stage: ""
      })), has_more: false } });
      if (path.endsWith(`/${a}/state`) && delayed === "old-workspace-404") {
        requested = true; await wait;
        await route.fulfill({ status: 404, json: { detail: "not found" } }); replied = true; return;
      }
      if (path.endsWith("/state")) return route.fulfill({ json: state(path.split("/").at(-2)!) });
      if (path === "/api/historical-report-workspaces") {
        if (delayed !== "initial-history") return route.fulfill({ json: { items: [] } });
        requested = true; await wait;
        return route.fulfill({ json: { items: [{
          workspace_id: "historical-report-old-a", run_id: "old-run", title: "旧历史任务A", task_id: "task-a",
          report_version_id: "report-a", run_status: "PUBLISHED", import_semantics: "historical",
          draft: { task_name: "旧历史任务A", subject: "旧调查", platform: "dy", search_terms: ["旧词"], analysis_plan: "规则", analysis_description: "", recall_lexicons: [] },
          display_timeline: [], conversation: [], latest_turn: null
        }] } });
      }
      if (path === "/api/report-versions/report-a") {
        await route.fulfill({ json: { report_version_id: "report-a", report_id: "report-a", task_id: "task-a", version_number: 1,
          status: "published", title: "旧历史任务A", published_at: now,
          presentation: { presentation_version: "human-report-v1", title: "旧历史任务A", summary: { text: "旧报告" }, key_metrics: [], sections: [], case_blocks: [], conclusion: { text: "旧结论" }, data_quality_note: { text: "" } }
        } }); replied = true; return;
      }
      return route.fulfill({ json: { items: [] } });
    });
    await page.goto(delayed === "initial-history" ? "/investigation" : `/investigation/${encodeURIComponent(a)}`);
    await expect.poll(() => requested).toBe(true);
    if (selectB) {
      if (delayed === "initial-history") await page.getByRole("button", { name: "新建调查", exact: true }).first().click();
      else await page.getByRole("complementary", { name: "调查会话侧边栏" }).getByText("待确认B", { exact: true }).click();
      await expect(page).toHaveURL(new RegExp(encodeURIComponent(b)));
      await expect(page.locator(".inv-msg-user-bubble").getByText("全国服务调查需求已保存", { exact: true })).toBeVisible();
      await page.locator("textarea").fill("B中尚未发送的后续问题");
      // Another tab may load/reconcile A while this tab owns an explicit B URL.
      const tabA = await context.newPage();
      await tabA.goto(`/investigation/${encodeURIComponent(a)}`);
    }
    release(); await expect.poll(() => replied).toBe(true);
    // Drain React effects after the delayed HTTP reply rather than sleeping for a timer.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (!selectB) {
      await expect(page).toHaveURL(delayed === "initial-history" ? /investigation\/historical-report-old-a$/ : /investigation$/);
      return;
    }
    await expect(page).toHaveURL(new RegExp(encodeURIComponent(b)));
    await expect(page.locator("textarea")).toHaveValue("B中尚未发送的后续问题");
    await expect(page.locator(".inv-msg-user-bubble").getByText("全国服务调查需求已保存", { exact: true })).toBeVisible();
  });
}
