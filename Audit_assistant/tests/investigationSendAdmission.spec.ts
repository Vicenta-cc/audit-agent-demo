import { expect, test } from "@playwright/test";
const a = "investigation-session:admission-a", b = "investigation-session:admission-b";
const now = "2026-10-16T10:00:00Z";
for (const destination of ["existing", "new"]) for (const send of ["click", "enter"]) {
  test(`A pending → ${destination} B retains Chinese input, then sends once (${send})`, async ({ page }) => {
    const posts: { session: string; content: string }[] = [];
    await page.addInitScript(() => {
      class Source extends EventTarget {
        constructor(public url: string) { super(); ((window as any).sources ||= []).push(this); }
        close() {}
      }
      Object.defineProperty(window, "EventSource", { value: Source });
    });
    await page.route("**/api/**", async route => {
      const req = route.request(), path = decodeURIComponent(new URL(req.url()).pathname);
      if (path === "/api/auth/config" || path === "/api/me/task-quota") return route.fulfill({ json: { enabled: false } });
      if (path === "/api/investigation-workspaces" && req.method() === "POST") return route.fulfill({ json: { workspace_session_id: b, status: "active", created_at: now, updated_at: now } });
      if (path === "/api/investigation-workspaces") return route.fulfill({ json: {
        items: [a, ...(destination === "existing" ? [b] : [])].map((id, index) => ({ workspace_session_id: id, title: index ? "会话B" : "会话A", updated_at: now, run_status: "", presentation_stage: "" })), has_more: false
      } });
      if (path.endsWith("/state")) {
        const id = path.split("/").at(-2);
        return route.fulfill({ json: {
          workspace: { workspace_session_id: id, status: "active", created_at: now, updated_at: now },
          messages: [{ message_id: `q-${id}`, turn_id: "fixture", role: "user", content: id === a ? "会话A" : "会话B", created_at: now }],
          latest_turn: id === a ? { turn_id: "turn-a", status: "running", stage: "answering" } : null,
          draft_artifact: null, run: null, report_messages: [], latest_report_turn: null, activity_events: []
        } });
      }
      if (path.endsWith("/turns") && req.method() === "POST") {
        posts.push({ session: path.split("/").at(-2)!, content: req.postDataJSON().content });
        return route.fulfill({ json: { turn_id: "turn-b", status: "accepted", updated_at: now } });
      }
      if (path.startsWith("/api/investigation-workspace-turns/")) return route.fulfill({ json: { turn_id: path.split("/").at(-1), status: "running" } });
      return route.fulfill({ json: { items: [] } });
    });
    await page.goto(`/investigation/${encodeURIComponent(a)}`);
    await expect(page.locator("textarea")).toBeDisabled();
    if (destination === "new") await page.getByRole("button", { name: "新建调查", exact: true }).first().click();
    else await page.getByRole("complementary", { name: "调查会话侧边栏" }).getByText("会话B", { exact: true }).click();
    await expect(page).toHaveURL(new RegExp(encodeURIComponent(b)));
    const input = page.locator("textarea"), button = page.getByRole("button", { name: "发送消息", exact: true });
    await expect(input).toBeEnabled();
    const content = "帮我调查抖音上的色情引流服务，开启精选。暂不启动。";
    await input.fill(content);
    await expect(page.getByRole("status")).toContainText("另一个会话正在处理");
    await expect(button).toBeDisabled();
    await expect(page.getByText("正在整理回答", { exact: true })).toHaveCount(0);
    if (send === "enter") await input.press("Enter");
    else await button.evaluate((element: HTMLButtonElement) => element.click());
    await expect(input).toHaveValue(content);
    expect(posts).toEqual([]);
    await page.evaluate(() => (window as any).sources.find((source: any) => source.url.includes("turn-a"))
      .dispatchEvent(new MessageEvent("turn", { data: JSON.stringify({
        event_id: "done-a", turn_id: "turn-a", sequence: 2, occurred_at: "2026-10-16T10:00:01Z",
        stage: "completed", answer: "A完成", safe_message: "", retryable: false
      }) })));
    await expect(button).toBeEnabled();
    if (send === "enter") await input.press("Enter"); else await button.click();
    await expect(input).toHaveValue("");
    await expect.poll(() => posts.length).toBe(1);
    expect(posts).toEqual([{ session: b, content }]);
    await expect(button).toBeDisabled();
  });
}
for (const reason of ["", "当前会话正在处理，请稍后发送。", "另一个会话正在保存调查范围，请稍后发送。"]) {
  test(`rejected composer input survives both send paths: ${reason || "callback race"}`, async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/");
    await page.evaluate(async reason => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { InvestigationCenterArea } = await load("/src/features/investigation/InvestigationCenterArea.tsx");
      const { restoreInvestigationWorkspace } = await load("/src/features/investigation/workspaceRecovery.ts");
      const { mountPresentation } = await load("/tests/presentationMount.tsx");
      const session = restoreInvestigationWorkspace({
        workspace: { workspace_session_id: "composer", status: "active", created_at: "2026-10-16T10:00:00Z", updated_at: "2026-10-16T10:00:00Z" },
        messages: [], latest_turn: null, draft_artifact: null, run: null, report_messages: [], latest_report_turn: null, activity_events: []
      });
      const host = document.createElement("div"); document.body.replaceChildren(host);
      const noop = () => {}; (window as any).calls = 0;
      mountPresentation(host, React.createElement(InvestigationCenterArea, {
        session, isSendingMessage: false, sendBlockedReason: reason, isSidebarCollapsed: true,
        onToggleSidebar: noop, onUpdateDraftKeywords: noop, onRunControlAccepted: noop,
        onUpdateDraftPlatforms: noop, onGenerateTaskConfig: noop, onStartAgentExecution: noop,
        onPhaseChange: noop, onSendMessage: () => { (window as any).calls++; return false; },
        onOpenDrawer: noop, onOpenReportSupport: noop, onExamplePromptSelect: noop
      }));
    }, reason);
    const input = page.locator("textarea"), button = page.getByRole("button", { name: "发送消息", exact: true });
    await input.fill("保留这段中文，不应静默丢失"); await input.press("Enter");
    await expect(input).toHaveValue("保留这段中文，不应静默丢失");
    if (!reason) await button.click();
    else { await expect(button).toBeDisabled(); await expect(page.getByRole("status")).toContainText(reason); }
    await expect(input).toHaveValue("保留这段中文，不应静默丢失");
    expect(await page.evaluate(() => (window as any).calls)).toBe(reason ? 0 : 2);
  });
}
