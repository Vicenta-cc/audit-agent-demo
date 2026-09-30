import { expect, test } from "@playwright/test";

// 2026-09-30: the card shows only what executes. No 并发/分析批次; creator tasks say 本任务最多.
for (const mode of ["search", "creator"] as const) {
  test(`confirmation card limit wording for ${mode} tasks`, async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/");
    await page.evaluate(async (mode) => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
      const { TaskConfirmationCard } = await load("/src/features/investigation/TaskConfirmationCard.tsx");
      const parameters = {
        search_sort: "general", start_page: 1, max_notes: 1, max_total_notes: 10, max_comments: 300,
        collect_comments: true, get_sub_comment: false, collect_media: true, max_items_per_minute: 5,
        max_concurrency: 1, auto_analyze: true, analyze_limit: 10, analysis_batch_size: 5
      };
      const host = document.createElement("div");
      document.body.replaceChildren(host);
      createRoot(host).render(React.createElement(TaskConfirmationCard, {
        draft: {
          taskName: "限额核对", taskType: "平台话题采集", subject: "bc料", platforms: ["dy"],
          keywords: mode === "search" ? ["bc料"] : [], matchedRuleSet: "规则", ruleSetDescription: "",
          status: "等待确认", confirmed: false
        },
        preview: {
          mode, title: "限额核对", objective: "核对", platform: "dy",
          resolved_search_terms: mode === "search" ? ["bc料"] : [],
          creator_url: mode === "creator" ? "https://www.douyin.com/user/MS4wLjABAAAA-valid" : "",
          recall_plan: { strategy: "none" }, ruleset_revision: null, temporary_ruleset: null,
          max_notes: mode === "search" ? 1 : 10, estimated_max_contents: mode === "search" ? 1 : 10,
          max_posts_per_keyword: mode === "search" ? 1 : 10, max_comments_per_post: 300, get_sub_comment: false,
          effective_parameters: parameters, blockers: [], can_confirm: true
        },
        onOpenConfig: () => {}, onStartExecution: () => {}, onOpenKeywordEditor: () => {}
      }));
    }, mode);

    const card = page.getByRole("region", { name: "最终任务确认卡" });
    const scope = card.getByText("每帖最多 300 条评论", { exact: false });
    await expect(scope).toContainText(mode === "creator" ? "本任务最多 10 条" : "每词最多 1 条");
    await expect(scope).not.toContainText(mode === "creator" ? "每词" : "本任务");
    const settings = page.getByLabel("本次使用的统一设置");
    await expect(settings).toContainText("5 条/分钟");
    await expect(settings).not.toContainText("并发");
    await expect(settings).not.toContainText("分析批次");
  });
}
