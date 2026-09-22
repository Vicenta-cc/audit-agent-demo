import { expect, test, type Page } from "@playwright/test";

const content = "模型说明\n\n临时研判规则 · 完整规则快照\n规则 1：收费\n规则 2：培训贷\n豁免：无\n<tag> https://example.test ``` draft_id\n" + "a".repeat(64);

async function mount(page: Page, stage: "suggestion" | "confirmation", mixed: boolean, temporary = false, failure = false, ready = false) {
  await page.evaluate(async ({ content, stage, mixed, temporary, failure, ready }) => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { InvestigationCenterArea } = await load("/src/features/investigation/InvestigationCenterArea.tsx");
    const { restoreInvestigationWorkspace } = await load("/src/features/investigation/workspaceRecovery.ts");
    const ruleset = { id: "published-1", name: "现有正式规则", version: 1, enabled_rule_count: 2 };
    const temporaryRuleSet = temporary ? { strategy: "temporary_ruleset", proposal_id: "proposal-1",
      proposal_version: 2, content_hash: "a".repeat(64), content: { name: "招聘诈骗规则", domain: "招聘诈骗",
        categories: [{ rules: [{ enabled: true }] }] } } : null;
    const draft = { id: "draft-1", current_revision: 3, title: "调查草案", objective: "调查招聘风险" };
    const preview = { mode: "search", platform: "xhs", resolved_search_terms: ["招聘"], ruleset_revision: temporary ? null : ruleset,
      temporary_ruleset: temporaryRuleSet, max_notes: 1,
      blockers: temporary && !ready ? [{ code: "TEMPORARY_RULESET_EXECUTION_UNAVAILABLE", message: "T5 pending" }] : [],
      can_confirm: !temporary || ready, recall_plan: { strategy: "temporary_terms" } };
    const artifact = {
      artifact_type: "investigation_draft", presentation_stage: stage, draft_id: draft.id,
      draft_revision: 3, draft, confirmation_preview: preview,
      suggestion: { title: draft.title, objective: draft.objective, selected_platform: "xhs",
        search_terms: ["招聘"], ruleset_revision: temporary ? null : ruleset, temporary_ruleset: temporaryRuleSet, recall_lexicons: [],
        platform_options: [{ id: "xhs", name: "小红书", available: true }, { id: "dy", name: "抖音", available: true }] }
    };
    const state = {
      workspace: { workspace_session_id: "mixed-session", status: "active" },
      messages: [{ message_id: "actual-answer", turn_id: "actual-turn", role: "assistant",
        content: failure ? "本次规则采用操作未成功：已展示的规则版本发生了变化。请重新展示当前版本，并等待下一次用户消息确认。" : mixed ? content : "已生成调查建议", sequence: 2, created_at: "2026-09-07T00:00:00Z",
        artifact: failure ? undefined : { ...artifact, ...(mixed ? { proposal_presentations: [{ assistant_message_id: "actual-answer", text: content }] } : {}) } }],
      latest_turn: null, draft_artifact: failure ? null : artifact, run: null, report_messages: [], latest_report_turn: null
    };
    const session = restoreInvestigationWorkspace(state);
    const events: unknown[] = [];
    Object.assign(window, { mixedTest: { events, messageIds: session.messages.map((m: { id: string }) => m.id) } });
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    const noop = () => {};
    const { mountPresentation } = await load("/tests/presentationMount.tsx");
    mountPresentation(host, React.createElement(InvestigationCenterArea, {
      session, isSendingMessage: false, isSidebarCollapsed: true,
      onToggleSidebar: noop, onUpdateDraftKeywords: noop,
      onUpdateCreationSearchTerms: async (terms: string[]) => { events.push(["update", terms]); },
      onUpdateDraftPlatforms: (platforms: string[]) => events.push(["platform", platforms]),
      onGenerateTaskConfig: (id: string) => events.push(["generate", id]),
      onStartAgentExecution: () => events.push(["confirm"]),
      onPhaseChange: noop, onSendMessage: noop,
      onOpenDrawer: (type: string) => events.push(["drawer", type]),
      onOpenReportSupport: noop, onExamplePromptSelect: noop
    }));
  }, { content, stage, mixed, temporary, failure, ready });
}

async function events(page: Page) {
  return page.evaluate(() => (window as unknown as { mixedTest: { events: unknown[] } }).mixedTest.events);
}

for (const width of [1280, 390]) {
  for (const stage of ["suggestion", "confirmation"] as const) {
    test(`temporary binding ${stage} remains editable and cannot execute at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
      await page.goto("/");
      for (const phase of ["first", "refresh"]) {
        if (phase === "refresh") await page.reload();
        await mount(page, stage, false, true);
        const card = page.getByRole("region", { name: stage === "suggestion" ? "任务建议卡片" : "最终任务确认卡" });
        await expect(card).toBeVisible();
        await expect(card.getByText("本次使用临时规则：招聘诈骗规则", { exact: true })).toBeVisible();
        if (stage === "confirmation") {
          await expect(card.getByRole("button", { name: "确认并开始调查" })).toBeDisabled();
          await expect(card.getByText("本次使用临时规则，暂不支持启动调查。", { exact: true })).toBeVisible();
          await card.getByRole("textbox", { name: "最终搜索词" }).fill("招聘收费");
          await card.getByRole("button", { name: "应用到本次任务" }).click();
          expect(await events(page)).toEqual([["update", ["招聘收费"]]]);
        } else {
          await card.getByRole("button", { name: "抖音", exact: true }).click();
          await card.getByRole("button", { name: "生成任务配置" }).click();
          expect(await events(page)).toEqual([["platform", ["dy"]], ["generate", "actual-answer"]]);
        }
        expect(await card.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
        await page.screenshot({ path: `/tmp/t4b-${stage}-${width}-${phase}.png`, fullPage: true });
      }
    });
  }
}

for (const stage of ["suggestion", "confirmation"] as const) {
  for (const mixed of [false, true]) {
    test(`${mixed ? "mixed" : "Draft-only"} ${stage}: first render, refresh and actions`, async ({ page }) => {
      await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
      await page.goto("/");
      for (const phase of ["first", "refresh"]) {
        if (phase === "refresh") await page.reload();
        await mount(page, stage, mixed);
        const card = page.getByRole("region", { name: stage === "suggestion" ? "任务建议卡片" : "最终任务确认卡" });
        await expect(card).toBeVisible();
        if (mixed) {
          const narrative = page.locator(".inv-assistant-markdown").filter({hasText: "完整规则快照"});
          await expect(narrative).toHaveCount(1);
          for (const phrase of ["模型说明", "规则 1：收费", "规则 2：培训贷", "豁免：无"]) {
            await expect(narrative).toContainText(phrase);
          }
          expect(await narrative.locator("tag, script, iframe").count()).toBe(0);
          const links = await narrative.locator("a").evaluateAll(nodes => nodes.map(node => node.getAttribute("href")));
          expect(links.every(href => href?.startsWith("https://example.test"))).toBe(true);
          expect(await narrative.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
        }
        expect(await page.evaluate(() => (window as unknown as { mixedTest: { messageIds: string[] } }).mixedTest.messageIds)).toEqual(["actual-answer"]);
        if (stage === "suggestion") {
          await card.getByRole("button", { name: "抖音", exact: true }).click();
          await card.getByRole("button", { name: "查看规则详情" }).click();
          await card.getByRole("button", { name: "编辑主词", exact: true }).click();
          await card.getByRole("textbox", { name: "编辑召回词" }).fill("招聘诈骗");
          await card.getByRole("button", { name: "应用到本次任务" }).click();
          await card.getByRole("button", { name: "生成任务配置" }).click();
          expect(await events(page)).toEqual([["platform", ["dy"]], ["drawer", "ruleset"], ["update", ["招聘诈骗"]], ["generate", "actual-answer"]]);
        } else {
          await card.getByRole("textbox", { name: "最终搜索词" }).fill("招聘诈骗");
          await card.getByRole("button", { name: "应用到本次任务" }).click();
          await card.getByRole("button", { name: "查看完整配置" }).click();
          await card.getByRole("button", { name: "确认并开始调查" }).click();
          expect(await events(page)).toEqual([["update", ["招聘诈骗"]], ["drawer", "task_config"], ["confirm"]]);
        }
        if (mixed) await page.screenshot({ path: `/tmp/t4a-mixed-${stage}-${phase}.png`, fullPage: true });
      }
    });
  }
}

for (const width of [1280, 390]) {
  test(`binding failure fallback remains visible after recovery at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/");
    for (const phase of ["first", "refresh"]) {
      if (phase === "refresh") await page.reload();
      await mount(page, "suggestion", false, false, true);
      await expect(page.getByText("本次规则采用操作未成功：已展示的规则版本发生了变化。请重新展示当前版本，并等待下一次用户消息确认。", { exact: true })).toBeVisible();
      await expect(page.getByRole("region", { name: "任务建议卡片" })).toHaveCount(0);
    }
  });
}

for (const width of [390, 1440]) {
  test(`T5 ready temporary Confirm survives refresh at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/");
    for (const phase of ["first", "refresh"]) {
      if (phase === "refresh") await page.reload();
      await mount(page, "confirmation", false, true, false, true);
      const card = page.getByRole("region", { name: "最终任务确认卡" });
      await expect(card.getByText("本次使用临时规则：招聘诈骗规则", { exact: true })).toBeVisible();
      const button = card.getByRole("button", { name: "确认并开始调查" });
      await expect(button).toBeEnabled();
      await button.click();
      expect(await events(page)).toEqual([["confirm"]]);
    }
  });
}
