import { expect, test } from "@playwright/test";

test("suggestion Draft allows direct main-term editing before task configuration is generated", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { TaskSuggestionCard } = await load(
      "/src/features/investigation/TaskSuggestionCard.tsx"
    );
    const events: string[][] = [];
    Object.assign(window, { keywordEditingTest: { events } });
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(TaskSuggestionCard, {
      assistantContent: "已将实际搜索主词识别为用户原文“bc料”。",
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
      onUpdateSearchTerms: async (terms: string[]) => { events.push(terms); },
      onGenerateConfig: () => {},
      onOpenAnalysisPlan: () => {}
    }));
  });

  const card = page.getByRole("region", { name: "任务建议卡片" });
  await expect(card.getByText("bc料", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "编辑主词" }).click();
  const editor = card.getByRole("textbox", { name: "编辑召回词" });
  await editor.fill("bc料、bc内幕");
  await page.screenshot({ path: "/tmp/xhs-task-suggestion-keyword-edit-20260921.png", fullPage: true });
  await expect(card.getByText("仅修改当前 Draft，不保存到黑话库。", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "应用到本次任务" }).click();
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { keywordEditingTest: { events: string[][] } }
  ).keywordEditingTest.events)).toEqual([["bc料", "bc内幕"]]);
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
      onUpdateSearchTerms: async () => {},
      onGenerateConfig: () => {},
      onOpenAnalysisPlan: () => {}
    }));
  });

  const card = page.getByRole("region", { name: "任务建议卡片" });
  await expect(card.getByText("任务已启动，搜索词已冻结；如需修改，请新建调查。", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "编辑主词" })).toHaveCount(0);
});

test("final Draft only enables start after the edited search terms are persisted", async ({ page }) => {
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

    function Harness() {
      const [terms, setTerms] = React.useState(["bc料"]);
      const saveAttempt = React.useRef(0);
      return React.createElement(TaskConfirmationCard, {
        draft: {
          taskName: "抖音 bc料抓取 1 条",
          taskType: "平台话题采集",
          subject: "bc料",
          platforms: ["dy"],
          keywords: terms,
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
          resolved_search_terms: terms,
          creator_url: "",
          recall_plan: { strategy: "temporary_terms", terms, source_lexicon_ids: [] },
          ruleset_revision: { id: "ruleset-1", name: "赌博博彩风险规则集", version: 2, content_hash: "a".repeat(64), enabled_rule_count: 7 },
          temporary_ruleset: null,
          max_notes: 1,
          blockers: [],
          can_confirm: true
        },
        onOpenConfig: () => events.push("open-config"),
        onStartExecution: () => events.push("start"),
        onUpdateSearchTerms: async (nextTerms: string[]) => {
          saveAttempt.current += 1;
          events.push(["save", saveAttempt.current, nextTerms]);
          if (saveAttempt.current === 1) return false;
          setTerms(nextTerms);
          return true;
        }
      });
    }

    createRoot(host).render(React.createElement(Harness));
  });

  const card = page.getByRole("region", { name: "最终任务确认卡" });
  const editor = card.getByRole("textbox", { name: "最终搜索词" });
  const start = card.getByRole("button", { name: "确认并开始调查" });
  await editor.fill("bc料、bc内幕");
  await expect(start).toBeDisabled();
  await expect(card.getByText("搜索词已修改，请先应用到本次任务；不会保存到黑话库。", { exact: true })).toBeVisible();

  await card.getByRole("button", { name: "应用到本次任务" }).click();
  await expect(start).toBeDisabled();

  await card.getByRole("button", { name: "应用到本次任务" }).click();
  await expect(start).toBeEnabled();
  await expect(card.getByText("已应用到当前 Draft，可以确认并开始调查；不会保存到黑话库。", { exact: true })).toBeVisible();
  await start.click();
  await card.getByRole("button", { name: "查看完整配置" }).click();
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { finalDraftEditingTest: { events: unknown[] } }
  ).finalDraftEditingTest.events)).toEqual([
    ["save", 1, ["bc料", "bc内幕"]],
    ["save", 2, ["bc料", "bc内幕"]],
    "start",
    "open-config"
  ]);
});
