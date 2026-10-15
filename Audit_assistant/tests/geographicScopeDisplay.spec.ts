import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import {
  buildConfirmationCardView,
  buildRelevanceScopeView,
  formatConfirmationBlockerMessage
} from "../src/features/investigation/confirmationView";
import { restoreInvestigationWorkspace } from "../src/features/investigation/workspaceRecovery";
import type { TaskDraft } from "../src/types/investigation";
import type {
  ConfirmationPreview,
  InvestigationDraftArtifact,
  InvestigationWorkspaceState,
  RelevanceScope,
  RelevanceScopeState
} from "../src/types/investigationCreation";

const draft: TaskDraft = {
  taskName: "本地程序验证：地域草案", taskType: "平台话题采集", subject: "欠薪舆情",
  platforms: ["dy"], keywords: ["欠薪"], matchedRuleSet: "本地验证规则",
  ruleSetDescription: "", status: "等待确认", confirmed: false
};
const scope: RelevanceScope = {
  schema_version: "geo-scope-v1", target: { name: "克拉玛依市", aliases: ["克拉玛依"] },
  province: { name: "新疆维吾尔自治区", aliases: ["新疆"] }, topic: null,
  selection_policy: "local_first_with_supplement", content_unit: "parent_post_event"
};
const pending: RelevanceScopeState = {
  status: "pending", target_text: "朝阳区", reason: "当前支持省级、地级市和直辖市，区县、街道暂不支持，请提供明确的省市名称。",
  selection_policy: "local_only", candidates: []
};
const provinceScope: RelevanceScope = {
  ...scope, target: { name: "新疆维吾尔自治区", aliases: ["新疆"] }
};
const ambiguousPending: RelevanceScopeState = {
  status: "pending", target_text: "吉林", reason: "吉林可能指吉林省或吉林市，请明确省或市。",
  selection_policy: "local_only", candidates: [
    { name: "吉林省", level: "province", kind: "province", province_name: "吉林省" },
    { name: "吉林市", level: "prefecture", kind: "prefecture_city", province_name: "吉林省" }
  ]
};
function previewFor(state?: RelevanceScopeState, value?: RelevanceScope): ConfirmationPreview {
  const blocked = Boolean(value || state?.status === "pending");
  return {
    draft_id: "geo-draft", draft_revision: 2, title: draft.taskName, objective: draft.subject,
    platform: "dy", mode: "search", resolved_search_terms: ["欠薪"], creator_url: "",
    recall_plan: { strategy: "temporary_terms", lexicon_id: "", lexicon_title: "",
      enabled_main_term_count: 1, enabled_main_terms: ["欠薪"], temporary_terms: ["欠薪"], source_lexicon_ids: [] },
    ruleset_revision: { id: "rules-rev", ruleset_id: "rules", name: "本地验证规则", domain: "local",
      version: 1, content_hash: "test-hash", enabled_rule_count: 1 },
    max_notes: 5,
    blockers: blocked ? [{ code: state?.status === "pending" ? "RELEVANCE_SCOPE_PENDING" : "RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE",
      message: state?.status === "pending" ? "地域要求待澄清，当前不能启动。" : "地域执行能力尚未接通，当前不能启动。", resource_type: "relevance_scope", resource_id: "", latest_safe_summary: {}, management_url: "" }] : [],
    can_confirm: !blocked,
    ...(value ? { relevance_scope: value } : {}), ...(state ? { relevance_scope_state: state } : {})
  };
}
function artifactFor(id: string, preview: ConfirmationPreview): InvestigationDraftArtifact {
  return {
    artifact_type: "investigation_draft", presentation_stage: "confirmation", draft_id: id,
    draft_revision: 2, confirmation_preview: { ...preview, draft_id: id }, suggestion: null,
    draft: {
      id, status: "DRAFT", current_revision: 2, title: draft.taskName, objective: draft.subject,
      created_at: "2026-10-15T00:00:00Z", updated_at: "2026-10-15T00:01:00Z", confirmed_revision: null, confirmed_at: "",
      configuration: { schema_version: "investigation-draft-config-v4", platform: "dy",
        investigation: { mode: "search", recall_plan: { strategy: "temporary_terms", terms: ["欠薪"], source_lexicon_ids: [] } },
        judgement: { strategy: "existing_ruleset", ruleset_revision_id: "rules-rev", expected_ruleset_version: 1, expected_ruleset_content_hash: "test-hash" },
        ...(preview.relevance_scope ? { relevance_scope: preview.relevance_scope } : {}),
        ...(preview.relevance_scope_state ? { relevance_scope_state: preview.relevance_scope_state } : {}) }
    }
  };
}

test("scope display handles canonical province, city, municipality, policies and omitted legacy fields", () => {
  expect(buildRelevanceScopeView()).toBeNull();
  expect(buildRelevanceScopeView({ relevance_scope_state: { status: "cleared", candidates: [] } })).toBeNull();
  expect(buildRelevanceScopeView({ relevance_scope: scope })).toMatchObject({
    target: "克拉玛依市（新疆维吾尔自治区）", policy: "本地优先，不足时按已确认策略补充", pending: false
  });
  expect(buildRelevanceScopeView({ relevance_scope: { ...scope, selection_policy: "local_only" } })?.policy)
    .toBe("仅本地，不补充其他地区");
  for (const [name, alias, province] of [["朝阳市", "朝阳", "辽宁省"], ["吉林市", "吉林", "吉林省"]]) {
    expect(buildRelevanceScopeView({ relevance_scope: { ...scope,
      target: { name, aliases: [alias] }, province: { name: province, aliases: [] } } })?.target)
      .toBe(`${name}（${province}）`);
  }
  expect(buildRelevanceScopeView({ relevance_scope: provinceScope })?.target).toBe("新疆维吾尔自治区");
  expect(buildRelevanceScopeView({ relevance_scope_state: ambiguousPending, relevance_scope: scope }))
    .toMatchObject({ target: "吉林", pending: true, candidates: "吉林省、吉林市（吉林省）" });
  expect(buildRelevanceScopeView({ relevance_scope_state: { status: "pending", candidates: [] } })?.reason)
    .toContain("省级、地级市或直辖市");
  const beijing = { name: "北京市", aliases: ["北京"] };
  expect(buildRelevanceScopeView({ relevance_scope: { ...scope, target: beijing, province: beijing } })?.target).toBe("北京市");
});

test("pending latest requirement hides previously resolved target and server blockers remain readable", () => {
  const view = buildConfirmationCardView(draft, previewFor(pending, scope));
  expect(view.relevanceScope).toMatchObject({ target: "朝阳区", pending: true,
    candidates: "", policy: "仅本地，不补充其他地区" });
  expect(JSON.stringify(view.relevanceScope)).not.toContain("克拉玛依");
  expect(view.canConfirm).toBe(false);
  expect(formatConfirmationBlockerMessage("RELEVANCE_SCOPE_PENDING", "")).toContain("待澄清");
  expect(formatConfirmationBlockerMessage("RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE", "")).toContain("尚未接通");
  expect(formatConfirmationBlockerMessage("RELEVANCE_SCOPE_MODE_UNSUPPORTED", "当前模式暂不支持地域调查。"))
    .toBe("当前模式暂不支持地域调查。");
  for (const code of ["RELEVANCE_SCOPE_PENDING", "RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE", "RELEVANCE_SCOPE_MODE_UNSUPPORTED"]) {
    expect(formatConfirmationBlockerMessage(code, "  受控地域说明  ")).toBe("  受控地域说明  ");
  }
  for (const code of ["FUTURE_SAFE_CODE", "NO_PUBLISHED_AUDIT_POLICY", "RELEVANCE_SCOPE_UNKNOWN"]) {
    expect(formatConfirmationBlockerMessage(code, "internal")).toBe("当前配置暂时无法继续，请调整相关设置后重试。");
  }
  expect(formatConfirmationBlockerMessage("UNKNOWN", " ")).toContain("当前配置暂时无法继续");
});

test("workspace recovery retains pending and resolved projections without freezing blocked draft or polluting old snapshots", () => {
  const oldArtifact = artifactFor("old-no-scope", previewFor());
  for (const currentPreview of [previewFor({ status: "resolved", candidates: [] }, scope), previewFor(pending, scope)]) {
    const current = artifactFor("geo-draft", currentPreview);
    const state: InvestigationWorkspaceState = {
      workspace: { workspace_session_id: "local-test", status: "active", created_at: "2026-10-15T00:00:00Z", updated_at: "2026-10-15T00:02:00Z" },
      messages: [oldArtifact, current].map((artifact, index) => ({ message_id: `assistant-${index}`, turn_id: `turn-${index}`,
        role: "assistant", content: "本地程序测试", artifact, sequence: index, created_at: `2026-10-15T00:0${index}:00Z` })),
      latest_turn: null, draft_artifact: current, run: null, report_messages: [], latest_report_turn: null, activity_events: []
    };
    const recovered = restoreInvestigationWorkspace(JSON.parse(JSON.stringify(state)));
    expect(recovered.draft.confirmed).toBe(false);
    expect(recovered.creationBinding?.draft?.configuration.relevance_scope_state).toEqual(currentPreview.relevance_scope_state);
    expect(recovered.creationBinding?.confirmationPreview).toEqual(current.confirmation_preview);
    expect(buildConfirmationCardView(recovered.draft, recovered.creationBinding?.confirmationPreview).canConfirm).toBe(false);
    expect(recovered.messages[0].confirmationPreview?.relevance_scope).toBeUndefined();
    expect(recovered.messages[0].confirmationPreview?.relevance_scope_state).toBeUndefined();
  }
});

for (const variant of ["resolved", "province", "pending", "ambiguous", "local_only", "legacy"] as const) {
  test(`original card and drawer render ${variant} scope read-only`, async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto("/");
    const preview = variant === "legacy" ? previewFor()
      : variant === "pending" ? previewFor(pending, scope)
      : variant === "ambiguous" ? previewFor(ambiguousPending, scope)
      : variant === "province" ? previewFor({ status: "resolved", candidates: [] }, provinceScope)
      : previewFor({ status: "resolved", candidates: [] }, { ...scope,
        selection_policy: variant === "local_only" ? "local_only" : "local_first_with_supplement" });
    await page.evaluate(async ({ draft, preview }) => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
      const { TaskConfirmationCard } = await load("/src/features/investigation/TaskConfirmationCard.tsx");
      const { InvestigationContextDrawer } = await load("/src/features/investigation/InvestigationContextDrawer.tsx");
      const host = document.createElement("main");
      host.style.cssText = "width:820px;padding:28px;background:#f8fafc;min-height:100vh;";
      document.body.replaceChildren(host);
      createRoot(host).render(React.createElement(React.Fragment, null,
        React.createElement(TaskConfirmationCard, { draft, preview, onOpenConfig: () => {}, onStartExecution: () => {} }),
        React.createElement(InvestigationContextDrawer, { isOpen: true, type: "task_config", draft,
          confirmationPreview: preview, onClose: () => {} })
      ));
    }, { draft, preview });
    const card = page.getByRole("region", { name: "最终任务确认卡" });
    const drawer = page.getByLabel("上下文详情抽屉");
    await expect(card).toBeVisible();
    await expect(drawer).toBeVisible();
    if (variant === "legacy") {
      await expect(card.getByText("调查地域", { exact: true })).toHaveCount(0);
      await expect(card.getByLabel("地域配置说明")).toHaveCount(0);
      await expect(drawer.getByLabel("调查地域配置")).toHaveCount(0);
      await expect(card.getByRole("button", { name: "确认并开始调查" })).toBeEnabled();
    } else {
      await expect(card.getByRole("button", { name: "确认并开始调查" })).toBeDisabled();
      await expect(card.getByText("任务已启动", { exact: false })).toHaveCount(0);
      const details = drawer.getByLabel("调查地域配置");
      if (variant === "ambiguous") {
        await expect(card).toContainText("最新地域要求（待澄清）");
        await expect(card).toContainText("吉林省、吉林市（吉林省）");
        await expect(details).toContainText(ambiguousPending.reason!);
        await expect(details).toContainText("吉林省、吉林市（吉林省）");
        await expect(card).not.toContainText("克拉玛依");
        await expect(details).not.toContainText("克拉玛依");
      } else if (variant === "province") {
        await expect(card).toContainText("新疆维吾尔自治区");
        await expect(card).not.toContainText("新疆维吾尔自治区（新疆维吾尔自治区）");
        await expect(details).toContainText("新疆维吾尔自治区");
        await expect(details).toContainText("地域执行能力尚未接通");
      } else if (variant === "pending") {
        await expect(card).toContainText("最新地域要求（待澄清）");
        await expect(card).toContainText("朝阳区");
        await expect(card).not.toContainText("克拉玛依");
        await expect(details).toContainText(pending.reason!);
        await expect(details).not.toContainText("克拉玛依");
      } else {
        await expect(card).toContainText("克拉玛依市（新疆维吾尔自治区）");
        await expect(details).toContainText("地域执行能力尚未接通");
        await expect(details).toContainText(variant === "local_only" ? "仅本地，不补充其他地区" : "本地优先，不足时按已确认策略补充");
      }
      await expect(details.getByRole("textbox")).toHaveCount(0);
      await expect(details.getByRole("checkbox")).toHaveCount(0);
      await expect(details.getByRole("combobox")).toHaveCount(0);
      if (variant !== "local_only") {
        const evidenceDir = "../experiments/geographic_scope_draft/evidence";
        mkdirSync(evidenceDir, { recursive: true });
        await page.screenshot({ path: `${evidenceDir}/scope-${variant}-card-drawer.png`, fullPage: true });
      }
    }
  });
}


// Both captures use real configure/service/temporary SQLite/public API calls.
// The second capture uses the packaged national catalog without a validator
// patch. Scripted actions prove integration, never AI understanding.
for (const dataset of ["fixture", "national"] as const) {
for (const operation of ["resolved", "pending"] as const) {
  test(`real service ${dataset} ${operation} workspace restores into original card and drawer`, async ({ page }) => {
    const filename = dataset === "national" ? "service-flow-national.json" : "service-flow.json";
    const evidence = JSON.parse(readFileSync(`../experiments/geographic_scope_draft/evidence/${filename}`, "utf8")) as {
      validation: string; catalog: string; model_requests: number;
      steps: Array<{ operation: string; confirmation_preview: ConfirmationPreview; workspace: InvestigationWorkspaceState }>;
    };
    expect(evidence.validation).toContain("scripted model substitute");
    expect(evidence.catalog).toContain(dataset === "national" ? "production national catalog" : "NOT national coverage");
    expect(evidence.model_requests).toBe(0);
    const step = evidence.steps.find((item) => item.operation === operation)!;
    expect(step).toBeTruthy();
    const recovered = restoreInvestigationWorkspace(step.workspace);
    const preview = recovered.creationBinding!.confirmationPreview!;
    expect(preview).toEqual(step.confirmation_preview);
    expect(recovered.creationBinding?.draft).toEqual(step.workspace.draft_artifact?.draft);
    expect(recovered.draft.confirmed).toBe(false);
    expect(preview.can_confirm).toBe(false);
    expect(preview.relevance_scope_state?.status).toBe(operation);
    // The captured pending requirement remains an unsupported district, not a province.
    if (operation === "pending") expect(preview.relevance_scope_state?.target_text).toBe("朝阳区");

    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto("/");
    const label = dataset === "national"
      ? "程序联通验证：真实工具/API · 临时 SQLite · 全国名录；模型为脚本替身，未验证 AI 理解或地域执行。"
      : "程序联通验证：真实工具/API · 临时 SQLite · 10 条地域 fixture；模型为脚本替身，未验证 AI 理解或全国覆盖。";
    await page.evaluate(async ({ draft, preview, label }) => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
      const { TaskConfirmationCard } = await load("/src/features/investigation/TaskConfirmationCard.tsx");
      const { InvestigationContextDrawer } = await load("/src/features/investigation/InvestigationContextDrawer.tsx");
      const host = document.createElement("main");
      host.style.cssText = "width:820px;padding:28px;background:#f8fafc;min-height:100vh;";
      document.body.replaceChildren(host);
      createRoot(host).render(React.createElement(React.Fragment, null,
        React.createElement("p", { style: { fontSize: "13px", marginBottom: "16px" } },
          label),
        React.createElement(TaskConfirmationCard, { draft, preview, onOpenConfig: () => {}, onStartExecution: () => {} }),
        React.createElement(InvestigationContextDrawer, { isOpen: true, type: "task_config", draft,
          confirmationPreview: preview, onClose: () => {} })
      ));
    }, { draft: recovered.draft, preview, label });
    const card = page.getByRole("region", { name: "最终任务确认卡" });
    const details = page.getByLabel("上下文详情抽屉").getByLabel("调查地域配置");
    await expect(card.getByRole("button", { name: "确认并开始调查" })).toBeDisabled();
    await expect(details).toBeVisible();
    await expect(card.getByText("任务已启动", { exact: false })).toHaveCount(0);
    if (operation === "pending") {
      await expect(card).toContainText("最新地域要求（待澄清）");
      await expect(card).toContainText("朝阳区");
      await expect(details).toContainText(preview.relevance_scope_state!.reason!);
      await expect(card).not.toContainText("克拉玛依");
      await expect(details).not.toContainText("克拉玛依");
    } else {
      await expect(card).toContainText(preview.relevance_scope!.target.name);
      await expect(details).toContainText(preview.relevance_scope!.province.name);
      await expect(card).toContainText("地域执行能力尚未接通");
    }
    const evidenceDir = "../experiments/geographic_scope_draft/evidence";
    const prefix = dataset === "national" ? "scope-service-national" : "scope-service";
    await page.screenshot({ path: `${evidenceDir}/${prefix}-${operation}.png`, fullPage: true });
  });
}
}

for (const scenario of [
  { name: "resolved geography", status: "resolved", extra: "", canPreview: true },
  { name: "pending geography", status: "pending", extra: "", canPreview: false },
  { name: "unsupported geographic mode", status: "resolved", extra: "RELEVANCE_SCOPE_MODE_UNSUPPORTED", canPreview: true },
  { name: "missing rule resource", status: "resolved", extra: "NO_PUBLISHED_RULESET", canPreview: false },
  { name: "missing recall resource", status: "pending", extra: "NO_PUBLISHED_RECALL_LEXICON", canPreview: false },
  { name: "missing crawler account", status: "resolved", extra: "NO_CRAWLER_ACCOUNT", canPreview: false },
  { name: "unknown blocker", status: "resolved", extra: "FUTURE_RESOURCE_BLOCKER", canPreview: false },
  { name: "geographic code without geographic state", status: "absent", extra: "", canPreview: false },
  { name: "historical read-only card", status: "resolved", extra: "", readOnly: true, canPreview: false },
  { name: "missing platform", status: "resolved", extra: "", noPlatform: true, canPreview: false }
] as const) {
  test(`suggestion preview allows only geographic readiness blockers: ${scenario.name}`, async ({ page }) => {
    const preview = previewFor(scenario.status === "pending" ? pending
      : scenario.status === "resolved" ? { status: "resolved", candidates: [] } : undefined, scope);
    if (scenario.extra) preview.blockers.push({ code: scenario.extra, message: "待处理的配置限制",
      resource_type: "", resource_id: "", latest_safe_summary: {}, management_url: "" });
    const cardDraft = { ...draft, platforms: "noPlatform" in scenario && scenario.noPlatform ? [] : draft.platforms };
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/");
    await page.evaluate(async ({ draft, preview, readOnly }) => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
      const { TaskSuggestionCard } = await load("/src/features/investigation/TaskSuggestionCard.tsx");
      const { TaskConfirmationCard } = await load("/src/features/investigation/TaskConfirmationCard.tsx");
      const host = document.createElement("main");
      document.body.replaceChildren(host);
      function PreviewFlow() {
        const [showPreview, setShowPreview] = React.useState(false);
        return showPreview
          ? React.createElement(TaskConfirmationCard, { draft, preview, onOpenConfig: () => {}, onStartExecution: () => {} })
          : React.createElement(TaskSuggestionCard, {
              draft, preview, assistantContent: "配置已保存，尚未启动。", shouldStream: false,
              showSuggestionCard: true, isReadOnly: readOnly, onStreamingComplete: () => {},
              onUpdatePlatforms: () => {}, onOpenAnalysisPlan: () => {}, onGenerateConfig: () => setShowPreview(true)
            });
      }
      createRoot(host).render(React.createElement(PreviewFlow));
    }, { draft: cardDraft, preview, readOnly: "readOnly" in scenario && scenario.readOnly });
    const generate = page.getByRole("button", { name: "生成任务配置", exact: true });
    if (scenario.canPreview) {
      await expect(generate).toBeEnabled();
      await generate.click();
      const confirmation = page.getByRole("region", { name: "最终任务确认卡" });
      await expect(confirmation).toBeVisible();
      await expect(confirmation.getByRole("button", { name: "确认并开始调查" })).toBeDisabled();
      if (scenario.status === "pending") await expect(confirmation).toContainText("朝阳区");
    } else {
      await expect(generate).toBeDisabled();
      await expect(page.getByRole("region", { name: "最终任务确认卡" })).toHaveCount(0);
    }
  });
}


for (const status of ["resolved", "pending"] as const) {
  test(`suggestion card retains ${status} geography when collection service blocks confirmation`, async ({ page }) => {
    const preview = previewFor(status === "pending" ? ambiguousPending : { status: "resolved", candidates: [] }, scope);
    preview.blockers.push({ code: "COLLECTION_SERVICE_UNAVAILABLE", message: "采集服务暂不可用",
      resource_type: "collection_service", resource_id: "", latest_safe_summary: {}, management_url: "" });
    await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/");
    await page.evaluate(async ({ draft, preview }) => {
      const load = (path: string) => import(path);
      const React = (await load("/node_modules/.vite/deps/react.js")).default;
      const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
      const { TaskSuggestionCard } = await load("/src/features/investigation/TaskSuggestionCard.tsx");
      const host = document.createElement("main");
      document.body.replaceChildren(host);
      createRoot(host).render(React.createElement(TaskSuggestionCard, {
        draft, preview, assistantContent: "草案已保存，采集服务暂不可用。", shouldStream: false,
        showSuggestionCard: true, isReadOnly: false, onStreamingComplete: () => {},
        onUpdatePlatforms: () => {}, onOpenAnalysisPlan: () => {}, onGenerateConfig: () => {}
      }));
    }, { draft, preview });
    const card = page.getByRole("region", { name: "任务建议卡片" });
    const details = page.getByLabel("调查地域配置");
    await expect(details).toBeVisible();
    await expect(card.getByRole("button", { name: "生成任务配置", exact: true })).toBeDisabled();
    if (status === "pending") {
      await expect(details).toContainText("最新地域要求（待澄清）");
      await expect(details).toContainText(ambiguousPending.reason!);
      await expect(details).toContainText("吉林省、吉林市（吉林省）");
      await expect(details).not.toContainText("克拉玛依");
      await expect(details).toContainText("地域要求待澄清，当前不能启动");
    } else {
      await expect(details).toContainText("调查地域");
      await expect(details).toContainText("克拉玛依市（新疆维吾尔自治区）");
      await expect(details).toContainText("本地优先，不足时按已确认策略补充");
      await expect(details).toContainText("地域执行能力尚未接通，当前不能启动");
    }
    await expect(details.getByRole("textbox")).toHaveCount(0);
    await expect(details.getByRole("combobox")).toHaveCount(0);
  });
}

// These tests use the production page, POST client, callback and workspace recovery.
// API fixtures isolate UI behavior from model availability; no turn is permitted.
for (const mode of ["choose", "manual", "invalid", "retry", "conflict", "busy", "history", "duplicate", "resolved-edit"] as const) {
  test(`direct geographic clarification: ${mode}`, async ({ page }) => {
    const sessionId = "investigation-session:geo-choice";
    const initial = artifactFor("geo-draft", mode === "resolved-edit"
      ? previewFor({ status: "resolved", candidates: [] }, scope) : previewFor(ambiguousPending));
    let current = structuredClone(initial);
    const bodies: Array<{ target_text: string; expected_revision: number; operation_id: string }> = [];
    const forbidden: string[] = [];
    let failedOnce = false;
    let stateReads = 0;
    const workspaceState = () => ({
      workspace: { workspace_session_id: sessionId, status: "active", created_at: initial.draft.created_at, updated_at: current.draft.updated_at },
      messages: [
        ...(mode === "history" ? [{ message_id: "old-card", turn_id: "old-turn", role: "assistant", content: "旧地域要求", artifact: artifactFor("old-draft", previewFor(ambiguousPending)), sequence: 1, created_at: initial.draft.created_at }] : []),
        { message_id: "current-card", turn_id: "current-turn", role: "assistant", content: "请确认调查地域", artifact: initial, sequence: 2, created_at: initial.draft.created_at }
      ],
      latest_turn: mode === "busy" ? { turn_id: "pending-turn", status: "running", stage: "thinking" } : null,
      draft_artifact: current, run: null, report_messages: [], latest_report_turn: null, activity_events: []
    });
    await page.route("**/api/**", async route => {
      const req = route.request(); const path = decodeURIComponent(new URL(req.url()).pathname);
      if (path === "/api/auth/config") return route.fulfill({ json: { enabled: false } });
      if (path === "/api/me/task-quota") return route.fulfill({ json: { enabled: false } });
      if (req.method() === "POST" && path.endsWith("/relevance-scope")) {
        const body = req.postDataJSON(); bodies.push(body);
        if (mode === "retry" && !failedOnce) { failedOnce = true; return route.abort("failed"); }
        if (mode === "conflict") return route.fulfill({ status: 409, json: { detail: { code: "DRAFT_REVISION_CONFLICT", message: "版本已变化" } } });
        if (body.target_text === "朝阳区") return route.fulfill({ status: 422, json: { detail: {
          code: "RELEVANCE_SCOPE_INVALID", message: "区县暂不支持，请填写完整省市名称。", details: { mutation_applied: false,
            region_resolution: { ...pending, target_text: "朝阳区" } }
        } } });
        if (mode === "duplicate") await new Promise(resolve => setTimeout(resolve, 200));
        current = artifactFor("geo-draft", { ...previewFor({ status: "resolved", candidates: [] }, {
          ...scope, target: { name: "吉林省", aliases: [] }, province: { name: "吉林省", aliases: [] }
        }), draft_revision: 3 });
        current.draft.current_revision = 3;
        current.draft_revision = 3;
        return route.fulfill({ json: current.draft });
      }
      if (req.method() !== "GET") { forbidden.push(path); return route.fulfill({ status: 500, json: {} }); }
      if (path === "/api/investigation-workspaces") return route.fulfill({ json: { items: [{ workspace_session_id: sessionId, title: "吉林调查", updated_at: initial.draft.updated_at, run_status: "", presentation_stage: "confirmation" }], has_more: false } });
      if (path.endsWith("/state")) { stateReads++; return route.fulfill({ json: workspaceState() }); }
      if (path.endsWith("pending-turn")) return route.fulfill({ json: { turn_id: "pending-turn", status: "running", stage: "thinking" } });
      return route.fulfill({ json: { items: [] } });
    });
    if (mode === "manual") await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/investigation/${encodeURIComponent(sessionId)}`);
    const cards = page.getByRole("region", { name: "最终任务确认卡" });
    const card = cards.last();
    await expect(card).toBeVisible();
    if (mode === "choose") await card.screenshot({ path: "/tmp/geographic-direct-clarification.png" });
    if (mode === "history") {
      await expect(cards).toHaveCount(2);
      await expect(cards.first().getByRole("button", { name: "吉林省", exact: true })).toHaveCount(0);
    }
    if (mode === "resolved-edit") {
      const region = card.getByLabel("调查地域配置");
      const beforeConfiguration = structuredClone(current.draft.configuration);
      const beforeRevision = current.draft.current_revision;
      await expect(region).toContainText("克拉玛依市（新疆维吾尔自治区）");
      await card.getByRole("button", { name: "修改调查地域", exact: true }).click();
      await expect(region).toContainText("填写新的省市名称，保存后将直接更新草案。");
      await expect(region).not.toContainText("指哪个调查范围");
      await region.getByRole("textbox", { name: "省级地域或城市完整名称" }).fill("朝阳区");
      await region.getByRole("button", { name: "验证并保存地域", exact: true }).click();
      await expect(region.getByRole("alert")).toContainText("区县暂不支持");
      await expect(region).toContainText("克拉玛依市（新疆维吾尔自治区）");
      expect(current.draft.current_revision).toBe(beforeRevision);
      expect(current.draft.configuration).toEqual(beforeConfiguration);
      await region.getByRole("button", { name: "取消修改", exact: true }).click();
      await expect(region.getByRole("textbox")).toHaveCount(0);
      await expect(region.getByRole("alert")).toHaveCount(0);
      await expect(region).toContainText("克拉玛依市（新疆维吾尔自治区）");
      expect(current.draft.current_revision).toBe(beforeRevision);
      expect(bodies).toHaveLength(1);
      await card.getByRole("button", { name: "修改调查地域", exact: true }).click();
      await region.getByRole("textbox", { name: "省级地域或城市完整名称" }).fill("吉林省");
      await region.getByRole("button", { name: "验证并保存地域", exact: true }).click();
      await expect(region.getByRole("textbox")).toHaveCount(0);
      await expect(region).toContainText("吉林省");
      await expect(region).not.toContainText("克拉玛依");
      expect(current.draft.current_revision).toBe(beforeRevision + 1);
      expect(current.draft.configuration.judgement).toEqual(beforeConfiguration.judgement);
      expect(current.draft.configuration.investigation).toEqual(beforeConfiguration.investigation);
      expect(bodies.map(body => body.target_text)).toEqual(["朝阳区", "吉林省"]);
      expect(bodies.map(body => body.expected_revision)).toEqual([beforeRevision, beforeRevision]);
      expect(bodies[0].operation_id).not.toBe(bodies[1].operation_id);
      await page.reload();
      const restoredRegion = page.getByRole("region", { name: "最终任务确认卡" }).getByLabel("调查地域配置");
      await expect(restoredRegion).toContainText("吉林省");
      await expect(restoredRegion).not.toContainText("克拉玛依");
      await expect(restoredRegion.getByRole("button", { name: "修改调查地域", exact: true })).toBeVisible();
      expect(stateReads).toBeGreaterThan(2);
      expect(forbidden).toEqual([]);
      return;
    }
    const choose = card.getByRole("button", { name: "吉林省", exact: true });
    await expect(choose).toBeVisible();
    if (mode === "busy") {
      await expect(choose).toBeDisabled();
      expect(bodies).toHaveLength(0); return;
    }
    if (mode === "invalid") {
      await card.getByRole("button", { name: "其他地域", exact: true }).click();
      await card.getByRole("textbox", { name: "省级地域或城市完整名称" }).fill("朝阳区");
      await card.getByRole("button", { name: "验证并保存地域" }).click();
      await expect(card.getByRole("alert").filter({ hasText: "区县暂不支持" })).toBeVisible();
      expect(current.draft.current_revision).toBe(2);
      expect(current.draft.configuration.judgement).toEqual(initial.draft.configuration.judgement);
      expect(current.draft.configuration.investigation).toEqual(initial.draft.configuration.investigation);
      expect(forbidden).toEqual([]); return;
    }
    if (mode === "manual") {
      await card.getByRole("button", { name: "其他地域", exact: true }).click();
      await card.getByRole("textbox", { name: "省级地域或城市完整名称" }).fill("吉林省");
      await card.getByRole("button", { name: "验证并保存地域" }).click();
    } else if (mode === "duplicate") {
      await choose.evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
      await expect(card.getByRole("button", { name: "吉林市（吉林省）", exact: true })).toBeDisabled();
    } else await choose.click();
    if (mode === "retry") {
      await expect(card.getByRole("alert").filter({ hasText: "地域暂时无法保存" })).toBeVisible();
      await choose.click();
      await expect.poll(() => bodies.length).toBe(2);
      expect(bodies[0]).toEqual(bodies[1]);
    }
    if (mode === "conflict") {
      await expect(card.getByRole("alert").filter({ hasText: "配置刚刚发生变化" })).toBeVisible();
      expect(current.draft.current_revision).toBe(2);
    } else {
      await expect(card.getByRole("button", { name: "修改调查地域" })).toBeVisible();
      await expect(card.getByLabel("地域澄清")).toHaveCount(0);
      expect(current.draft.configuration.judgement).toEqual(initial.draft.configuration.judgement);
      expect(current.draft.configuration.investigation).toEqual(initial.draft.configuration.investigation);
      await page.reload();
      await expect(page.getByRole("button", { name: "修改调查地域" })).toBeVisible();
    }
    if (mode === "duplicate") expect(bodies).toHaveLength(1);
    expect(stateReads).toBeGreaterThan(1);
    expect(bodies[0].target_text).toBe("吉林省");
    expect(bodies[0].expected_revision).toBe(2);
    expect(bodies[0].operation_id).toBeTruthy();
    expect(Object.keys(bodies[0]).sort()).toEqual(["expected_revision", "operation_id", "target_text"]);
    expect(forbidden).toEqual([]);
  });
}

for (const clarifyModification of [false, true]) {
test(`region selection history survives repeated choices and refresh without replaying actions${clarifyModification ? " via intermediate clarification" : ""}`, async ({ page }) => {
  const sessionId = "investigation-session:geo-history";
  const initial = artifactFor("geo-draft", previewFor(ambiguousPending));
  let current = structuredClone(initial);
  const history: import("../src/types/investigationCreation").RegionSelectionHistoryEntry[] = [];
  const requests: Array<{ target_text: string; expected_revision: number; operation_id: string; clarification_target_text?: string }> = [];
  const forbidden: string[] = [];
  const sourceMessages: import("../src/types/investigationCreation").InvestigationWorkspaceMessage[] = [
    { message_id: "initial-question", turn_id: "initial-turn", role: "user", content: "调查吉林火灾", sequence: 1, created_at: "2026-10-15T00:00:00Z", artifact: null },
    { message_id: "initial-card", turn_id: "initial-turn", role: "assistant", content: "请确认调查地域。", artifact: initial, sequence: 2, created_at: "2026-10-15T00:01:00Z" }
  ];
  await page.route("**/api/**", async route => {
    const req = route.request(); const path = decodeURIComponent(new URL(req.url()).pathname);
    if (path === "/api/auth/config") return route.fulfill({ json: { enabled: false } });
    if (path === "/api/me/task-quota") return route.fulfill({ json: { enabled: false } });
    if (req.method() === "POST" && path.endsWith("/relevance-scope")) {
      const body = req.postDataJSON(); requests.push(body);
      expect(body.expected_revision).toBe(current.draft.current_revision);
      if (body.target_text === "吉林") return route.fulfill({ status: 422, json: { detail: {
        code: "RELEVANCE_SCOPE_INVALID", message: ambiguousPending.reason,
        details: { mutation_applied: false, region_resolution: ambiguousPending }
      } } });
      const targetName = body.target_text;
      const oldScope = current.draft.configuration.relevance_scope;
      const oldState = current.draft.configuration.relevance_scope_state;
      const nextRevision = current.draft.current_revision + 1;
      history.push({
        event_id: `selection-${history.length + 1}`, draft_id: current.draft_id,
        previous_revision: current.draft.current_revision, revision: nextRevision, after_sequence: 2,
        created_at: `2026-10-15T00:0${history.length + 2}:00Z`,
        question: history.length === 0 || body.clarification_target_text ? "“吉林”指哪个调查范围？" : "修改调查地域：原地域为吉林省",
        previous_target: oldScope?.target || null, previous_state: structuredClone(body.clarification_target_text ? ambiguousPending : oldState || null),
        selected_target: { name: targetName, aliases: [] }, selected_province: { name: "吉林省", aliases: [] }
      });
      current = artifactFor("geo-draft", { ...previewFor({ status: "resolved", candidates: [] }, {
        ...scope, target: { name: targetName, aliases: [] }, province: { name: "吉林省", aliases: [] }
      }), draft_revision: nextRevision });
      current.draft.current_revision = nextRevision;
      current.draft_revision = nextRevision;
      if (history.length === 2) sourceMessages.push({ message_id: "later-user", turn_id: "later-turn", role: "user",
        content: "后续问题：继续核对规则", sequence: 3, created_at: "2026-10-15T00:04:00Z", artifact: null });
      return route.fulfill({ json: current.draft });
    }
    if (req.method() !== "GET") { forbidden.push(path); return route.fulfill({ status: 500, json: {} }); }
    if (path === "/api/investigation-workspaces") return route.fulfill({ json: { items: [{ workspace_session_id: sessionId, title: "吉林地域历史", updated_at: current.draft.updated_at, run_status: "", presentation_stage: "confirmation" }], has_more: false } });
    if (path.endsWith("/state")) return route.fulfill({ json: {
      workspace: { workspace_session_id: sessionId, status: "active", created_at: initial.draft.created_at, updated_at: current.draft.updated_at },
      messages: sourceMessages, latest_turn: null, draft_artifact: current, run: null, report_messages: [],
      latest_report_turn: null, activity_events: [], region_selection_history: history
    } });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto(`/investigation/${encodeURIComponent(sessionId)}`);
  const currentCard = page.getByRole("region", { name: "最终任务确认卡" });
  await currentCard.getByRole("button", { name: "吉林省", exact: true }).click();
  const historyCards = page.getByRole("region", { name: "地域选择历史" });
  await expect(historyCards).toHaveCount(1);
  const firstHistory = historyCards.first();
  await expect(firstHistory).toContainText("“吉林”指哪个调查范围？");
  await expect(firstHistory.getByRole("button", { name: "吉林省 · 已选择", exact: true })).toBeDisabled();
  await expect(firstHistory.getByRole("button", { name: "吉林市（吉林省）", exact: true })).toBeDisabled();
  await expect(firstHistory.getByRole("button", { name: "其他地域", exact: true })).toBeDisabled();
  await expect(firstHistory.getByLabel("地域修改结果")).toHaveText("吉林 → 吉林省");
  const firstText = await firstHistory.textContent();
  const firstEvent = structuredClone(history[0]);
  await currentCard.getByRole("button", { name: "修改调查地域", exact: true }).click();
  await currentCard.getByRole("textbox", { name: "省级地域或城市完整名称" }).fill(clarifyModification ? "吉林" : "吉林市");
  await currentCard.getByRole("button", { name: "验证并保存地域", exact: true }).click();
  if (clarifyModification) {
    await expect(currentCard.getByLabel("地域澄清").getByRole("alert")).toHaveText(ambiguousPending.reason!);
    await expect(currentCard.getByText(ambiguousPending.reason!, { exact: true })).toHaveCount(1);
    expect(current.draft.current_revision).toBe(3);
    expect(current.draft.configuration.relevance_scope?.target.name).toBe("吉林省");
    expect(requests[1].target_text).toBe("吉林");
    expect(requests[1]).not.toHaveProperty("clarification_target_text");
    await currentCard.getByRole("button", { name: "吉林市（吉林省）", exact: true }).click();
    expect(requests[2].target_text).toBe("吉林市");
    expect(requests[2].clarification_target_text).toBe("吉林");
  }
  await expect(historyCards).toHaveCount(2);
  await expect(firstHistory).toHaveText(firstText!);
  expect(history[0]).toEqual(firstEvent);
  await expect(historyCards.last().getByLabel("地域修改结果")).toHaveText("吉林省 → 吉林市（吉林省）");
  await expect(historyCards.last().getByRole("button", { name: clarifyModification ? "吉林市（吉林省） · 已选择" : "其他地域：吉林市（吉林省） · 已选择", exact: true })).toBeDisabled();
  if (clarifyModification) {
    await expect(historyCards.last()).toContainText("“吉林”指哪个调查范围？");
    await expect(historyCards.last().getByRole("button", { name: "吉林省", exact: true })).toBeDisabled();
    await expect(historyCards.last().getByRole("button", { name: "其他地域", exact: true })).toBeDisabled();
    expect(history[1].previous_state?.target_text).toBe("吉林");
    expect(history[1].previous_state?.candidates).toEqual(ambiguousPending.candidates);
  }
  const sortedTimeline = await page.locator('[aria-label="最终任务确认卡"], [aria-label="地域选择历史"], .inv-msg-user-bubble').allTextContents();
  expect(sortedTimeline[0]).toBe("调查吉林火灾");
  expect(sortedTimeline[1]).toContain("配置确认");
  expect(sortedTimeline[2]).toContain("“吉林”指哪个调查范围？");
  expect(sortedTimeline[3]).toContain(clarifyModification ? "“吉林”指哪个调查范围？" : "修改调查地域：原地域为吉林省");
  expect(sortedTimeline[4]).toBe("后续问题：继续核对规则");
  await firstHistory.getByRole("button").evaluateAll(buttons => buttons.forEach(button => (button as HTMLButtonElement).click()));
  expect(requests).toHaveLength(clarifyModification ? 3 : 2);
  expect(forbidden).toEqual([]);
  await page.reload();
  await expect(historyCards).toHaveCount(2);
  await expect(firstHistory).toHaveText(firstText!);
  await expect(currentCard.getByLabel("调查地域配置")).toContainText("吉林市（吉林省）");
  await expect(currentCard.getByRole("button", { name: "修改调查地域", exact: true })).toBeVisible();
  await expect(historyCards.getByRole("button").filter({ hasText: "已选择" })).toHaveCount(2);
  for (const button of await historyCards.getByRole("button").all()) await expect(button).toBeDisabled();
  expect(current.draft.configuration.judgement).toEqual(initial.draft.configuration.judgement);
  expect(current.draft.configuration.investigation).toEqual(initial.draft.configuration.investigation);
  expect(requests).toHaveLength(clarifyModification ? 3 : 2);
  expect(forbidden).toEqual([]);
  if (clarifyModification) {
    await expect(historyCards.last()).toContainText("“吉林”指哪个调查范围？");
    await expect(historyCards.last().getByRole("button", { name: "吉林省", exact: true })).toBeDisabled();
    await expect(historyCards.last().getByRole("button", { name: "吉林市（吉林省） · 已选择", exact: true })).toBeDisabled();
  }
  await page.screenshot({ path: "/tmp/geographic-region-selection-history.png", fullPage: true });
});
}

test("region selection recovery uses sequence anchors, retains snapshots and accepts legacy states", () => {
  const artifact = artifactFor("geo-draft", previewFor(ambiguousPending));
  const state: InvestigationWorkspaceState = {
    workspace: { workspace_session_id: "sequence-history", status: "active", created_at: "2026-10-15T00:00:00Z", updated_at: "2026-10-15T00:05:00Z" },
    messages: [
      { message_id: "answer", role: "assistant", turn_id: "turn-1", sequence: 1, content: "原提问", artifact, created_at: "2026-10-15T00:01:00Z" },
      { message_id: "next-user", role: "user", turn_id: "turn-2", sequence: 2, content: "之后的用户问题", artifact: null, created_at: "2026-10-15T00:02:00Z" }
    ], latest_turn: null, draft_artifact: artifact, run: null, report_messages: [], latest_report_turn: null, activity_events: [], resource_continuity_enabled: true
  };
  const legacy = restoreInvestigationWorkspace(state);
  expect(legacy.messages.some(message => message.type === "region_selection")).toBe(false);
  const first = { event_id: "first", draft_id: "geo-draft", previous_revision: 2, revision: 3, after_sequence: 1,
    // Intentionally disagree with message timestamps: sequence is authoritative.
    created_at: "2026-10-15T00:03:00Z", question: "吉林指省还是市？", previous_target: null,
    previous_state: ambiguousPending, selected_target: { name: "吉林省" }, selected_province: { name: "吉林省" } };
  const second = { ...first, event_id: "second", previous_revision: 3, revision: 4, created_at: "2026-10-15T00:04:00Z",
    question: "更改吉林省", previous_target: { name: "吉林省" }, previous_state: { status: "resolved" as const, candidates: [] }, selected_target: { name: "吉林市" } };
  const recovered = restoreInvestigationWorkspace({ ...state, region_selection_history: [second, first] });
  expect(recovered.messages.map(message => message.id)).toEqual(["answer", "region-selection:first", "region-selection:second", "next-user"]);
  const actions = recovered.messages.filter(message => message.type === "region_selection");
  expect(actions.map(message => message.sender)).toEqual(["system", "system"]);
  expect(actions[0].regionSelection?.previous_state?.candidates).toEqual(ambiguousPending.candidates);
  expect(actions[0].regionSelection?.question).toBe(first.question);
  expect(actions[1].regionSelection?.previous_target?.name).toBe("吉林省");
  expect(actions.every(message => message.content === undefined)).toBe(true);
});
