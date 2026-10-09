import { expect, test, type Page } from "@playwright/test";

const turnId = "existing-turn";
async function start(page: Page, nativeStream = false) {
  await page.route("**/turn-lifetime-test", route => route.fulfill({ contentType: "text/html", body: "<html><body>Turn lifetime test</body></html>" }));
  await page.goto("/turn-lifetime-test");
  await page.clock.install({ time: new Date("2026-10-09T00:00:00Z") });
  await page.evaluate(async (nativeStream) => {
    const load = (path: string) => import(path);
    const { waitForTurn } = await load("/src/services/investigations.ts");
    const state: any = { result: null, error: "", closed: 0 };
    class Source extends EventTarget {
      constructor() { super(); state.source = this; }
      close() { state.closed++; }
    }
    if (!nativeStream) Object.defineProperty(window, "EventSource", { configurable: true, value: Source });
    state.controller = new AbortController();
    state.pending = waitForTurn("existing-turn", {
      statusPath: "/api/lifetime/status", eventPath: "/api/lifetime/events"
    }, { signal: state.controller.signal })
      .then((result: unknown) => { state.result = result; })
      .catch((error: Error) => { state.error = error.message; });
    (window as any).lifetime = state;
  }, nativeStream);
}
const read = (page: Page) => page.evaluate(() => {
  const { result, error, closed } = (window as any).lifetime;
  return { result, error, closed };
});
const completed = {
  turn_id: turnId, status: "completed", answer: "已保存，版本 8",
  artifact: { receipt_id: "saved-once" }, safe_message: "", retryable: false
};

test("240-second completion is delivered automatically with the existing status polling", async ({ page }) => {
  let ready = false;
  const methods: string[] = [];
  await page.route("**/api/lifetime/status", route => {
    methods.push(route.request().method());
    return route.fulfill({ json: ready ? completed : { turn_id: turnId, status: "running" } });
  });
  await start(page);
  await page.clock.runFor(1000);
  await expect.poll(() => methods.length).toBe(1);
  await page.clock.fastForward(180000);
  await expect.poll(() => methods.length).toBe(2);
  expect(await read(page)).toEqual({ result: null, error: "", closed: 0 });
  ready = true;
  await page.clock.fastForward(59000);
  await expect.poll(async () => (await read(page)).result).toEqual(completed);
  expect((await read(page)).closed).toBe(1);
  expect(methods.every(method => method === "GET")).toBe(true);
});

test("completion can still arrive over SSE after 180 seconds", async ({ page }) => {
  await page.route("**/api/lifetime/status", route => route.fulfill({ json: { turn_id: turnId, status: "running" } }));
  await start(page);
  await page.clock.fastForward(240000);
  await page.evaluate(() => (window as any).lifetime.source.dispatchEvent(new MessageEvent("turn", {
    data: JSON.stringify({
      event_id: "event-final", turn_id: "existing-turn", sequence: 2,
      occurred_at: new Date().toISOString(), stage: "completed", answer: "完整回答",
      safe_message: "", retryable: false
    })
  })));
  await expect.poll(async () => (await read(page)).result?.answer).toBe("完整回答");
  expect((await read(page)).error).toBe("");
});

test("connection loss and temporary 503 recover the original receipt using GET only", async ({ page }) => {
  let unavailable = true;
  let count = 0;
  const methods: string[] = [];
  await page.route("**/api/lifetime/status", route => {
    count++;
    methods.push(route.request().method());
    return unavailable ? route.fulfill({ status: 503, body: "temporary outage" }) : route.fulfill({ json: completed });
  });
  await start(page);
  await page.evaluate(() => (window as any).lifetime.source.dispatchEvent(new Event("error")));
  await page.clock.runFor(1000);
  await expect.poll(() => count).toBe(1);
  await page.clock.fastForward(181000);
  await expect.poll(() => count).toBe(2);
  expect((await read(page)).error).toBe("");
  unavailable = false;
  await page.clock.runFor(1600);
  await expect.poll(async () => (await read(page)).result).toEqual(completed);
  expect(methods).toEqual(["GET", "GET", "GET"]);
});

for (const status of ["error", "interrupted"]) {
  test(`backend ${status} terminates waiting after its own execution deadline`, async ({ page }) => {
    let ended = false;
    await page.route("**/api/lifetime/status", route => route.fulfill({
      json: ended
        ? { turn_id: turnId, status, safe_message: "执行已结束", retryable: false }
        : { turn_id: turnId, status: "running" }
    }));
    await start(page);
    await page.clock.runFor(1000);
    ended = true;
    await page.clock.fastForward(600000);
    await expect.poll(async () => (await read(page)).result?.status).toBe(status);
    expect((await read(page)).closed).toBe(1);
  });
}

test("leaving only stops observation; re-entering reads the original completed result", async ({ page }) => {
  let ready = false;
  const methods: string[] = [];
  await page.route("**/api/lifetime/status", route => {
    methods.push(route.request().method());
    return route.fulfill({ json: ready ? completed : { turn_id: turnId, status: "running" } });
  });
  await start(page);
  await page.clock.runFor(1000);
  await expect.poll(() => methods.length).toBe(1);
  await page.evaluate(() => (window as any).lifetime.controller.abort());
  await expect.poll(async () => (await read(page)).error).toContain("cancelled");
  expect((await read(page)).closed).toBe(1);
  // Discard the browser's in-memory wait. The backend's durable turn remains.
  ready = true;
  await page.reload();
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const { waitForTurn } = await load("/src/services/investigations.ts");
    class Source extends EventTarget { close() {} }
    Object.defineProperty(window, "EventSource", { value: Source });
    (window as any).restored = waitForTurn("existing-turn", {
      statusPath: "/api/lifetime/status", eventPath: "/api/lifetime/events"
    });
  });
  await page.clock.runFor(1000);
  expect(await page.evaluate(() => (window as any).restored)).toEqual(completed);
  expect(methods).toEqual(["GET", "GET"]);
});

test("native EventSource reconnects with the last event ID and delivers the original completion", async ({ page }) => {
  const requests: { method: string; cursor?: string }[] = [];
  await page.route("**/api/lifetime/status", route => route.fulfill({ json: { turn_id: turnId, status: "running" } }));
  await page.route("**/api/lifetime/events", route => {
    requests.push({ method: route.request().method(), cursor: route.request().headers()["last-event-id"] });
    const complete = requests.length > 1;
    const event = {
      event_id: complete ? "event-2" : "event-1", turn_id: turnId,
      sequence: complete ? 2 : 1, occurred_at: new Date().toISOString(),
      stage: complete ? "completed" : "planning", answer: complete ? "原请求已保存" : "",
      safe_message: "", retryable: false
    };
    // Finite body closes the first stream, forcing native browser reconnection.
    return route.fulfill({
      contentType: "text/event-stream",
      body: `retry: 100\nid: ${event.event_id}\nevent: turn\ndata: ${JSON.stringify(event)}\n\n`
    });
  });
  await start(page, true);
  await expect.poll(async () => (await read(page)).result?.answer, { timeout: 10000 }).toBe("原请求已保存");
  expect(requests).toEqual([{ method: "GET", cursor: undefined }, { method: "GET", cursor: "event-1" }]);
  expect((await read(page)).error).toBe("");
});
