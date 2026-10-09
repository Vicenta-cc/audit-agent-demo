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

test("comment items separate author, audit label, verbatim text and source time", () => {
  const content = [
    "风险评论 · 共 66 条 · 本次 9–10 条", "",
    "9. **读者甲** · `中风险`", "",
    "   > 原文中的 term、high 和 revision 都不能改写。", "   > &lt;script&gt;alert(1)&lt;/script&gt;", "",
    "   *发布于 2026-10-09 11:00:00（北京时间）*", "",
    "10. **读者乙** · `待审核`", "",
    "    > 第二条原文。", "", "发送“继续”查看下一批。"
  ].join("\n");
  const html = renderToStaticMarkup(createElement(AssistantMarkdown, { content: presentCreationAssistantContent(content) }));
  expect(html).toContain('<ol start="9">');
  expect(html.match(/<li>/g)).toHaveLength(2);
  expect(html.match(/<blockquote>/g)).toHaveLength(2);
  expect(html).toContain('<strong>读者甲</strong>');
  expect(html).toContain('<code>中风险</code>');
  expect(html).toContain('原文中的 term、high 和 revision 都不能改写。');
  expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
  expect(html).not.toContain('<script>');
  expect(html).toContain('发布于 2026-10-09 11:00:00（北京时间）');
});

test("published rule recommendation with a domain label remains visible", () => {
  const raw = [
    "**可用规则（已发布版本）**", "",
    "- **《团结内容审核规则-本地验收》**（social_harmony，已发布 v2，7 条启用规则）——覆盖民族仇恨与群体歧视。",
    "", "**现存黑话库（召回资源）**", "- 目前没有足够匹配的搜索词。",
    "", "draft_id: investigation-draft:private", "RESOURCE_STALE"
  ].join("\n");
  const html = renderToStaticMarkup(createElement(AssistantMarkdown, { content: presentCreationAssistantContent(raw) }));
  expect(html).toContain("<strong>《团结内容审核规则-本地验收》</strong>");
  expect(html).toContain("已发布 v2，7 条启用规则");
  expect(html).toContain("覆盖民族仇恨与群体歧视");
  expect(html).not.toMatch(/draft_id|investigation-draft:private|RESOURCE_STALE/);
});
