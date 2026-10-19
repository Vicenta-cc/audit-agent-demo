import { expect, test } from "@playwright/test";
import { loadAnalysisRecordRun } from "../src/features/investigation/m3AnalysisRecords";

const oldRun = { run_id: "investigation-run:old", report_version_id: "report-version:old", status: "PUBLISHED" };
const newRun = { run_id: "investigation-run:new", report_version_id: "report-version:new", status: "PUBLISHED" };
let requests: string[];
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
let responses: Record<string, unknown>;
let statuses: Record<string, number>;

test.beforeEach(() => {
  requests = [];
  responses = {};
  statuses = {};
  Object.defineProperty(globalThis, "window", { configurable: true, value: { location: { origin: "https://fixture.invalid" } } });
  globalThis.fetch = async (input) => {
    const path = new URL(String(input)).pathname;
    requests.push(path);
    return new Response(JSON.stringify(responses[path] ?? { detail: "调查会话不存在。" }), {
      status: statuses[path] ?? (path in responses ? 200 : 404),
      headers: { "Content-Type": "application/json" }
    });
  };
});

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
});

test("an earlier round loads its own run rather than the latest run", async () => {
  responses["/api/investigation-workspaces/investigation-session%3Achat/state"] = {
    run: newRun,
    rounds: [{ title: "第一次调查", run: oldRun }, { title: "第二次调查", run: newRun }]
  };
  const result = await loadAnalysisRecordRun("investigation-session:chat", oldRun.run_id, oldRun.report_version_id);
  expect(result.run.run_id).toBe(oldRun.run_id);
  expect(result.title).toBe("第一次调查");
});

test("an existing run-based link uses the authorized run endpoint", async () => {
  responses["/api/investigation-runs/investigation-run%3Aold"] = oldRun;
  const result = await loadAnalysisRecordRun(oldRun.run_id, oldRun.run_id, oldRun.report_version_id);
  expect(result.run.run_id).toBe(oldRun.run_id);
  expect(requests).toEqual(["/api/investigation-runs/investigation-run%3Aold"]);
});

test("a run-based path cannot silently select another run", async () => {
  await expect(loadAnalysisRecordRun(oldRun.run_id, newRun.run_id, "")).rejects.toThrow("调查任务引用不一致");
  expect(requests).toEqual([]);
});

test("a missing round does not fall back to the latest run", async () => {
  responses["/api/investigation-workspaces/chat/state"] = { run: newRun, rounds: [] };
  await expect(loadAnalysisRecordRun("chat", oldRun.run_id, "")).rejects.toThrow("当前调查没有匹配的真实 Run");
});

test("a mismatched report is rejected", async () => {
  responses["/api/investigation-runs/investigation-run%3Aold"] = oldRun;
  await expect(loadAnalysisRecordRun(oldRun.run_id, oldRun.run_id, newRun.report_version_id)).rejects.toThrow("Run 与 ReportVersion 引用不一致");
});

test("legacy single-round workspace responses remain supported", async () => {
  responses["/api/investigation-workspaces/chat/state"] = { run: oldRun, draft_artifact: { draft: { title: "原调查" } } };
  expect((await loadAnalysisRecordRun("chat", oldRun.run_id, "")).title).toBe("原调查");
});

test("an inaccessible workspace is not retried through a run endpoint", async () => {
  await expect(loadAnalysisRecordRun("chat", oldRun.run_id, "")).rejects.toThrow("调查会话不存在。");
  expect(requests).toEqual(["/api/investigation-workspaces/chat/state"]);
});

for (const status of [401, 403, 404, 503]) {
  for (const legacyRunLink of [false, true]) {
    test(`${legacyRunLink ? "legacy run" : "workspace"} HTTP ${status} is preserved without fallback`, async () => {
      const investigationId = legacyRunLink ? oldRun.run_id : "chat";
      const path = legacyRunLink
        ? "/api/investigation-runs/investigation-run%3Aold"
        : "/api/investigation-workspaces/chat/state";
      responses[path] = { detail: "读取被拒绝或暂不可用" };
      statuses[path] = status;
      await expect(loadAnalysisRecordRun(investigationId, oldRun.run_id, ""))
        .rejects.toMatchObject({ status });
      expect(requests).toEqual([path]);
    });
  }
}

test("a workspace round with a mismatched report is rejected", async () => {
  responses["/api/investigation-workspaces/chat/state"] = {
    run: newRun,
    rounds: [{ title: "第一次调查", run: oldRun }]
  };
  await expect(loadAnalysisRecordRun("chat", oldRun.run_id, newRun.report_version_id))
    .rejects.toThrow("Run 与 ReportVersion 引用不一致");
  expect(requests).toEqual(["/api/investigation-workspaces/chat/state"]);
});

test("a legacy run endpoint returning another run is rejected", async () => {
  responses["/api/investigation-runs/investigation-run%3Aold"] = newRun;
  await expect(loadAnalysisRecordRun(oldRun.run_id, oldRun.run_id, ""))
    .rejects.toThrow("当前调查没有匹配的真实 Run");
});

test("the matching current run remains supported when rounds omit it", async () => {
  responses["/api/investigation-workspaces/chat/state"] = {
    run: newRun,
    rounds: [{ title: "第一次调查", run: oldRun }],
    draft_artifact: { draft: { title: "当前调查标题" } }
  };
  const result = await loadAnalysisRecordRun("chat", newRun.run_id, newRun.report_version_id);
  expect(result.run.run_id).toBe(newRun.run_id);
  expect(result.title).toBe("当前调查标题");
});
