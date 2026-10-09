import { useEffect, useRef, useState } from "react";
import { apiRequest } from "../../services/apiClient";
import { loginControlHeaders } from "../../services/crawlerAccounts";
import {
  createLoginSocket,
  decodeLoginFrame,
} from "../../services/crawlerLoginV2";
import type { LoginSnapshot, LoginFrame } from "../../services/crawlerLoginV2";
import { CrawlerLoginChallengeCanvas } from "./CrawlerLoginChallengeCanvas";
const phases: Record<string, string> = {
  preparing: "正在获取二维码",
  qr_ready: "请使用抖音扫码",
  qr_expired: "二维码已过期",
  phone_confirm: "请在手机上确认登录",
  sms_required: "请完成短信验证",
  challenge_required: "请完成人工验证",
  manual_required: "请完成当前登录步骤",
  identity_check: "正在核对账号身份",
  profile_flush: "正在保存浏览器登录状态",
  reuse_check: "正在检查登录状态能否接续使用",
  db_commit: "正在保存登录信息",
  done: "登录信息已保存",
};
export function CrawlerLoginV2View({
  initial,
  onSnapshot,
}: {
  initial: LoginSnapshot;
  onSnapshot: (snapshot: LoginSnapshot) => void;
}) {
  const [snapshot, setSnapshot] = useState(initial);
  const [connection, setConnection] = useState("正在连接");
  const [error, setError] = useState("");
  const [frame, setFrame] = useState<LoginFrame | null>(null);
  const pendingStorageKey = `crawler-account-login-command:${initial.id}`;
  const [pending, setPending] = useState(
    () => window.sessionStorage.getItem(pendingStorageKey) || "",
  );
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [manualText, setManualText] = useState("");
  const socket = useRef<WebSocket | null>(null);
  const current = useRef(snapshot);
  const sequence = useRef(0);
  const callback = useRef(onSnapshot);
  const pendingRef = useRef(pending);
  current.current = snapshot;
  callback.current = onSnapshot;
  useEffect(() => {
    let disposed = false;
    let retry = 0;
    let needsResultQuery = Boolean(pendingRef.current);
    let timer = 0;
    const clear = () => {
      disposed = true;
      document.removeEventListener("visibilitychange", visibility);
      window.clearTimeout(timer);
      socket.current?.close();
      setPhone("");
      setCode("");
      setManualText("");
      setFrame(null);
      setSnapshot((previous) => ({
        ...previous,
        qr: null,
        challenge: null,
        controller_state: "detached",
      }));
      setConnection("登录身份已变化，请重新打开");
    };
    const connect = async () => {
      if (
        disposed ||
        document.hidden ||
        ["success", "failed", "expired", "cancelled"].includes(
          current.current.status,
        )
      )
        return;
      try {
        const created = await createLoginSocket(initial.id);
        if (disposed || document.hidden) {
          created.socket.close();
          return;
        }
        socket.current = created.socket;
        created.socket.onopen = () =>
          created.socket.send(
            JSON.stringify({
              type: "auth",
              protocol_version: 2,
              ticket: created.ticket,
            }),
          );
        created.socket.onmessage = (event) => {
          if (disposed) return;
          if (event.data instanceof ArrayBuffer) {
            try {
              const next = decodeLoginFrame(event.data);
              const state = current.current;
              if (
                state.challenge &&
                next.area_version === state.challenge.area_version &&
                (!next.metadata.challenge_id ||
                  next.metadata.challenge_id ===
                    state.challenge.challenge_id) &&
                next.metadata.controller_epoch === state.controller_epoch
              )
                setFrame((prev) =>
                  !prev || next.sequence > prev.sequence ? next : prev,
                );
            } catch {
              setError("验证画面异常，请重新连接");
            }
            return;
          }
          const fieldLimit = (name: string, fallback: number) => {
            const field = snapshot.sms?.fields?.find(
              (field) => typeof field !== "string" && field.name === name,
            );
            return typeof field === "object" && field.max_length
              ? Math.min(fallback, field.max_length)
              : fallback;
          };
          const message = JSON.parse(event.data);
          if (message.type === "snapshot" || message.type === "state_changed") {
            const next = message.item as LoginSnapshot;
            if (next.id !== initial.id) return;
            if (
              next.controller_epoch !== current.current.controller_epoch ||
              next.challenge?.area_version !==
                current.current.challenge?.area_version
            )
              setFrame(null);
            current.current = next;
            setSnapshot(next);
            callback.current(next);
            retry = 0;
            setConnection(
              next.controller_state === "attached" ? "已连接" : "正在连接",
            );
            // Commands are never replayed after reconnect; uncertain results are queried below.
            if (
              pendingRef.current &&
              needsResultQuery &&
              next.controller_state === "attached"
            )
              void apiRequest<{ item: { platform_status?: string } }>(
                `/api/crawler-accounts/${next.account_id}/login-operations/${next.operation_id}/commands/${pendingRef.current}`,
                { headers: loginControlHeaders() },
              )
                .then((result) => {
                  needsResultQuery = false;
                  if (result.item.platform_status === "confirmed") {
                    setPending("");
                    pendingRef.current = "";
                    window.sessionStorage.removeItem(pendingStorageKey);
                  } else
                    setError(
                      "上次操作结果尚未确认，请勿重复提交，可查看当前登录步骤",
                    );
                })
                .catch(() => setError("上次操作结果尚未确认，请勿重复提交"));
          } else if (message.type === "command_result") {
            if (message.command_id === pendingRef.current) {
              if (
                message.platform_status === "unknown" ||
                message.platform_status === "pending"
              )
                setError("正在确认平台处理结果，请勿重复提交");
              else {
                setPending("");
                pendingRef.current = "";
                window.sessionStorage.removeItem(pendingStorageKey);
                if (
                  ["failed", "rejected"].includes(message.execution_status) ||
                  message.platform_status === "rejected"
                )
                  setError("平台未接受本次操作，请检查输入和当前步骤后重试");
              }
            }
          } else if (message.type === "error") {
            setError(
              message.message ||
                message.error?.message ||
                "操作未完成，请根据当前步骤重试",
            );
            if (
              message.command_id === pendingRef.current &&
              message.execution_status === "rejected"
            ) {
              setPending("");
              pendingRef.current = "";
              window.sessionStorage.removeItem(pendingStorageKey);
            }
          } else if (message.type === "ping")
            created.socket.send(JSON.stringify({ type: "pong" }));
        };
        created.socket.onclose = (event) => {
          setFrame(null);
          needsResultQuery = Boolean(pendingRef.current);
          if (disposed) return;
          setConnection("连接已断开");
          if ([4401, 4403, 4409, 4410, 1000].includes(event.code)) {
            setError(
              event.code === 4409
                ? "登录控制已转移到其他窗口"
                : event.code === 4410 || event.code === 1000
                  ? ""
                  : "登录权限已失效，请重新打开",
            );
            return;
          }
          timer = window.setTimeout(
            connect,
            Math.min(5000, 500 * 2 ** retry++) + Math.random() * 200,
          );
        };
        created.socket.onerror = () => setConnection("正在恢复连接");
      } catch (failure) {
        if (!disposed) {
          setError(failure instanceof Error ? failure.message : "连接失败");
          timer = window.setTimeout(
            connect,
            Math.min(5000, 500 * 2 ** retry++),
          );
        }
      }
    };
    const visibility = () => {
      if (document.hidden) {
        window.clearTimeout(timer);
        socket.current?.close(1000, "page hidden");
        setFrame(null);
      } else if (
        !disposed &&
        (!socket.current || socket.current.readyState === WebSocket.CLOSED)
      )
        void connect();
    };
    document.addEventListener("visibilitychange", visibility);
    void connect();
    window.addEventListener("application-auth-cleared", clear);
    window.addEventListener("application-auth-expired", clear);
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", visibility);
      window.clearTimeout(timer);
      socket.current?.close();
      window.removeEventListener("application-auth-cleared", clear);
      window.removeEventListener("application-auth-expired", clear);
    };
  }, [initial.id]);
  const enabled =
    connection === "已连接" &&
    snapshot.controller_state === "attached" &&
    !["success", "failed", "expired", "cancelled", "finalizing"].includes(
      snapshot.status,
    );
  const send = (input: Record<string, unknown>) => {
    if (!enabled || socket.current?.readyState !== WebSocket.OPEN) return;
    const commandId = crypto.randomUUID();
    socket.current.send(
      JSON.stringify({
        ...input,
        command_id: commandId,
        operation_id: snapshot.operation_id,
        controller_epoch: snapshot.controller_epoch,
        sequence: ++sequence.current,
        expected_state_revision: snapshot.state_revision,
      }),
    );
    if (
      input.type === "action" &&
      ["request_sms", "submit_sms"].includes(String(input.action))
    ) {
      pendingRef.current = commandId;
      window.sessionStorage.setItem(pendingStorageKey, commandId);
      setPending(commandId);
      setError("");
    }
  };
  const action = (name: string, payload = {}) =>
    send({
      type: "action",
      action: name,
      payload,
      ...(name === "refresh_challenge" && snapshot.challenge
        ? {
            challenge_id: snapshot.challenge.challenge_id,
            area_version: snapshot.challenge.area_version,
          }
        : {}),
    });
  const available = (name: string) =>
    enabled && !pending && snapshot.available_actions.includes(name);
  const needsPhone = snapshot.sms?.fields?.some(
    (field) => (typeof field === "string" ? field : field.name) === "phone",
  );
  const fieldLimit = (name: string, fallback: number) => {
    const field = snapshot.sms?.fields?.find(
      (field) => typeof field !== "string" && field.name === name,
    );
    return typeof field === "object" && field.max_length
      ? Math.min(fallback, field.max_length)
      : fallback;
  };
  const message =
    typeof snapshot.error === "string"
      ? snapshot.error
      : snapshot.error?.message;
  return (
    <div className="crawler-login-v2">
      <div className="crawler-login-v2-status" role="status">
        <strong>{phases[snapshot.phase] || "正在确认登录步骤"}</strong>
        <small>{connection}</small>
      </div>
      {snapshot.qr && ["qr_ready", "qr_expired"].includes(snapshot.phase) ? (
        <div className="crawler-login-v2-qr">
          <img
            src={snapshot.qr.image_data_url}
            alt="抖音登录二维码"
            className={snapshot.qr.state === "expired" ? "is-expired" : ""}
          />
          <p>扫码后在手机上确认</p>
          {snapshot.available_actions.includes("refresh_qr") ? (
            <button
              disabled={!available("refresh_qr")}
              onClick={() => action("refresh_qr")}
            >
              刷新二维码
            </button>
          ) : null}
        </div>
      ) : null}
      {snapshot.phase === "sms_required" && snapshot.sms ? (
        <form
          className="crawler-login-v2-sms"
          onSubmit={(event) => {
            event.preventDefault();
            action("submit_sms", { code });
          }}
        >
          {snapshot.sms.masked_phone ? (
            <p>验证手机号：{snapshot.sms.masked_phone}</p>
          ) : null}
          {needsPhone ? (
            <label>
              手机号
              <input
                autoComplete="off"
                inputMode="tel"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                maxLength={fieldLimit("phone", 20)}
                disabled={!enabled}
              />
            </label>
          ) : null}
          <label>
            短信验证码
            <input
              autoComplete="one-time-code"
              inputMode="numeric"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              maxLength={fieldLimit("code", 12)}
              disabled={!enabled}
            />
          </label>
          <div>
            <button
              type="button"
              disabled={
                !available("request_sms") ||
                Boolean(
                  snapshot.sms.resend_available_at &&
                  Date.parse(snapshot.sms.resend_available_at) > Date.now(),
                )
              }
              onClick={() => action("request_sms", phone ? { phone } : {})}
            >
              发送验证码
            </button>
            <button type="submit" disabled={!available("submit_sms") || !code}>
              提交验证码
            </button>
          </div>
          {snapshot.sms.error ? <p role="alert">{snapshot.sms.error}</p> : null}
        </form>
      ) : null}
      {snapshot.challenge &&
      ["challenge_required", "manual_required"].includes(snapshot.phase) ? (
        <>
          <CrawlerLoginChallengeCanvas
            area={snapshot.challenge}
            frame={frame}
            enabled={
              enabled &&
              Boolean(frame) &&
              frame?.area_version === snapshot.challenge.area_version
            }
            send={send}
          />
          {snapshot.challenge.allowed_actions.includes("text") ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                send({
                  type: "text",
                  text: manualText,
                  challenge_id: snapshot.challenge!.challenge_id,
                  area_version: snapshot.challenge!.area_version,
                });
                setManualText("");
              }}
            >
              <label>
                填写当前选中的输入框
                <input
                  value={manualText}
                  maxLength={256}
                  onChange={(event) => setManualText(event.target.value)}
                  disabled={!enabled}
                />
              </label>
              <button disabled={!enabled || !manualText}>输入</button>
            </form>
          ) : null}
        </>
      ) : null}
      {snapshot.status === "success" ? (
        <p>
          登录信息已保存
          {snapshot.result?.account_eligibility === "cooling_down"
            ? "，账号仍处于冷却中"
            : snapshot.result?.account_eligibility === "disabled"
              ? "，账号仍为停用状态"
              : ""}
          。
        </p>
      ) : null}
      {pending ? <p role="status">正在等待平台处理结果，请勿重复提交</p> : null}
      {error || message ? (
        <p role="alert" className="crawler-login-v2-error">
          {error || message}
        </p>
      ) : null}
      {snapshot.available_actions.includes("switch_to_sms") ? (
        <button
          disabled={!available("switch_to_sms")}
          onClick={() => action("switch_to_sms")}
        >
          使用短信登录
        </button>
      ) : null}
      {snapshot.available_actions.includes("refresh_challenge") ? (
        <button
          disabled={!available("refresh_challenge")}
          onClick={() => action("refresh_challenge")}
        >
          刷新验证题目
        </button>
      ) : null}
      {snapshot.available_actions.includes("close_manual") ? (
        <button disabled={!enabled} onClick={() => action("close_manual")}>
          返回扫码
        </button>
      ) : null}
      {snapshot.available_actions.includes("open_manual") ? (
        <button disabled={!enabled} onClick={() => action("open_manual")}>
          查看当前登录步骤
        </button>
      ) : null}
    </div>
  );
}
