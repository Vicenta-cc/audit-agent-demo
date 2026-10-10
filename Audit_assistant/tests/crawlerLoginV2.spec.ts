import { expect, test } from "@playwright/test";
import type { WebSocketRoute } from "@playwright/test";
import { decodeLoginFrame } from "../src/services/crawlerLoginV2";
const png =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==";
const snapshot = (phase = "qr_ready", revision = 1) => ({
  protocol_version: 2,
  engine: "v2",
  id: "login-fixture",
  operation_id: "operation-fixture",
  account_id: "account-fixture",
  status: phase === "qr_ready" ? "waiting_scan" : "interactive",
  phase,
  state_revision: revision,
  controller_epoch: 1,
  controller_state: "attached",
  expires_at: new Date(Date.now() + 600000).toISOString(),
  available_actions: ["refresh_qr", "open_manual", "request_sms", "submit_sms"],
  qr: {
    revision: "fixture-image-sha256",
    state: "valid",
    image_data_url: `data:image/png;base64,${png}`,
    expires_at: null,
  },
  sms:
    phase === "sms_required"
      ? {
          fields: [
            { name: "phone", label: "手机号", max_length: 20 },
            { name: "code", label: "短信验证码", max_length: 6 },
          ],
          masked_phone: "",
          send_status: "ready",
        }
      : null,
  challenge:
    phase === "challenge_required"
      ? {
          challenge_id: "challenge-fixture",
          area_version: 2,
          kind: "draw",
          interaction_mode: "draw",
          clip_rect: { x: 10, y: 20, width: 320, height: 180 },
          viewport: { width: 1280, height: 800 },
          image_width: 320,
          image_height: 180,
          allowed_actions: ["pointer_batch", "text", "key", "scroll"],
        }
      : null,
  identity: { status: "pending", platform_account_id: "" },
  error: null,
  result: null,
});
function packet() {
  const metadata = Buffer.from(
    JSON.stringify({
      controller_epoch: 1,
      challenge_id: "challenge-fixture",
      codec: "jpeg",
      image_width: 1,
      image_height: 1,
    }),
  );
  const image = Buffer.from(png, "base64");
  const header = Buffer.alloc(28);
  header.write("DLV2");
  header[4] = 2;
  header[5] = 1;
  header.writeUInt16BE(metadata.length, 6);
  header.writeUInt32BE(image.length, 8);
  header.writeUInt32BE(1, 12);
  header.writeUInt32BE(2, 16);
  return Buffer.concat([header, metadata, image]);
}
test("binary parser rejects credential packets, truncation and malformed length", () => {
  const value = packet();
  const buffer = value.buffer.slice(
    value.byteOffset,
    value.byteOffset + value.byteLength,
  ) as ArrayBuffer;
  expect(decodeLoginFrame(buffer).area_version).toBe(2);
  expect(() => decodeLoginFrame(buffer.slice(0, -1))).toThrow("长度");
  const secret = buffer.slice(0);
  new Uint8Array(secret)[5] = 2;
  expect(() => decodeLoginFrame(secret)).toThrow("格式");
});
for (const viewport of [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`QR conditional SMS and drawing preserve protocol at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let starts = 0;
    let socket: WebSocketRoute;
    const commands: Record<string, any>[] = [];
    await page.route(
      "**/api/crawler-accounts/account-fixture/login-sessions",
      (route) => {
        starts++;
        const body = route.request().postDataJSON();
        expect(body.supported_protocols).toEqual([2, 1]);
        return route.fulfill({ json: { item: snapshot() } });
      },
    );
    await page.route(
      "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
      (route) =>
        route.fulfill({ json: { ticket: "fixture-ticket", expires_in: 30 } }),
    );
    await page.route(
      "**/api/crawler-account-login-sessions/login-fixture",
      (route) =>
        route.fulfill({
          json: {
            item: { ...snapshot(), status: "cancelled", phase: "cancelled" },
          },
        }),
    );
    await page.routeWebSocket(
      "**/api/crawler-account-login-sessions/login-fixture/stream",
      (ws) => {
        socket = ws;
        ws.onMessage((data) => {
          const command = JSON.parse(String(data));
          if (command.type === "auth") {
            expect(command.ticket).toBe("fixture-ticket");
            ws.send(JSON.stringify({ type: "snapshot", item: snapshot() }));
          } else {
            commands.push(command);
            if (command.type === "action")
              ws.send(
                JSON.stringify({
                  type: "command_result",
                  command_id: command.command_id,
                  execution_status: "executed",
                  platform_status: "confirmed",
                }),
              );
          }
        });
      },
    );
    await page.goto("/tests/crawlerLoginV2.html");
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新二维码", exact: true })).toBeEnabled();
    await expect(page.getByAltText("抖音登录二维码")).toBeVisible();
    await expect(page.getByLabel("短信验证码")).toHaveCount(0);
    expect(starts).toBe(1);
    await page.getByRole("button", { name: "刷新二维码" }).click();
    await expect.poll(() => commands.length).toBe(1);
    expect(commands[0]).toMatchObject({
      action: "refresh_qr",
      controller_epoch: 1,
      operation_id: "operation-fixture",
    });
    await page.screenshot({
      path: `/Users/hose/.codex/visualizations/2026/10/09/01a12072-f58f-7b03-a114-522562084e7b/login-v2-qr-${viewport.width}.png`,
    });
    socket!.send(
      JSON.stringify({ type: "snapshot", item: snapshot("sms_required", 2) }),
    );
    await page.getByLabel("手机号", { exact: true }).fill("13800000000");
    await page.getByLabel("短信验证码").fill("123456");
    expect(commands.length).toBe(1);
    await page.getByRole("button", { name: "发送验证码" }).click();
    await expect.poll(() => commands.length).toBe(2);
    expect(commands[1]).toMatchObject({
      action: "request_sms",
      payload: { phone: "13800000000" },
    });
    await page.getByLabel("短信验证码").fill("123456");
    await expect(
      page.getByRole("button", { name: "提交验证码" }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "提交验证码" }).click();
    await expect.poll(() => commands.length).toBe(3);
    expect(commands[2].payload).toEqual({ code: "123456" });
    socket!.send(
      JSON.stringify({
        type: "snapshot",
        item: snapshot("challenge_required", 3),
      }),
    );
    socket!.send(packet());
    await expect(page.getByLabel("人工验证区域")).toBeVisible();
    const canvas = page.getByLabel("人工验证区域");
    const bounds = (await canvas.boundingBox())!;
    await page.mouse.move(bounds.x + 10, bounds.y + 10);
    await page.mouse.down();
    await page.mouse.move(bounds.x + 60, bounds.y + 40, { steps: 12 });
    await page.mouse.up();
    await expect
      .poll(
        () => commands.filter((item) => item.type === "pointer_batch").length,
      )
      .toBeGreaterThan(1);
    const points = commands
      .filter((item) => item.type === "pointer_batch")
      .flatMap((item) => item.points);
    expect(points[0].event).toBe("down");
    expect(points[points.length - 1].event).toBe("up");
    expect(
      points.filter((point) => point.event === "move").length,
    ).toBeGreaterThan(2);
    expect(
      points.every(
        (point) => point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1,
      ),
    ).toBeTruthy();
    await page.getByLabel("填写当前选中的输入框").fill("中文验证码123");
    await page.getByRole("button", { name: "输入", exact: true }).click();
    await expect
      .poll(() =>
        commands.some(
          (item) => item.type === "text" && item.text === "中文验证码123",
        ),
      )
      .toBeTruthy();
    await expect(page.getByLabel("填写当前选中的输入框")).toHaveValue("");
    socket!.send(
      JSON.stringify({ type: "snapshot", item: snapshot("sms_required", 4) }),
    );
    await expect(page.getByLabel("短信验证码")).toBeVisible();
    await expect(canvas).toHaveCount(0);
    socket!.send(
      JSON.stringify({
        type: "snapshot",
        item: {
          ...snapshot("done", 5),
          status: "success",
          result: {
            auth_saved: true,
            identity_verified: true,
            reuse_verified: true,
            account_eligibility: "cooling_down",
          },
        },
      }),
    );
    await expect(
      page.getByText("账号仍处于冷却中，登录不会解除冷却。", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "查看当前登录步骤" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "完成", exact: true }),
    ).toHaveClass(/mt-button-primary/);
    await page.screenshot({
      path: `/Users/hose/.codex/visualizations/2026/10/09/01a12072-f58f-7b03-a114-522562084e7b/login-v2-result-${viewport.width}.png`,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    expect(errors).toEqual([]);
    await page.getByRole("button", { name: "关闭登录窗口" }).click();
    await expect(page.getByText("登录窗口已关闭")).toBeVisible();
  });
}

test("reconnect uses a new ticket and queries uncertain command without replay; auth clear stops controls", async ({
  page,
}) => {
  let tickets = 0;
  let sockets = 0;
  let queried = 0;
  const commands: any[] = [];
  let connected: WebSocketRoute;
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) => route.fulfill({ json: { item: snapshot("sms_required") } }),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
    (route) =>
      route.fulfill({
        json: { ticket: `ticket-${++tickets}`, expires_in: 30 },
      }),
  );
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-operations/operation-fixture/commands/*",
    (route) => {
      queried++;
      return route.fulfill({
        json: {
          item: { execution_status: "dispatching", platform_status: "unknown" },
        },
      });
    },
  );
  await page.routeWebSocket(
    "**/api/crawler-account-login-sessions/login-fixture/stream",
    (ws) => {
      connected = ws;
      sockets++;
      ws.onMessage((data) => {
        const command = JSON.parse(String(data));
        if (command.type === "auth")
          ws.send(
            JSON.stringify({
              type: "snapshot",
              item: { ...snapshot("sms_required"), controller_epoch: sockets },
            }),
          );
        else {
          commands.push(command);
          if (command.action === "request_sms")
            ws.close({ code: 1011, reason: "fixture interruption" });
        }
      });
    },
  );
  await page.goto("/tests/crawlerLoginV2.html");
  await expect(page.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await page.getByLabel("手机号", { exact: true }).fill("13800000000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await expect.poll(() => tickets).toBe(2);
  await expect.poll(() => queried).toBe(1);
  expect(commands.filter((item) => item.action === "request_sms")).toHaveLength(
    1,
  );
  await expect(page.getByRole("button", { name: "发送验证码" })).toBeDisabled();
  await page.evaluate(() =>
    window.dispatchEvent(new Event("application-auth-cleared")),
  );
  await expect(page.getByLabel("手机号", { exact: true })).toHaveValue("");
  await expect(
    page.getByText("应用登录已结束，请重新登录后打开此窗口", { exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(700);
  expect(tickets).toBe(2);
  expect(sockets).toBe(2);
});

test("403 owner rejection never creates a replacement session", async ({
  page,
}) => {
  let starts = 0;
  await page.addInitScript(() =>
    window.sessionStorage.setItem(
      "crawler-account-login-session:account-fixture",
      "old-session",
    ),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/old-session",
    (route) =>
      route.fulfill({
        status: 403,
        json: {
          detail: { code: "OWNER_MISMATCH", message: "登录控制属于其他窗口" },
        },
      }),
  );
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) => {
      starts++;
      return route.fulfill({ json: { item: snapshot() } });
    },
  );
  await page.goto("/tests/crawlerLoginV2.html");
  await expect(
    page.getByText("登录控制属于其他窗口", { exact: true }),
  ).toBeVisible();
  expect(starts).toBe(0);
});

test("missing live session offers explicit recovery without resetting credentials", async ({
  page,
}) => {
  let intent = "";
  await page.addInitScript(() =>
    window.sessionStorage.setItem(
      "crawler-account-login-session:account-fixture",
      "lost-session",
    ),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/lost-session",
    (route) => route.fulfill({ status: 404, json: { detail: "会话不存在" } }),
  );
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-operations?recoverable=true",
    (route) =>
      route.fulfill({
        json: {
          items: [{ id: "interrupted-operation", updated_at: "2026-10-09" }],
        },
      }),
  );
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) => {
      const body = route.request().postDataJSON();
      intent = body.intent;
      expect(body.source_operation_id).toBe("interrupted-operation");
      return route.fulfill({
        json: {
          item: {
            ...snapshot("done"),
            status: "success",
            result: {
              auth_saved: true,
              identity_verified: true,
              reuse_verified: true,
              account_eligibility: "ready",
            },
          },
        },
      });
    },
  );
  await page.goto("/tests/crawlerLoginV2.html");
  await expect(page.getByText("上次登录保存过程被中断")).toBeVisible();
  expect(intent).toBe("");
  await page.getByRole("button", { name: "恢复登录信息" }).click();
  await expect(
    page.getByRole("button", { name: "完成", exact: true }),
  ).toBeVisible();
  expect(intent).toBe("recover");
});

test("expired QR without an image can refresh, and rejected command shows safe server message", async ({
  page,
}) => {
  const expired = { ...snapshot("qr_expired"), qr: null };
  let socket: WebSocketRoute;
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) => route.fulfill({ json: { item: expired } }),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
    (route) =>
      route.fulfill({ json: { ticket: "fixture-ticket", expires_in: 30 } }),
  );
  await page.routeWebSocket(
    "**/api/crawler-account-login-sessions/login-fixture/stream",
    (ws) => {
      socket = ws;
      ws.onMessage((data) => {
        const command = JSON.parse(String(data));
        if (command.type === "auth")
          ws.send(JSON.stringify({ type: "snapshot", item: expired }));
        else if (command.action === "refresh_qr")
          ws.send(JSON.stringify({ type: "snapshot", item: snapshot() }));
        else if (command.action === "submit_sms")
          ws.send(
            JSON.stringify({
              type: "command_result",
              command_id: command.command_id,
              execution_status: "rejected",
              platform_status: "rejected",
              error_code: "SOME_INTERNAL_CODE",
              error_message: "验证码不正确，请重新输入",
            }),
          );
      });
    },
  );
  await page.goto("/tests/crawlerLoginV2.html");
  await expect(page.getByText("二维码已过期", { exact: true })).toBeVisible();
  await expect(page.getByAltText("抖音登录二维码")).toHaveCount(0);
  await page.getByRole("button", { name: "刷新二维码" }).click();
  await expect(page.getByAltText("抖音登录二维码")).toBeVisible();
  socket!.send(
    JSON.stringify({ type: "snapshot", item: snapshot("sms_required", 2) }),
  );
  await page.getByLabel("短信验证码").fill("123456");
  await page.getByRole("button", { name: "提交验证码" }).click();
  await expect(page.getByRole("alert")).toHaveText("验证码不正确，请重新输入");
  await expect(page.getByText("SOME_INTERNAL_CODE")).toHaveCount(0);
});

test("stale state rejected before execution releases pending only for a new user retry", async ({
  page,
}) => {
  const commands: any[] = [];
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) => route.fulfill({ json: { item: snapshot("sms_required") } }),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
    (route) => route.fulfill({ json: { ticket: "ticket", expires_in: 30 } }),
  );
  await page.routeWebSocket(
    "**/api/crawler-account-login-sessions/login-fixture/stream",
    (ws) =>
      ws.onMessage((data) => {
        const command = JSON.parse(String(data));
        if (command.type === "auth")
          ws.send(
            JSON.stringify({
              type: "snapshot",
              item: snapshot("sms_required"),
            }),
          );
        else {
          commands.push(command);
          ws.send(
            JSON.stringify({
              type: "error",
              code: "STALE_STATE",
              message: "登录步骤已变化，请重试",
              command_id: command.command_id,
              execution_status: "rejected",
              platform_status: "not_applicable",
            }),
          );
        }
      }),
  );
  await page.goto("/tests/crawlerLoginV2.html");
  await expect(page.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "发送验证码" }).click();
  await expect(page.getByRole("button", { name: "发送验证码" })).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveText("登录步骤已变化，请重试");
  expect(commands).toHaveLength(1);
  await page.getByRole("button", { name: "发送验证码" }).click();
  await expect.poll(() => commands.length).toBe(2);
  expect(commands[0].command_id).not.toBe(commands[1].command_id);
});
for (const executionStatus of ["not_executed", "rejected"]) {
  test(`reconnect query ${executionStatus} not_applicable releases pending without replay`, async ({
    page,
  }) => {
    let connections = 0;
    let queried = 0;
    const commands: any[] = [];
    await page.route(
      "**/api/crawler-accounts/account-fixture/login-sessions",
      (route) => route.fulfill({ json: { item: snapshot("sms_required") } }),
    );
    await page.route(
      "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
      (route) => route.fulfill({ json: { ticket: "ticket", expires_in: 30 } }),
    );
    await page.route(
      "**/api/crawler-accounts/account-fixture/login-operations/operation-fixture/commands/*",
      (route) => {
        queried++;
        return route.fulfill({
          json: {
            item: {
              execution_status: executionStatus,
              platform_status: "not_applicable",
              error_message: "上次操作未执行，可以重新提交",
            },
          },
        });
      },
    );
    await page.routeWebSocket(
      "**/api/crawler-account-login-sessions/login-fixture/stream",
      (ws) => {
        connections++;
        ws.onMessage((data) => {
          const command = JSON.parse(String(data));
          if (command.type === "auth")
            ws.send(
              JSON.stringify({
                type: "snapshot",
                item: {
                  ...snapshot("sms_required"),
                  controller_epoch: connections,
                },
              }),
            );
          else {
            commands.push(command);
            ws.close({ code: 1011 });
          }
        });
      },
    );
    await page.goto("/tests/crawlerLoginV2.html");
    await expect(page.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "发送验证码" }).click();
    await expect.poll(() => queried).toBe(1);
    await expect(
      page.getByRole("button", { name: "发送验证码" }),
    ).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveText(
      "上次操作未执行，可以重新提交",
    );
    expect(commands).toHaveLength(1);
  });
}

test("login does not ask users to choose a browser mode or restart after QR", async ({ page }) => {
  let starts = 0; let cancels = 0;
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { starts++; return route.fulfill({ json: { item: snapshot() } }); });
  await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'ticket', expires_in: 30 } }));
  await page.route('**/api/crawler-account-login-sessions/login-fixture', route => { cancels++; return route.fulfill({ json: { item: snapshot() } }); });
  await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => ws.onMessage(data => { if (JSON.parse(String(data)).type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: snapshot() })); }));
  await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByAltText('抖音登录二维码')).toBeVisible();
  await expect(page.getByText(/兼容模式|已连接|接续|服务器需要/)).toHaveCount(0); await expect(page.getByRole('button', { name: '查看当前登录步骤' })).toHaveCount(0);
  await page.waitForTimeout(300); expect(starts).toBe(1); expect(cancels).toBe(0);
});

test("gesture notice is visible and preserves manual retry without replay", async ({
  page,
}) => {
  let socket: WebSocketRoute;
  const commands: any[] = [];
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) =>
      route.fulfill({ json: { item: snapshot("challenge_required") } }),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
    (route) => route.fulfill({ json: { ticket: "ticket", expires_in: 30 } }),
  );
  await page.routeWebSocket(
    "**/api/crawler-account-login-sessions/login-fixture/stream",
    (ws) => {
      socket = ws;
      ws.onMessage((data) => {
        const command = JSON.parse(String(data));
        if (command.type === "auth") {
          ws.send(
            JSON.stringify({
              type: "snapshot",
              item: snapshot("challenge_required"),
            }),
          );
          ws.send(packet());
        } else commands.push(command);
      });
    },
  );
  await page.goto("/tests/crawlerLoginV2.html");
  const canvas = page.getByLabel("人工验证区域");
  await expect(canvas).not.toHaveClass(/is-disabled/);
  socket!.send(
    JSON.stringify({
      type: "notice",
      code: "GESTURE_EXPIRED",
      message: "本次操作已超时，请重新拖动或画图",
    }),
  );
  await expect(page.getByRole("alert")).toHaveText(
    "本次操作已超时，请重新拖动或画图",
  );
  expect(commands).toHaveLength(0);
  const bounds = (await canvas.boundingBox())!;
  await page.mouse.click(bounds.x + 20, bounds.y + 20);
  await expect
    .poll(
      () =>
        commands.filter((command) => command.type === "pointer_batch").length,
    )
    .toBe(2);
});

async function prepareDrawing(page: import("@playwright/test").Page) {
  const commands: any[] = [];
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) =>
      route.fulfill({ json: { item: snapshot("challenge_required") } }),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
    (route) => route.fulfill({ json: { ticket: "ticket", expires_in: 30 } }),
  );
  await page.routeWebSocket(
    "**/api/crawler-account-login-sessions/login-fixture/stream",
    (ws) =>
      ws.onMessage((data) => {
        const command = JSON.parse(String(data));
        if (command.type === "auth") {
          ws.send(
            JSON.stringify({
              type: "snapshot",
              item: snapshot("challenge_required"),
            }),
          );
          ws.send(packet());
        } else commands.push(command);
      }),
  );
  await page.goto("/tests/crawlerLoginV2.html");
  const canvas = page.getByLabel("人工验证区域");
  await expect(canvas).not.toHaveClass(/is-disabled/);
  const bounds = (await canvas.boundingBox())!;
  await page.mouse.move(bounds.x + 20, bounds.y + 20);
  return { canvas, commands };
}
test("drawing human pause after flushed down still sends up without replaying elapsed time", async ({
  page,
}) => {
  const { commands } = await prepareDrawing(page);
  await page.mouse.down();
  await expect.poll(() => commands.length).toBe(1);
  await page.waitForTimeout(300);
  await page.mouse.up();
  await expect.poll(() => commands.length).toBe(2);
  expect(commands[0].points[0].event).toBe("down");
  expect(commands[1].points[0]).toMatchObject({ event: "up", dt_ms: 0 });
  expect(
    commands.some((command) => command.type === "abort_gesture"),
  ).toBeFalsy();
});
test("drawing genuinely queued points older than 250ms aborts with visible retry message", async ({
  page,
}) => {
  const { canvas, commands } = await prepareDrawing(page);
  await page.mouse.down();
  await expect.poll(() => commands.length).toBe(1);
  await canvas.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const realNow = performance.now.bind(performance);
    let offset = 0;
    Object.defineProperty(performance, "now", {
      configurable: true,
      value: () => realNow() + offset,
    });
    const move = () =>
      element.dispatchEvent(
        new PointerEvent("pointermove", {
          bubbles: true,
          pointerId: 1,
          clientX: rect.x + 30,
          clientY: rect.y + 30,
        }),
      );
    move();
    offset = 300;
    move();
    Object.defineProperty(performance, "now", {
      configurable: true,
      value: realNow,
    });
  });
  await expect(page.getByRole("alert")).toHaveText(
    "操作发送积压，本次手势已停止，请重新拖动或画图",
  );
  await expect
    .poll(() => commands.some((command) => command.type === "abort_gesture"))
    .toBeTruthy();
  expect(
    commands.filter((command) => command.type === "pointer_batch"),
  ).toHaveLength(1);
  await page.mouse.up();
});
test("socket send backlog stops input and relies on disconnect for reliable release", async ({
  page,
}) => {
  await page.route(
    "**/api/crawler-accounts/account-fixture/login-sessions",
    (route) =>
      route.fulfill({ json: { item: snapshot("challenge_required") } }),
  );
  await page.route(
    "**/api/crawler-account-login-sessions/login-fixture/ws-tickets",
    (route) => route.fulfill({ json: { ticket: "ticket", expires_in: 30 } }),
  );
  await page.addInitScript(
    ({ state, bytes }) => {
      (window as any).__testCommands = [];
      class FixtureSocket {
        static OPEN = 1;
        static CLOSED = 3;
        readyState = 0;
        bufferedAmount = 0;
        binaryType = "arraybuffer";
        onopen: any;
        onmessage: any;
        onclose: any;
        onerror: any;
        constructor() {
          (window as any).__testSocket = this;
          setTimeout(() => {
            this.readyState = 1;
            this.onopen?.();
          }, 0);
        }
        send(data: string) {
          const command = JSON.parse(data);
          if (command.type === "auth") {
            this.onmessage?.({
              data: JSON.stringify({ type: "snapshot", item: state }),
            });
            this.onmessage?.({ data: new Uint8Array(bytes).buffer });
          } else (window as any).__testCommands.push(command);
        }
        close(code: number) {
          this.readyState = 3;
          this.onclose?.({ code });
        }
      }
      window.WebSocket = FixtureSocket as any;
    },
    { state: snapshot("challenge_required"), bytes: Array.from(packet()) },
  );
  await page.goto("/tests/crawlerLoginV2.html");
  const canvas = page.getByLabel("人工验证区域");
  await expect(canvas).not.toHaveClass(/is-disabled/);
  await page.evaluate(() => {
    (window as any).__testSocket.bufferedAmount = 65537;
  });
  const bounds = (await canvas.boundingBox())!;
  await page.mouse.move(bounds.x + 20, bounds.y + 20);
  await page.mouse.down();
  await expect(page.getByRole("alert")).toHaveText(
    "连接发送积压，本次操作已停止，恢复连接后请重新操作",
  );
  expect(await page.evaluate(() => (window as any).__testCommands.length)).toBe(
    0,
  );
  expect(
    await page.evaluate(() => (window as any).__testSocket.readyState),
  ).toBe(3);
  await page.mouse.up();
});

for (const width of [1280, 390]) {
  test(`user flow shows QR, phone confirmation, conditional verification and identified result at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 }); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message)); let socket: WebSocketRoute; let starts = 0;
    await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { starts++; return route.fulfill({ json: { item: snapshot() } }); });
    await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'ticket', expires_in: 30 } }));
    await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => { socket = ws; ws.onMessage(data => { if (JSON.parse(String(data)).type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: snapshot() })); }); });
    await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByText('打开抖音扫一扫', { exact: true })).toBeVisible(); await expect(page.getByAltText('抖音登录二维码')).toBeVisible();
    await expect(page.getByLabel('短信验证码')).toHaveCount(0); await expect(page.getByText(/兼容模式|已连接|Profile|接续检查/)).toHaveCount(0);
    socket!.send(JSON.stringify({ type: 'snapshot', item: { ...snapshot('preparing', 2), status: 'scanned', qr: null } }));
    await expect(page.getByText('请在手机上确认登录', { exact: true })).toBeVisible(); await expect(page.getByAltText('抖音登录二维码')).toHaveCount(0);
    await page.screenshot({ path: `/Users/hose/.codex/visualizations/2026/10/09/01a12072-f58f-7b03-a114-522562084e7b/login-user-phone-${width}.png` });
    socket!.send(JSON.stringify({ type: 'snapshot', item: snapshot('challenge_required', 3) })); socket!.send(packet()); await expect(page.getByText('完成安全验证', { exact: true })).toBeVisible(); await expect(page.getByLabel('人工验证区域')).toBeVisible();
    socket!.send(JSON.stringify({ type: 'snapshot', item: { ...snapshot('identity_check', 4), status: 'finalizing', qr: null } })); await expect(page.getByText('正在完成登录，请稍候', { exact: true })).toBeVisible();
    socket!.send(JSON.stringify({ type: 'snapshot', item: { ...snapshot('done', 5), status: 'success', identity: { status: 'verified' }, result: { auth_saved: true, identity_verified: true, reuse_verified: true, platform_account_id: 'verified-douyin-account', account_eligibility: 'ready' } } }));
    await expect(page.getByText('登录成功', { exact: true })).toBeVisible(); await expect(page.getByText('verified-douyin-account', { exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '完成', exact: true })).toBeVisible(); expect(starts).toBe(1);
    await page.screenshot({ path: `/Users/hose/.codex/visualizations/2026/10/09/01a12072-f58f-7b03-a114-522562084e7b/login-user-success-${width}.png` }); expect(errors).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.waitForTimeout(1100); await expect(page.getByRole('button', { name: '完成', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '完成', exact: true }).click(); await expect(page.getByText('登录已完成', { exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '登录测试入口' })).toBeFocused();
  });
}

test('SMS resend countdown follows server deadline and capability, unknown phase never invents scanned', async ({ page }) => {
  let socket: WebSocketRoute; let starts = 0;
  const sms = { ...snapshot('sms_required'), sms: { fields: ['code'], resend_available_at: new Date(Date.now() + 2000).toISOString() } };
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { starts++; return route.fulfill({ json: { item: sms } }); });
  await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'ticket', expires_in: 30 } }));
  await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => { socket = ws; ws.onMessage(data => { if (JSON.parse(String(data)).type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: sms })); }); });
  await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByRole('button', { name: /秒后可重新发送/ })).toBeDisabled(); await expect(page.getByRole('button', { name: '发送验证码', exact: true })).toBeEnabled({ timeout: 5000 });
  socket!.send(JSON.stringify({ type: 'snapshot', item: { ...snapshot('unclassified', 2), qr: null } })); await expect(page.getByText('正在等待抖音返回登录结果', { exact: true })).toBeVisible(); await expect(page.getByText('请在手机上确认登录', { exact: true })).toHaveCount(0);
  socket!.send(JSON.stringify({ type: 'snapshot', item: { ...snapshot('failed', 3), status: 'failed', qr: null, error: { message: '抖音暂时没有响应，请重新尝试' } } })); await expect(page.getByText('未完成登录', { exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '重新扫码' })).toBeVisible(); expect(starts).toBe(1);
});

test('short screen keyboard stays in login dialog and scanned wait is truthful', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 480 }); let socket: WebSocketRoute;
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => route.fulfill({ json: { item: snapshot() } }));
  await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'ticket', expires_in: 30 } }));
  await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => { socket = ws; ws.onMessage(data => { if (JSON.parse(String(data)).type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: snapshot() })); }); });
  await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByRole('button', { name: '刷新二维码' })).toBeEnabled();
  await page.getByRole('button', { name: '关闭登录窗口' }).focus(); await page.keyboard.press('Shift+Tab'); await expect(page.getByRole('button', { name: '查看抖音提示' })).toBeFocused(); await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: '关闭登录窗口' })).toBeFocused();
  socket!.send(JSON.stringify({ type: 'snapshot', item: { ...snapshot('phone_confirm', 2), status: 'scanned', qr: null } })); await expect(page.getByText('请在手机上确认登录', { exact: true })).toBeVisible();
  const dialog = (await page.getByRole('dialog').boundingBox())!; expect(dialog.height).toBeLessThanOrEqual(480);
  await page.screenshot({ path: '/Users/hose/.codex/visualizations/2026/10/09/01a12072-f58f-7b03-a114-522562084e7b/login-user-phone-short.png' });
});

test('delayed start automatically focuses dialog and keyboard cannot leave it', async ({ page }) => {
  let releaseStart: (() => void) | undefined;
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', async route => { await new Promise<void>(resolve => { releaseStart = resolve; }); await route.fulfill({ json: { item: snapshot() } }); });
  await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'ticket', expires_in: 30 } }));
  await page.route('**/api/crawler-account-login-sessions/login-fixture', route => route.fulfill({ json: { item: { ...snapshot('cancelled'), status: 'cancelled', cleanup_state: 'complete' } } }));
  await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => ws.onMessage(data => { if (JSON.parse(String(data)).type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: snapshot() })); }));
  await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByRole('dialog')).toBeFocused(); await page.keyboard.press('Tab'); await expect(page.getByRole('dialog')).toBeFocused();
  releaseStart!(); await expect(page.getByRole('button', { name: '刷新二维码' })).toBeEnabled(); await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: '关闭登录窗口' })).toBeFocused();
  await page.keyboard.press('Shift+Tab'); await expect(page.getByRole('button', { name: '查看抖音提示' })).toBeFocused(); await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: '关闭登录窗口' })).toBeFocused();
});

test('save failure checks recoverable operation and continues without reauth or clearing login', async ({ page }) => {
  const failed = { ...snapshot('failed'), status: 'failed', qr: null, error: { code: 'SAVE_FAILED', message: '登录信息暂未保存，请继续完成登录' } };
  const requests: any[] = []; let cancels = 0;
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { requests.push(route.request().postDataJSON()); return route.fulfill({ json: { item: requests.length === 1 ? failed : { ...snapshot('done'), status: 'success', identity: { status: 'verified' }, result: { auth_saved: true, identity_verified: true, reuse_verified: true, platform_account_id: 'real-manager-result-id', account_eligibility: 'ready' } } } }); });
  await page.route('**/api/crawler-accounts/account-fixture/login-operations/operation-fixture', route => route.fulfill({ json: { item: { id: 'operation-fixture', stage: 'recovery_required' } } }));
  await page.route('**/api/crawler-account-login-sessions/login-fixture', route => { cancels++; return route.fulfill({ json: { item: { ...failed, cleanup_state: 'complete' } } }); });
  await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByRole('button', { name: '继续完成登录' })).toBeVisible(); await expect(page.getByRole('button', { name: '重新扫码' })).toHaveCount(0);
  await page.getByRole('button', { name: '继续完成登录' }).click(); await expect(page.getByText('real-manager-result-id', { exact: true })).toBeVisible();
  expect(requests[1]).toMatchObject({ intent: 'recover', source_operation_id: 'operation-fixture' }); expect(requests.some(request => request.intent === 'reauth')).toBeFalsy(); expect(cancels).toBe(1);
});

test('save recovery authorization failure does not offer reset or unverified recovery', async ({ page }) => {
  const failed = { ...snapshot('failed'), status: 'failed', qr: null, error: { code: 'SAVE_FAILED', message: '正在确认登录信息' } }; let starts = 0;
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { starts++; return route.fulfill({ json: { item: failed } }); });
  await page.route('**/api/crawler-accounts/account-fixture/login-operations/operation-fixture', route => route.fulfill({ status: 403, json: { detail: { code: 'FORBIDDEN', message: '无权查看该登录结果' } } }));
  await page.goto('/tests/crawlerLoginV2.html'); await expect(page.getByText('无权查看该登录结果', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '继续完成登录' })).toHaveCount(0); await expect(page.getByRole('button', { name: '重新扫码' })).toHaveCount(0); expect(starts).toBe(1);
});

for (const cleanupFails of [false, true]) {
  test(`saved login recovery waits for cleanup and ${cleanupFails ? 'keeps retry on failure' : 'then continues'}`, async ({ page }) => {
    const failed = { ...snapshot('failed'), status: 'failed', qr: null, error: { code: 'SAVE_FAILED', message: '请继续完成登录' } }; let starts = 0; let release: (() => void) | undefined;
    await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { starts++; return route.fulfill({ json: { item: starts === 1 ? failed : { ...snapshot('done'), status: 'success' } } }); });
    await page.route('**/api/crawler-accounts/account-fixture/login-operations/operation-fixture', route => route.fulfill({ json: { item: { stage: 'recovery_required' } } }));
    await page.route('**/api/crawler-account-login-sessions/login-fixture', async route => { await new Promise<void>(resolve => { release = resolve; }); return route.fulfill(cleanupFails ? { status: 503, json: { detail: { code: 'CLEANUP_TIMEOUT', message: '上次登录尚未结束，请稍后继续' } } } : { json: { item: { ...failed, cleanup_state: 'complete' } } }); });
    await page.goto('/tests/crawlerLoginV2.html'); await page.getByRole('button', { name: '继续完成登录' }).click(); await expect(page.getByRole('button', { name: '继续完成登录' })).toBeDisabled(); expect(starts).toBe(1); release!();
    if (cleanupFails) { await expect(page.getByText('上次登录尚未结束，请稍后继续', { exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '继续完成登录' })).toBeEnabled(); expect(starts).toBe(1); }
    else { await expect(page.getByText('登录成功', { exact: true })).toBeVisible(); expect(starts).toBe(2); }
  });
}

test('view platform hint and return to scan operate on same session without restarting login', async ({ page }) => {
  let starts = 0; const commands: any[] = [];
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => { starts++; return route.fulfill({ json: { item: snapshot() } }); });
  await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'ticket', expires_in: 30 } }));
  await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => ws.onMessage(data => {
    const command = JSON.parse(String(data));
    if (command.type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: snapshot() }));
    else { commands.push(command); ws.send(JSON.stringify({ type: 'snapshot', item: command.action === 'open_manual' ? { ...snapshot('manual_required', 2), available_actions: ['close_manual'], challenge: { ...snapshot('challenge_required').challenge } } : snapshot() })); }
  }));
  await page.goto('/tests/crawlerLoginV2.html'); await page.getByRole('button', { name: '查看抖音提示' }).click();
  await expect(page.getByRole('button', { name: '返回扫码' })).toBeVisible(); await page.getByRole('button', { name: '返回扫码' }).click(); await expect(page.getByAltText('抖音登录二维码')).toBeVisible();
  expect(starts).toBe(1); expect(commands.map(command => command.action)).toEqual(['open_manual', 'close_manual']); expect(commands.every(command => command.operation_id === 'operation-fixture' && command.controller_epoch === 1)).toBeTruthy(); expect(commands[1].sequence).toBeGreaterThan(commands[0].sequence);
});
