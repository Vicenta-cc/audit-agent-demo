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
    const details = page.getByLabel("调查地域配置");
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
  { name: "pending geography", status: "pending", extra: "", canPreview: true },
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
    const details = card.getByLabel("调查地域配置");
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
