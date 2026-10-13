import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantMarkdown } from "../src/features/investigation/AssistantMarkdown";
import { HistoricalReportPendingStatus } from "../src/features/investigation/HistoricalReportPendingStatus";
import {
  historicalPendingStatus,
  shouldShowHistoricalPending
} from "../src/features/investigation/historicalReportPresentation";

function renderMarkdown(content: string) {
  return renderToStaticMarkup(createElement(AssistantMarkdown, { content }));
}

test("GFM tables render semantic table sections instead of pipe text", () => {
  const html = renderMarkdown([
    "| 风险等级 | 数量 | 结论 |",
    "| --- | ---: | --- |",
    "| medium | 13 | review |",
    "| low | 7 | pass |"
  ].join("\n"));

  expect(html).toContain("<table>");
  expect(html).toContain("<thead>");
  expect(html).toContain("<tbody>");
  expect(html).toContain("中风险");
  expect(html).toContain("低风险");
  expect(html).toContain("需复核");
  expect(html).toContain("通过");
  expect(html).not.toContain("| ---");
});

test("headings, lists, quotes and code render safely without executing HTML", () => {
  const html = renderMarkdown([
    "# 调查结论",
    "",
    "- **重点**：需要复核",
    "1. 第一项",
    "",
    "> 原始证据中的 medium 保持原文",
    "",
    "行内代码 `risk_level=medium` 不转换。",
    "",
    "```json",
    "{\"risk_level\":\"high\"}",
    "```",
    "",
    "<script>globalThis.compromised = true</script>",
    "<img src=x onerror=\"globalThis.compromised=true\">"
  ].join("\n"));

  expect(html).toContain("<h1>调查结论</h1>");
  expect(html).toContain("<ul>");
  expect(html).toContain("<ol>");
  expect(html).toContain("<blockquote>");
  expect(html).toContain("<code>risk_level=medium</code>");
  expect(html).toContain("{&quot;risk_level&quot;:&quot;high&quot;}");
  expect(html).not.toContain("<script");
  expect(html).not.toContain("<img");
  expect(html).not.toContain("onerror");
});

test("display terminology is localized without mutating the underlying answer", () => {
  const answer = [
    "风险等级：high；审核状态：review。",
    "- 审核决定：**pass 188 篇、review 13 篇**",
    "- 风险等级分布：**none 188 / low 6 / medium 5 / high 2**",
    "- 5 篇帖子中，4 篇为 medium 风险，1 篇为 high 风险",
    "根据当前已加载的审核资料，帖子被审核为 medium（中风险）。",
    "决定：review",
    "",
    "| 类型 | 结果 |",
    "| --- | --- |",
    "| direct Evidence | none |",
    "| InvestigationFinding | pass |",
    "",
    "代码 `medium` 与链接 [原地址](https://example.test/medium) 保持原样。"
  ].join("\n");
  const original = answer;
  const html = renderMarkdown(answer);

  expect(html).toContain("风险等级：高风险");
  expect(html).toContain("审核状态：需复核");
  expect(html).toContain("通过 188 篇、需复核 13 篇");
  expect(html).toContain("无风险 188 / 低风险 6 / 中风险 5 / 高风险 2");
  expect(html).toContain("4 篇为 中风险，1 篇为 高风险");
  expect(html).toContain("帖子被审核为 中风险。");
  expect(html).toContain("决定：需复核");
  expect(html).toContain("直接证据");
  expect(html).toContain("调查发现");
  expect(html).toContain("无风险");
  expect(html).toContain("通过");
  expect(html).toContain("<code>medium</code>");
  expect(html).toContain('href="https://example.test/medium"');
  expect(answer).toBe(original);
});

test("pending stages have stable Chinese live-region copy", () => {
  expect(historicalPendingStatus("accepted")).toBe("已接收，等待开始回答……");
  expect(historicalPendingStatus("planning")).toBe("正在理解你的问题……");
  expect(historicalPendingStatus("preparing_sources")).toBe("正在准备所需资料……");
  expect(historicalPendingStatus("acquiring_source")).toBe("正在查询报告资料……");
  expect(historicalPendingStatus("answering")).toBe("正在整理回答……");
  expect(historicalPendingStatus("answering", true)).toBe("正在恢复上次回答……");

  const html = renderToStaticMarkup(
    createElement(HistoricalReportPendingStatus, { stage: "acquiring_source" })
  );
  expect(html).toContain('role="status"');
  expect(html).toContain('aria-live="polite"');
  expect(html).toContain("正在查询报告资料……");
});

test("completed and error states do not retain the pending region", () => {
  expect(shouldShowHistoricalPending(true, true, false)).toBe(true);
  expect(shouldShowHistoricalPending(true, false, true)).toBe(true);
  expect(shouldShowHistoricalPending(true, false, false)).toBe(false);
  expect(shouldShowHistoricalPending(false, true, true)).toBe(false);
});

test("manual images have bounded preview controls and report links retain their targets", () => {
  const src = `/api/user-manual/images/${"a".repeat(64)}`;
  const report = `/investigation/task%3A1/report?report=report-version%3A${"b".repeat(32)}`;
  const html = renderMarkdown(`![配置确认 · 示意·演示数据](${src})\n\n[查看报告](${report})`);
  expect(html).toContain('class="inv-manual-image"');
  expect(html).toContain('aria-label="放大：配置确认 · 示意·演示数据"');
  expect(html).toContain(`src="${src}"`);
  expect(html).toContain(`href="${report}"`);
  expect(html).toContain('loading="lazy"');
  const malformed = renderMarkdown('![配图](/api/user-manual/images/not-a-valid-image)');
  expect(malformed).not.toContain('<img');
  expect(malformed).not.toContain('<button');
});


test("bold account bare URL before Chinese explanation preserves target and emphasis", () => {
  const url = "https://www.douyin.com/user/qa-xiaoming-post";
  for (const suffix of ["（小明）在三份报告中的评论数量分布如下：", "，分布如下", "。", ""]) {
    const html = renderMarkdown(`账号 **${url}**${suffix}`);
    expect(html).toContain(`href="${url}"`);
    expect(html).toContain(`<strong><a href="${url}"`);
    expect(html).toContain(`>${url}</a></strong>${suffix}`);
    expect(html).not.toContain(`${url}**`);
  }
});

test("bold URL keeps Chinese query data while separating following explanation", () => {
  const url = "https://example.test/?q=上海（旅游）&page=2";
  const html = renderMarkdown(`账号 **${url}**（说明）`);
  expect(html).toContain(`<strong><a href="${encodeURI(url).replaceAll("&", "&amp;")}"`);
  expect(html).toContain(`>${url.replaceAll("&", "&amp;")}</a></strong>（说明）`);
});

test("bare URL query and fragment retain legal Chinese punctuation and parameters", () => {
  for (const url of [
    "https://example.test/?q=上海（旅游）&page=2",
    "https://example.test/?q=上海，旅游&next=2#（详情）",
    "https://example.test/#上海（旅游）",
    "https://example.test/?q=%E4%B8%8A%E6%B5%B7%EF%BC%88%E6%97%85%E6%B8%B8%EF%BC%89&page=2"
  ]) {
    const html = renderMarkdown(url);
    expect(html).toContain(`href="${encodeURI(url).replaceAll("%25", "%").replaceAll("&", "&amp;")}"`);
  }
});

test("explicit links, literal stars, and normal Chinese prose retain their boundaries", () => {
  const url = "https://example.test/path**?q=上海（旅游）&page=2";
  const html = renderMarkdown(`[原帖](${url})`);
  expect(html).toContain(`href="${encodeURI(url).replaceAll("&", "&amp;")}"`);
  expect(html).toContain(">原帖</a>");
  expect(renderMarkdown("https://example.test/path**，说明")).toContain('href="https://example.test/path**"');
  expect(renderMarkdown("https://example.test/path，后续正文")).toContain('>https://example.test/path</a>，后续正文');
  expect(renderMarkdown("`**https://example.test/path**` ")).toContain('<code>**https://example.test/path**</code>');
});
