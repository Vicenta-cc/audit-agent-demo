import { expect, test, type Page } from "@playwright/test";

async function renderCard(page: Page, labels: string[]) {
  await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: { detail: "local fixture" } }));
  await page.goto("/");
  await page.evaluate(async (items) => {
    const load = (path: string) => import(path);
    const { mountReportCard } = await load("/tests/reportCardMount.tsx");
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    mountReportCard(host, items.map((label) => ({ label, value: "1条", detail: "" })));
  }, labels);
  await expect(page.locator(".inv-metric-block")).toHaveCount(labels.length);
}

function metricValue(page: Page, label: string) {
  return page.locator(".inv-metric-block", { has: page.locator(".inv-metric-lbl", { hasText: new RegExp(`^${label}$`) }) })
    .locator(".inv-metric-val");
}

test("decision metrics are colored by audit decision", async ({ page }) => {
  await renderCard(page, ["分析内容", "建议拦截", "进入复审", "审核通过"]);

  await expect(metricValue(page, "分析内容")).toHaveAttribute("class", "inv-metric-val");
  await expect(metricValue(page, "建议拦截")).toHaveAttribute("class", "inv-metric-val text-danger");
  await expect(metricValue(page, "进入复审")).toHaveAttribute("class", "inv-metric-val text-warning");
  await expect(metricValue(page, "审核通过")).toHaveAttribute("class", "inv-metric-val text-success");
});

test("reports published with the older risk labels keep their colors", async ({ page }) => {
  await renderCard(page, ["分析内容", "进入复审", "中高风险", "未发现明显风险"]);

  await expect(metricValue(page, "进入复审")).toHaveAttribute("class", "inv-metric-val text-warning");
  await expect(metricValue(page, "中高风险")).toHaveAttribute("class", "inv-metric-val text-danger");
  await expect(metricValue(page, "未发现明显风险")).toHaveAttribute("class", "inv-metric-val text-success");
});
