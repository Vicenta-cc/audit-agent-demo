import { expect, test } from "@playwright/test";
import { evidenceSeconds, formatEvidenceTime, formatEvidenceRange, formatFramePosition } from "../src/features/task-outputs/evidenceTime";

test("frame positions preserve zero, numeric strings and hours", () => {
  expect(formatFramePosition({ timestamp: 0, start: 20, frame_number: 0 })).toBe("00:00 · 帧 0");
  expect(formatFramePosition({ timestamp: "62.5", frame_number: 1875 })).toBe("01:02 · 帧 1875");
  expect(formatEvidenceTime(3661.8)).toBe("01:01:01");
  expect(formatEvidenceRange(2, 2)).toBe("00:02");
});

test("missing frame time falls back to evidence or OCR context time", () => {
  expect(formatFramePosition({ timestamp: "", start: 2, end: 6, frame_number: 60 })).toBe("00:02 - 00:06 · 帧 60");
  expect(formatFramePosition({ ocr_context: [
    { frame_ids: ["f0002"], start: "4", end: "6" },
    { frame_ids: ["f0001"], start: 0, end: 2 }
  ] })).toBe("00:00 - 00:06");
  expect(formatFramePosition({ timestamp: 3, start: 0, end: 6 })).toBe("00:03");
});

test("invalid or absent times never become invented zero seconds", () => {
  for (const value of [null, undefined, "", "  ", -1, "bad", Infinity, NaN, true, false, []]) {
    expect(evidenceSeconds(value)).toBeNull();
    expect(formatEvidenceTime(value)).toBe("--");
  }
  expect(formatEvidenceRange(8, 2)).toBe("--");
  expect(formatFramePosition({ frame_number: 60 })).toBe("时间未知 · 帧 60");
  expect(formatFramePosition({ frame_id: "f0001", ocr_context: [{ frame_ids: ["f0001"] }] })).toBe("时间未知");
});

test("content detail renders OCR timestamps and honest legacy fallback", async ({ page }) => {
  await page.route("**/api/**", async route => {
    if (route.request().url().includes("/api/audit-results/123")) {
      await route.fulfill({ json: {
        audit_result: { audit_result_id: 123, job_id: "time-preview", risk_level: "high", title: "视频证据时间定位验收", summary: "本地展示测试", comments: [] },
        evidence_groups: [{ type: "ocr", items: [{
          id: "ocr-current", evidence_type: "上下文文字回填", frame_number: 60, timestamp: null, start: 0, end: 6,
          evidence_risk_level: "high", reason: "本地展示测试",
          ocr_context: [
            { frame_ids: ["f0001"], start: 0, end: 0, source_text: "第一帧文字" },
            { frame_ids: ["f0002", "f0003"], start: 2, end: 6, source_text: "连续画面文字", translation_zh: "连续画面译文" },
            { frame_ids: ["f0004"], start: null, end: null, source_text: "旧记录文字" }
          ]
        }, {
          id: "ocr-direct", evidence_type: "直接识别字段", evidence_risk_level: "high", ocr_text: "直接识别原文", ocr_text_zh: "直接译文",
          ocr_context: [{ start: 2, end: 3, source_text: "上下文原文", translation_zh: "上下文译文" }]
        }, {
          id: "ocr-translated-only", evidence_type: "仅有译文记录", evidence_risk_level: "high", ocr_text_zh: "仅有译文", content: "仅有译文"
        }, {
          id: "ocr-legacy-text", evidence_type: "旧字段文字", evidence_risk_level: "high", text: "旧字段原文", text_zh: "旧字段译文"
        }] }], is_historical_config: false
      } });
    } else await route.fulfill({ status: 404, json: { detail: "local fixture" } });
  });
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const { mountEvidenceTime } = await load("/tests/evidenceTimeMount.tsx");
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    mountEvidenceTime(host);
  });
  const evidence = page.locator(".detail-evidence-item").filter({ hasText: "上下文文字回填" });
  await expect(evidence).toContainText("00:00");
  await expect(evidence).toContainText("00:02 - 00:06");
  await expect(evidence).toContainText("连续画面译文");
  await expect(evidence).toContainText("时间未知（关键帧 f0004）");
  await expect(evidence).not.toContainText("[f0001]");
  await expect(evidence.locator(".detail-evidence-inline")).toHaveText("位置00:00 - 00:06 · 帧 60");
  await expect(evidence.getByRole("button", { name: /播放/ })).toHaveCount(0);
  const field = (name: string) => evidence.locator("dl > div").filter({ has: page.locator("dt", { hasText: name }) }).locator("dd");
  await expect(field("帧上下文")).not.toContainText("文字");
  await expect(field("OCR 原文")).toHaveText("第一帧文字\n\n连续画面文字\n\n旧记录文字");
  await expect(field("中文译文")).toHaveText("连续画面译文");
  const direct = page.locator(".detail-evidence-item").filter({ hasText: "直接识别字段" });
  await expect(direct).toContainText("直接识别原文");
  await expect(direct).toContainText("直接译文");
  await expect(direct).not.toContainText("上下文原文");
  await expect(direct).not.toContainText("上下文译文");
  const translatedOnly = page.locator(".detail-evidence-item").filter({ hasText: "仅有译文记录" });
  await expect(translatedOnly.locator("dl > div").filter({ has: page.locator("dt", { hasText: "OCR 原文" }) }).locator("dd")).toHaveText("--");
  const legacyText = page.locator(".detail-evidence-item").filter({ hasText: "旧字段文字" });
  await expect(legacyText).toContainText("旧字段原文");
  await expect(legacyText).toContainText("旧字段译文");
});

test("OCR playback button seeks the video; missing times have no button", async ({ page }, testInfo) => {
  const videoUrl = "/api/test-clip.webm";
  let clip = Buffer.alloc(0);
  await page.route("**/api/**", async route => {
    if (route.request().url().includes(videoUrl)) {
      await route.fulfill({ contentType: "video/webm", body: clip });
      return;
    }
    if (!route.request().url().includes("/api/audit-results/123")) {
      await route.fulfill({ status: 404, json: { detail: "local fixture" } });
      return;
    }
    await route.fulfill({ json: {
      audit_result: { audit_result_id: 123, job_id: "time-preview", risk_level: "high", title: "OCR 时间跳转验收", summary: "本地生成的视频", comments: [], video_results: [{ url: videoUrl }] },
      evidence_groups: [{ type: "ocr", items: [
        { id: "timed", evidence_type: "有时间的画面文字", start: 2, end: 3, frame_number: 60, evidence_risk_level: "high", ocr_context: [{ frame_ids: ["f0001"], start: 2, end: 3, source_text: "测试画面文字" }] },
        { id: "legacy", evidence_type: "旧记录无时间", frame_number: 90, evidence_risk_level: "high", ocr_context: [{ frame_ids: ["f0002"] }] }
      ] }, { type: "asr", items: [{ id: "asr", start: 1, end: 3, source_text_dolphin: "本地音频回归", evidence_risk_level: "high" }] }], is_historical_config: false
    } });
  });
  await page.goto("/");
  const bytes = await page.evaluate(async () => {
    // A local synthetic clip exercises the real HTML video element without
    // fetching user media or mocking currentTime/play.
    const canvas = document.createElement("canvas");
    canvas.width = 320; canvas.height = 180;
    const context = canvas.getContext("2d")!;
    const stream = canvas.captureStream(10);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
    const chunks: Blob[] = [];
    recorder.ondataavailable = event => chunks.push(event.data);
    const stopped = new Promise<void>(resolve => { recorder.onstop = () => resolve(); });
    let frame = 0;
    const timer = window.setInterval(() => {
      context.fillStyle = "#eef4ff"; context.fillRect(0, 0, 320, 180);
      context.fillStyle = "#1e293b"; context.font = "20px sans-serif";
      context.fillText(`Local video ${++frame}`, 40, 95);
    }, 100);
    recorder.start();
    await new Promise(resolve => window.setTimeout(resolve, 4000));
    recorder.stop(); await stopped;
    clearInterval(timer); stream.getTracks().forEach(track => track.stop());
    return Array.from(new Uint8Array(await new Blob(chunks, { type: "video/webm" }).arrayBuffer()));
  });
  clip = Buffer.from(bytes);
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const { mountEvidenceTime } = await load("/tests/evidenceTimeMount.tsx");
    const host = document.createElement("div"); document.body.replaceChildren(host);
    mountEvidenceTime(host);
  });
  const timed = page.locator(".detail-evidence-item").filter({ hasText: "有时间的画面文字" });
  const legacy = page.locator(".detail-evidence-item").filter({ hasText: "旧记录无时间" });
  await expect(timed.locator("dl > div").filter({ has: page.locator("dt", { hasText: "OCR 原文" }) }).locator("dd")).toHaveText("测试画面文字");
  await expect(timed.locator("dl > div").filter({ has: page.locator("dt", { hasText: "帧上下文" }) }).locator("dd")).toHaveText("00:02 - 00:03");
  await expect(legacy.getByRole("button", { name: /播放/ })).toHaveCount(0);
  await timed.getByRole("button", { name: "从 00:02 播放，至 00:03" }).click();
  await expect.poll(() => page.locator("video").evaluate(video => video.currentTime)).toBeGreaterThanOrEqual(2);
  await expect.poll(() => page.locator("video").evaluate(video => video.paused)).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("ocr-time-playback.png"), fullPage: true });
  await page.getByRole("tab", { name: "音频证据 1" }).click();
  await page.getByRole("button", { name: "从 00:01 播放，至 00:03" }).click();
  await expect.poll(() => page.locator("video").evaluate(video => video.currentTime)).toBeLessThan(2);
  await expect.poll(() => page.locator("video").evaluate(video => video.paused)).toBe(false);
});
