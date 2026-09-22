import { expect, test } from "@playwright/test";

test("suggestion Draft opens the structured keyword drawer instead of editing inline", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { TaskSuggestionCard } = await load(
      "/src/features/investigation/TaskSuggestionCard.tsx"
    );
    const events: string[] = [];
    Object.assign(window, { keywordEditingTest: { events } });
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(TaskSuggestionCard, {
      assistantContent: "已将实际搜索词识别为用户原文“bc料”。",
      shouldStream: false,
      showSuggestionCard: true,
      onStreamingComplete: () => {},
      draft: {
        taskName: "抖音 bc料抓取 1 条",
        taskType: "平台话题采集",
        subject: "bc料",
        platforms: ["dy"],
        keywords: ["bc料"],
        matchedRuleSet: "赌博博彩风险规则集",
        ruleSetDescription: "已选择发布版本。",
        status: "等待确认",
        confirmed: false
      },
      preview: {
        mode: "search",
        title: "抖音 bc料抓取 1 条",
        objective: "采集并审核 bc料相关内容",
        platform: "dy",
        resolved_search_terms: ["bc料"],
        creator_url: "",
        recall_plan: { strategy: "temporary_terms", terms: ["bc料"], source_lexicon_ids: [] },
        ruleset_revision: { id: "ruleset-1", name: "赌博博彩风险规则集", version: 2, content_hash: "a".repeat(64), enabled_rule_count: 7 },
        temporary_ruleset: null,
        max_notes: 1,
        blockers: [],
        can_confirm: true
      },
      platformOptions: [{ code: "dy", label: "抖音", available: true }],
      isReadOnly: false,
      onUpdatePlatforms: () => {},
      onOpenKeywordEditor: () => { events.push("open-keyword-editor"); },
      onGenerateConfig: () => {},
      onOpenAnalysisPlan: () => {}
    }));
  });

  const card = page.getByRole("region", { name: "任务建议卡片" });
  await expect(card.getByText("bc料", { exact: true })).toBeVisible();
  await expect(card.getByRole("textbox")).toHaveCount(0);
  await card.getByRole("button", { name: "编辑主题与变体" }).click();
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { keywordEditingTest: { events: string[] } }
  ).keywordEditingTest.events)).toEqual(["open-keyword-editor"]);
});

test("started task explains that its search terms are frozen", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { TaskSuggestionCard } = await load(
      "/src/features/investigation/TaskSuggestionCard.tsx"
    );
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(TaskSuggestionCard, {
      assistantContent: "任务已完成。",
      shouldStream: false,
      showSuggestionCard: true,
      onStreamingComplete: () => {},
      draft: {
        taskName: "抖音 bc料抓取 1 条",
        taskType: "平台话题采集",
        subject: "bc料",
        platforms: ["dy"],
        keywords: ["bc料"],
        matchedRuleSet: "赌博博彩风险规则集",
        ruleSetDescription: "已选择发布版本。",
        status: "已创建",
        confirmed: true
      },
      preview: {
        mode: "search",
        title: "抖音 bc料抓取 1 条",
        objective: "采集并审核 bc料相关内容",
        platform: "dy",
        resolved_search_terms: ["bc料"],
        creator_url: "",
        recall_plan: { strategy: "temporary_terms", terms: ["bc料"], source_lexicon_ids: [] },
        ruleset_revision: null,
        temporary_ruleset: null,
        max_notes: 1,
        blockers: [],
        can_confirm: true
      },
      platformOptions: [{ code: "dy", label: "抖音", available: true }],
      isReadOnly: true,
      onUpdatePlatforms: () => {},
      onOpenKeywordEditor: () => {},
      onGenerateConfig: () => {},
      onOpenAnalysisPlan: () => {}
    }));
  });

  const card = page.getByRole("region", { name: "任务建议卡片" });
  await expect(card.getByText("任务已启动，搜索词已冻结；如需修改，请新建调查。", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "编辑主题与变体" })).toHaveCount(0);
});

test("final Draft delegates keyword editing to the structured drawer", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { TaskConfirmationCard } = await load(
      "/src/features/investigation/TaskConfirmationCard.tsx"
    );
    const events: unknown[] = [];
    Object.assign(window, { finalDraftEditingTest: { events } });
    const host = document.createElement("div");
    document.body.replaceChildren(host);

    createRoot(host).render(React.createElement(TaskConfirmationCard, {
        draft: {
          taskName: "抖音 bc料抓取 1 条",
          taskType: "平台话题采集",
          subject: "bc料",
          platforms: ["dy"],
          keywords: ["bc料"],
          matchedRuleSet: "赌博博彩风险规则集",
          ruleSetDescription: "已选择发布版本。",
          status: "等待确认",
          confirmed: false
        },
        preview: {
          mode: "search",
          title: "抖音 bc料抓取 1 条",
          objective: "采集并审核 bc料相关内容",
          platform: "dy",
          resolved_search_terms: ["bc料"],
          creator_url: "",
          recall_plan: { strategy: "temporary_terms", terms: ["bc料"], source_lexicon_ids: [] },
          ruleset_revision: { id: "ruleset-1", name: "赌博博彩风险规则集", version: 2, content_hash: "a".repeat(64), enabled_rule_count: 7 },
          temporary_ruleset: null,
          max_notes: 1,
          blockers: [],
          can_confirm: true
        },
        onOpenConfig: () => events.push("open-config"),
        onStartExecution: () => events.push("start"),
        onOpenKeywordEditor: () => events.push("open-keyword-editor")
      }));
  });

  const card = page.getByRole("region", { name: "最终任务确认卡" });
  const start = card.getByRole("button", { name: "确认并开始调查" });
  await expect(start).toBeEnabled();
  await expect(card.getByRole("textbox")).toHaveCount(0);
  await card.getByRole("button", { name: "编辑主题与变体" }).click();
  await start.click();
  await card.getByRole("button", { name: "查看完整配置" }).click();
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { finalDraftEditingTest: { events: unknown[] } }
  ).finalDraftEditingTest.events)).toEqual([
    "open-keyword-editor",
    "start",
    "open-config"
  ]);
});

test("structured keyword drawer preserves theme bindings and projects enabled variants", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { InvestigationKeywordEditor } = await load(
      "/src/features/investigation/InvestigationKeywordEditor.tsx"
    );
    const events: unknown[] = [];
    Object.assign(window, { structuredKeywordTest: { events } });
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(InvestigationKeywordEditor, {
      draft: {
        taskName: "色情服务调查",
        taskType: "平台话题采集",
        subject: "色情服务",
        platforms: ["dy"],
        keywords: ["非绿地陪"],
        matchedRuleSet: "色情引流规则",
        ruleSetDescription: "已选择发布版本。",
        status: "等待确认",
        confirmed: false
      },
      content: {
        title: "色情服务黑话库",
        risk_label: "色情服务",
        entries: [
          { id: "theme-1", term: "色情服务", kind: "main", parent_id: "", enabled: true, platform: "全平台", match_type: "黑话词", risk_level: "中", note: "" },
          { id: "variant-1", term: "非绿地陪", kind: "variant", parent_id: "theme-1", enabled: true, platform: "全平台", match_type: "黑话词", risk_level: "中", note: "" }
        ]
      },
      fallbackTerms: ["非绿地陪"],
      canEdit: true,
      canSave: true,
      canPublish: true,
      onApply: async (content: unknown) => { events.push(["apply", content]); return true; },
      onSave: async (content: unknown) => { events.push(["save", content]); return { resourceId: "custom-1" }; }
    }));
  });

  await expect(page.getByRole("textbox", { name: "主题主词" })).toHaveValue("色情服务");
  await page.getByRole("textbox", { name: "变体搜索词" }).fill("门槛验牌");
  await expect(page.getByText("门槛验牌", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "应用到本次任务" }).click();
  await expect(page.getByText("已应用到当前 Draft，实际搜索词已按启用变体更新。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存为共享黑话库" }).click();
  await expect(page.getByText(/已保存为正式黑话库/)).toBeVisible();
  const events = await page.evaluate(() => (
    window as unknown as { structuredKeywordTest: { events: unknown[] } }
  ).structuredKeywordTest.events);
  expect(events).toHaveLength(2);
  expect(JSON.stringify(events)).toContain('"parent_id":"theme-1"');
  expect(JSON.stringify(events)).toContain('"term":"门槛验牌"');
});
