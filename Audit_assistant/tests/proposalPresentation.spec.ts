import { expect, test } from "@playwright/test";

for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
  test(`Legacy proposal renders readable Markdown at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    const text = "模型说明：只有一条规则。\n\n临时研判规则 · 完整规则快照\n名称：招聘诈骗\n版本：2\n分类数：1；规则数：2\n规则 1：入职前收费\n命中条件：明确要求付款\n规则 2：培训贷\n应用阶段：image_evidence, comment_audit\n规则豁免：无\n裁决说明：保留 <tag> https://example.test ``` draft_id\n" + "a".repeat(64);
    await page.evaluate(async (content) => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
      const { InvestigationCenterArea } = await load("/src/features/investigation/InvestigationCenterArea.tsx");
      const { restoreInvestigationWorkspace } = await load("/src/features/investigation/workspaceRecovery.ts");
      const session = restoreInvestigationWorkspace({
        workspace: { workspace_session_id: "presentation-test", status: "active",
          created_at: "2026-09-07T00:00:00Z", updated_at: "2026-09-07T00:00:00Z" },
        messages: [{ message_id: "actual-answer", turn_id: "actual-turn", role: "assistant",
          content, sequence: 2, created_at: "2026-09-07T00:00:00Z",
          artifact: { artifact_type: "ruleset_proposal_presentation",
            proposal_presentations: [{ assistant_message_id: "actual-answer", text: content }] } }],
        latest_turn: null, draft_artifact: null, run: null, report_messages: [],
        report_latest_turn: null, published_report: null
      });
      const host = document.createElement("div");
      document.body.replaceChildren(host);
      const noop = () => {};
      createRoot(host).render(React.createElement(InvestigationCenterArea, {
        session, isSendingMessage: false, isSidebarCollapsed: true,
        onToggleSidebar: noop, onUpdateDraftKeywords: noop, onUpdateCreationSearchTerms: async () => {},
        onUpdateDraftPlatforms: noop, onGenerateTaskConfig: noop, onStartAgentExecution: noop,
        onPhaseChange: noop, onSendMessage: noop, onOpenDrawer: noop,
        onOpenReportSupport: noop, onExamplePromptSelect: noop
      }));
    }, text);
    const rendered = page.locator('.inv-assistant-markdown').filter({ hasText: "完整规则快照" });
    await expect(rendered).toBeVisible();
    expect(await rendered.textContent()).toContain("规则 1：入职前收费");
    expect(await rendered.textContent()).toContain("规则 2：培训贷");
    expect(await rendered.locator("tag, script").count()).toBe(0);
    expect(await rendered.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/m3-t4a-proposal-${viewport.width}.png`, fullPage: true });
  });
}
