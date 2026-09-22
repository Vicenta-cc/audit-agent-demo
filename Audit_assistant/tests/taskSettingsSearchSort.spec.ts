import { expect, test } from "@playwright/test";

test("task settings exposes and saves Douyin search sorting", async ({ page }) => {
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { TaskParametersForm } = await load(
      "/src/features/investigation/TaskParametersForm.tsx"
    );
    const saves: Array<Record<string, unknown>> = [];
    Object.assign(window, { taskSettingsSearchSortTest: { saves } });
    const parameters = {
      crawler_account_id: null,
      search_sort: "general",
      start_page: 1,
      max_notes: 5,
      max_total_notes: 5,
      max_comments: 300,
      collect_comments: true,
      get_sub_comment: false,
      collect_media: true,
      max_items_per_minute: 5,
      max_concurrency: 1,
      auto_analyze: true,
      analyze_limit: 5,
      analysis_batch_size: 5
    };
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(TaskParametersForm, {
      preview: {
        requested_parameters: parameters,
        effective_parameters: parameters,
        platform: "dy",
        mode: "search",
        resolved_search_terms: ["bc料"],
        estimated_max_contents: 5
      },
      globalSettings: true,
      onDirty: () => {},
      onSave: async (next: Record<string, unknown>) => { saves.push(next); }
    }));
  });

  const sort = page.getByRole("combobox", { name: "抖音搜索排序" });
  await expect(sort).toHaveValue("general");
  await sort.selectOption("latest");
  await page.getByRole("button", { name: "保存统一设置" }).click();

  await expect.poll(() => page.evaluate(() => (
    window as unknown as { taskSettingsSearchSortTest: { saves: Array<Record<string, unknown>> } }
  ).taskSettingsSearchSortTest.saves.at(-1)?.search_sort)).toBe("latest");
  await expect(page.getByLabel("已保存的实际生效参数")).toContainText("抖音搜索综合排序");
  await page.screenshot({ path: "/tmp/xhs-task-settings-search-sort-20260922.png", fullPage: true });
});
