import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantMarkdown } from "../src/features/investigation/AssistantMarkdown";
import { presentCreationAssistantContent } from "../src/features/investigation/workspaceRecovery";

test("the reported save receipt keeps both resources and versions in a semantic table", () => {
  const raw = [
    "两份资源已成功保存回原后台，并重新读取正式库核对。", "", "## 保存回执", "",
    "| 资源 | 操作 ID | 新版本 | 修订 ID |",
    "|------|---------|--------|---------|",
    "| 审核规则「维汉婚恋真实测评规则0911」 | save-ruleset-0911-v2-20260911 | v2 | ruleset-revision:ruleset.example:v2 |",
    "| 词库「维汉婚恋临时词库0911」 | save-lexicon-0911-v2-20260911 | v2 | — |",
    "", "两份资源均已更新，未创建或启动任何任务。"
  ].join("\n");
  const html = renderToStaticMarkup(createElement(AssistantMarkdown, { content: presentCreationAssistantContent(raw) }));
  expect(html).toContain("<th>资源</th>");
  expect(html).toContain("<th>新版本</th>");
  expect(html).toContain("审核规则「维汉婚恋真实测评规则0911」");
  expect(html).toContain("词库「维汉婚恋临时词库0911」");
  expect(html.match(/<td>v2<\/td>/g)).toHaveLength(2);
  expect(html).not.toMatch(/save-lexicon|ruleset-revision|操作 ID|修订 ID|\|------/);
  expect(html).toContain("未创建或启动任何任务");
});

test("resource explanations retain conflict choices and remove implementation labels", () => {
  const raw = [
    "## 词库修改受阻", "",
    "- 主词（main，启用）：**维汉通婚**（id=main_weihan_tonghun）",
    "- 变体（variant，启用）：**维汉婚恋**（id=var_weihan_hunlian，归属主词 main_weihan_tonghun）",
    "- 标签（tag，启用）：**婚恋讨论**（id=tag_hunlian_taolun）", "",
    "**冲突说明**：同一词库内 term + platform + match_type 组合唯一。主词 term 改名后会与变体同名。", "",
    "1. **保留变体但改名**", "2. **删除原变体**", "",
    "实际搜索词（recall_plan.search_terms）：维汉通婚。",
    "> 引用原文：term 不代表侮辱。"
  ].join("\n");
  const shown = presentCreationAssistantContent(raw);
  expect(shown).not.toMatch(/id=|main_weihan|var_weihan|match_type|recall_plan/);
  expect(shown).toContain("变体（启用）：**维汉婚恋**（归属主词）");
  expect(shown).toContain("词条、适用平台和匹配方式");
  expect(shown).toContain("2. **删除原变体**");
  expect(shown).toContain("> 引用原文：term 不代表侮辱。");
});

test("ordinary search term code blocks and escaped pipes survive metadata projection", () => {
  const shown = presentCreationAssistantContent("已读取词库，主词如下。\n\n```text\n维汉婚恋\n```\n\n| 词条 | ID |\n|---|---|\n| 甲\\|乙 | main-1 |\n\n编辑 ID：lexicon-edit:private");
  const html = renderToStaticMarkup(createElement(AssistantMarkdown, { content: shown }));
  expect(html).toContain("维汉婚恋");
  expect(html).toContain("甲|乙");
  expect(html).not.toContain("main-1");
  expect(html).not.toContain("lexicon-edit");
});

test("tables without outer pipes retain both rows and numeric alignment", () => {
  const content = presentCreationAssistantContent("资源 | 操作 ID | 版本\n--- | --- | ---:\n规则 | save-rule | v2\n词库 | save-lexicon | v3");
  const html = renderToStaticMarkup(createElement(AssistantMarkdown, { content }));
  expect(html).toContain("<table>");
  expect(html).toContain('style="text-align:right">v2');
  expect(html).toContain('style="text-align:right">v3');
  expect(html).not.toContain("save-");
});
