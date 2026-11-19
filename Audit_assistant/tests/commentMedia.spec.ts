import { expect, test } from "@playwright/test";

test("both comment lists keep image and translation content, hide empty rows, and separate collected and visible totals", async ({ page, context }) => {
  const image = "https://comment-assets.example/photo?crop=0,0";
  const missing = "https://comment-assets.example/missing";
  await context.route("https://comment-assets.example/**", route => route.request().url() === missing
    ? route.fulfill({ status: 404, body: "" })
    : route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p1sAAAAASUVORK5CYII=", "base64") }));
  const comments = [
    { nickname: "纯图", pictures: image, audit_source: "local_empty", audit_status: "completed", risk_score: 0, risk_level: "none" },
    { nickname: "图文", content: "带图的文字", pictures: [image], audit_status: "completed", risk_score: 35, risk_level: "low" },
    { nickname: "图片失效", pictures: missing, audit_source: "local_empty", audit_status: "completed", risk_score: 0, risk_level: "none" },
    { nickname: "只有译文", translation_zh: "留存的译文", audit_status: "failed" },
    { nickname: "多图", pictures: image + "," + missing, audit_source: "local_empty", audit_status: "completed" },
    { nickname: "隐藏空评论", content: "  ", translation_zh: "\n", pictures: "", audit_status: "completed" },
    { nickname: "隐藏无效链接", pictures: "javascript:void(0)", audit_status: "completed" },
    ...Array.from({ length: 17 }, (_, i) => ({ nickname: "一般评论" + i, content: "正文" + i, audit_status: "failed" }))
  ].map((row, index) => ({ ...row, comment_id: String(index + 1) }));
  await page.route("**/api/**", route => route.request().url().includes("/api/audit-results/123")
    ? route.fulfill({ json: { audit_result: { audit_result_id: 123, job_id: "time-preview", title: "评论展示", comments_count: comments.length, comments }, evidence_groups: [] } })
    : route.fulfill({ status: 404, json: {} }));
  await page.goto("/");
  await page.evaluate(async () => {
    const { mountEvidenceTime } = await import("/tests/evidenceTimeMount.tsx");
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    mountEvidenceTime(host);
  });

  const list = page.getByRole("region", { name: "逐条评论审核" });
  await expect(list).toContainText("已采集 24 条 · 展示 22 条");
  await expect(list.getByRole("article")).toHaveCount(20);
  const picture = list.getByRole("article").filter({ hasText: "纯图" });
  await expect(picture).toContainText("图片未审核");
  await expect(picture).not.toContainText("0分");
  const link = picture.getByRole("link", { name: "查看评论图片 1" });
  await expect(link).toHaveAttribute("href", image);
  await link.scrollIntoViewIfNeeded();
  await expect.poll(() => link.locator("img").evaluate(el => el.naturalWidth)).toBeGreaterThan(0);
  const tab = context.waitForEvent("page");
  await link.click();
  const opened = await tab;
  await opened.waitForURL(image);
  await opened.close();
  const lost = list.getByRole("article").filter({ hasText: "图片失效" });
  await lost.scrollIntoViewIfNeeded();
  await expect(lost).toContainText("图片暂不可用");

  await list.getByRole("button", { name: "查看全部 22 条" }).click();
  const drawer = page.getByRole("dialog", { name: "全部评论" });
  await expect(drawer.getByRole("article")).toHaveCount(22);
  await expect(drawer).not.toContainText("隐藏空评论");
  await expect(drawer).not.toContainText("隐藏无效链接");
  await expect(drawer).not.toContainText("该评论未保存文字内容");
  await expect(drawer).toContainText("留存的译文");
  const mixed = drawer.getByRole("article").filter({ hasText: "带图的文字" });
  await expect(mixed.getByRole("link", { name: "查看评论图片 1" })).toHaveAttribute("href", image);
  await expect(drawer.getByRole("article").filter({ hasText: "纯图" })).toContainText("图片未审核");
  await drawer.getByLabel("按评论风险筛选").selectOption("safe");
  await expect(drawer.getByRole("article")).toHaveCount(0);
});
