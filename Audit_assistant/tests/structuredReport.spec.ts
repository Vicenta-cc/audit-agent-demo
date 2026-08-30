import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { resolvePublishedReportVersion } from "../src/features/investigation/reportRoute";
import {
  fetchReportAccountDetail,
  fetchReportAccounts,
  fetchReportAppendix,
  fetchReportFindingEvidence,
  fetchReportPostDetail,
  fetchReportPresentation
} from "../src/services/reports";

const version = "report-version:8c355a5ba03f45619795813af83ac669";

test.beforeEach(() => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      XHS_AUDIT_API_BASE: "http://api.test",
      location: { origin: "http://ui.test" }
    }
  });
});

test("an explicit stable ReportVersion resolves without creating report state", async () => {
  let fetched = false;
  globalThis.fetch = async () => {
    fetched = true;
    throw new Error("unexpected request");
  };

  await expect(resolvePublishedReportVersion("investigation-1", version)).resolves.toBe(version);
  await expect(resolvePublishedReportVersion("investigation-1", "legacy-version-1"))
    .rejects.toThrow("报告引用无效");
  expect(fetched).toBe(false);
});

test("presentation details and appendix use only encoded stable refs", async () => {
  const requestUrls: string[] = [];
  globalThis.fetch = async (input) => {
    requestUrls.push(String(input));
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  await fetchReportPresentation(version);
  await fetchReportAccounts(version, {
    filter: "cross_investigation_commenter",
    search: "账号 一",
    sort: "comment_count",
    limit: 20,
    cursor: "account-entry:20"
  });
  await fetchReportAccountDetail(version, "account-entry:目标/一");
  await fetchReportPostDetail(version, "post:代表/一");
  await fetchReportFindingEvidence(version, "investigation-finding:一");
  await fetchReportAppendix(version, {
    view: "posts",
    finding_ref: "investigation-finding:一",
    limit: 50,
    cursor: "post:50"
  });

  const root = `http://api.test/api/report-versions/${encodeURIComponent(version)}`;
  expect(requestUrls).toEqual([
    `${root}/presentation-projection`,
    `${root}/accounts?filter=cross_investigation_commenter&search=%E8%B4%A6%E5%8F%B7+%E4%B8%80&sort=comment_count&limit=20&cursor=account-entry%3A20`,
    `${root}/accounts/${encodeURIComponent("account-entry:目标/一")}`,
    `${root}/posts/${encodeURIComponent("post:代表/一")}`,
    `${root}/findings/${encodeURIComponent("investigation-finding:一")}/evidence`,
    `${root}/appendix?view=posts&finding_ref=investigation-finding%3A%E4%B8%80&limit=50&cursor=post%3A50`
  ]);
});

test("formal report frontend keeps the revised public contract", () => {
  const reportPage = readFileSync(
    new URL("../src/features/investigation/InvestigationReportPage.tsx", import.meta.url),
    "utf8"
  );
  const appendixPage = readFileSync(
    new URL("../src/features/investigation/InvestigationEvidenceAppendixPage.tsx", import.meta.url),
    "utf8"
  );
  const reportTypes = readFileSync(
    new URL("../src/types/reports.ts", import.meta.url),
    "utf8"
  );
  const publicFrontend = `${reportPage}\n${appendixPage}\n${reportTypes}`;

  expect(publicFrontend).not.toContain("account_corpus_revision");
  expect(reportPage).not.toMatch(/\bclaims?\b/i);
  expect(reportPage).toContain("report.ordered_sections.map");
  expect(reportPage).toContain("已完成独立审核评论");
  expect(reportPage).toContain("评论自身风险");
  expect(reportPage).toContain("report.investigation_summary.paragraphs");
  expect(reportPage).toContain("section.presentation_paragraphs || section.paragraphs");
  expect(reportPage).not.toContain("statistics.risk_distribution.risk_bars.map");
  expect(reportPage).not.toContain("ethnic-report-risk-chart r31-risk-distribution");
  expect(reportPage).toContain("statistics.risk_level.high");
  expect(reportPage).not.toContain("canonical FrozenSnapshot");
  expect(reportPage).not.toContain("当前发布报告未提供结构化资料范围");
  expect(reportPage).not.toContain("原始评论");
  expect(reportPage).toContain("accounts.cross_investigation_commenters");
  expect(reportPage).toContain("accounts.risk_commenters");
  expect(reportPage).toContain('onOpenAccountIndex("cross_investigation_commenter")');
  expect(reportPage).toContain('onOpenAccountIndex("risk_commenter")');
  expect(reportPage).not.toContain("active_comment_entries");
  expect(reportPage).toContain("basis_label");
  expect(reportPage).toContain("评论账号索引");
  expect(reportPage).toContain("返回评论账号索引");
  expect(reportPage).toContain("搜索账号显示名");
  expect(reportPage).toContain("相关调查分布");
  expect(reportPage).toContain("活跃情况");
  expect(reportPage).toContain("next_cursor");
  expect(reportPage).toContain("total_count");
  expect(reportPage).not.toContain("按调查任务分布");
  expect(reportPage).not.toContain("TOP 5");
  expect(reportPage).toContain("<small>风险帖子</small>");
  expect(reportPage).not.toContain("风险或复审帖子");
  expect(reportPage).not.toContain("statistics.decision.review");
  expect(reportPage).not.toContain("需人工核验");
  expect(reportPage).not.toContain("复审");
  expect(reportPage).not.toContain("history_activity");
  expect(reportPage).not.toContain("当前发布报告不包含历史活动");
  expect(reportPage).not.toContain("roleLabel");
  expect(reportPage).not.toContain("评论者");
  expect(reportPage).not.toContain("发布者");
  expect(reportPage).not.toContain(".reduce(");
  expect(reportPage).not.toContain("new Set");
  expect(reportPage).not.toContain("nickname");
  expect(publicFrontend).not.toContain("risk_post_count");
  expect(publicFrontend).not.toContain("report_session_id");
  expect(publicFrontend).not.toContain("internal_account");
  expect(appendixPage).not.toContain("reportSources");
  expect(appendixPage).not.toContain("fetchAuditResults");
  expect(appendixPage).toContain("finding_ref: findingRef");
});
