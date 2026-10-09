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
  expires_at: "2099-01-01T00:00:00Z",
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
    await expect(page.getByText("已连接", { exact: true })).toBeVisible();
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
    await expect(page.getByRole("button", { name: "查看当前登录步骤" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "完成", exact: true })).toHaveClass(/mt-button-primary/);
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
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
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
    page.getByText("登录身份已变化，请重新打开", { exact: true }),
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

test('expired QR without an image can refresh, and rejected command shows safe server message', async ({ page }) => {
  const expired = { ...snapshot('qr_expired'), qr: null };
  let socket: WebSocketRoute;
  await page.route('**/api/crawler-accounts/account-fixture/login-sessions', route => route.fulfill({ json: { item: expired } }));
  await page.route('**/api/crawler-account-login-sessions/login-fixture/ws-tickets', route => route.fulfill({ json: { ticket: 'fixture-ticket', expires_in: 30 } }));
  await page.routeWebSocket('**/api/crawler-account-login-sessions/login-fixture/stream', ws => {
    socket = ws;
    ws.onMessage(data => {
      const command = JSON.parse(String(data));
      if (command.type === 'auth') ws.send(JSON.stringify({ type: 'snapshot', item: expired }));
      else if (command.action === 'refresh_qr') ws.send(JSON.stringify({ type: 'snapshot', item: snapshot() }));
      else if (command.action === 'submit_sms') ws.send(JSON.stringify({ type: 'command_result', command_id: command.command_id, execution_status: 'rejected', platform_status: 'rejected', error_code: 'SOME_INTERNAL_CODE', error_message: '验证码不正确，请重新输入' }));
    });
  });
  await page.goto('/tests/crawlerLoginV2.html');
  await expect(page.getByText('二维码已过期', { exact: true })).toBeVisible(); await expect(page.getByAltText('抖音登录二维码')).toHaveCount(0);
  await page.getByRole('button', { name: '刷新二维码' }).click(); await expect(page.getByAltText('抖音登录二维码')).toBeVisible();
  socket!.send(JSON.stringify({ type: 'snapshot', item: snapshot('sms_required', 2) }));
  await page.getByLabel('短信验证码').fill('123456'); await page.getByRole('button', { name: '提交验证码' }).click();
  await expect(page.getByRole('alert')).toHaveText('验证码不正确，请重新输入'); await expect(page.getByText('SOME_INTERNAL_CODE')).toHaveCount(0);
});
