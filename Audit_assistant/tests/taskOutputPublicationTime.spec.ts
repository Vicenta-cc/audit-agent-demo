import { expect, test } from "@playwright/test";

const auditTimes = {
  analyzed_at: "2026-10-16T18:00:00",
  updated_at: "2026-10-16T18:01:00",
  created_at: "2026-10-16T17:59:00"
};

const cases: Array<{ name: string; fields: Record<string, unknown>; expected: string }> = [
  ...Object.keys(auditTimes).map(field => ({
    name: `does not use ${field} as publication time`,
    fields: { [field]: auditTimes[field as keyof typeof auditTimes] },
    expected: "--"
  })),
  { name: "missing source time stays unknown despite all audit timestamps", fields: auditTimes, expected: "--" },
  ...["publish_time", "published_at", "created_time", "time"].map(field => ({
    name: `uses source ${field} instead of the later audit time`,
    fields: { ...auditTimes, [field]: "2026-05-30T12:34:00" },
    expected: "05-30 12:34"
  })),
  {
    name: "report snapshot without publication time stays unknown",
    fields: { ...auditTimes, report_snapshot_source: true, publish_time: "2026-05-30T12:34:00" },
    expected: "--"
  },
  {
    name: "report snapshot preserves its own publication time",
    fields: { ...auditTimes, report_snapshot_source: true, published_at: "2026-05-30T12:34:00" },
    expected: "05-30 12:34"
  }
];

for (const scenario of cases) {
  test(scenario.name, async ({ page }) => {
    await page.route("**/api/**", async route => {
      if (route.request().url().includes("/api/audit-results/123")) {
        await route.fulfill({ json: {
          audit_result: {
            audit_result_id: 123, job_id: "time-preview", risk_level: "high",
            title: "原帖发布时间回归", summary: "本地展示测试", comments: [],
            ...scenario.fields
          },
          evidence_groups: [], is_historical_config: false
        } });
      } else {
        await route.fulfill({ status: 404, json: { detail: "local fixture" } });
      }
    });
    await page.goto("/");
    await page.evaluate(async () => {
      const load = (path: string) => import(path);
      const { mountEvidenceTime } = await load("/tests/evidenceTimeMount.tsx");
      const host = document.createElement("div");
      document.body.replaceChildren(host);
      mountEvidenceTime(host);
    });
    const publication = page.locator(".detail-compact-meta > span").filter({ hasText: /^发布时间：/ });
    await expect(publication).toHaveText(`发布时间：${scenario.expected}`);
  });
}
