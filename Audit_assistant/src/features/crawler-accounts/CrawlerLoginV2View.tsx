import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Smartphone, AlertCircle } from "lucide-react";
import { Button } from "../../components/common/Button";
import { apiRequest } from "../../services/apiClient";
import { loginControlHeaders } from "../../services/crawlerAccounts";
import {
  createLoginSocket,
  decodeLoginFrame,
} from "../../services/crawlerLoginV2";
import type { LoginSnapshot, LoginFrame } from "../../services/crawlerLoginV2";
import { CrawlerLoginChallengeCanvas } from "./CrawlerLoginChallengeCanvas";
const phases: Record<string, string> = {
  checking: "正在检查登录状态", preparing: "正在准备登录",
  qr_ready: "打开抖音扫一扫", qr_expired: "二维码已过期",
  phone_confirm: "请在手机上确认登录", sms_required: "完成短信验证",
  challenge_required: "完成安全验证", manual_required: "完成安全验证",
  identity_check: "正在完成登录，请稍候", profile_flush: "正在完成登录，请稍候",
  reuse_check: "正在完成登录，请稍候", db_commit: "正在完成登录，请稍候",
  done: "登录成功", failed: "未完成登录", expired: "本次登录已到期", cancelled: "本次登录已结束",
};
type CommandOutcome = {
  platform_status?: string;
  execution_status?: string;
  error_message?: string;
};
function commandHasDefinitiveOutcome(result: CommandOutcome): boolean {
  return (
    ["confirmed", "rejected"].includes(result.platform_status || "") ||
    (result.platform_status === "not_applicable" &&
      ["not_executed", "rejected"].includes(result.execution_status || ""))
  );
}

export function CrawlerLoginV2View({
  initial,
  onSnapshot,
}: {
  initial: LoginSnapshot;
  onSnapshot: (snapshot: LoginSnapshot) => void;
}) {
  const [snapshot, setSnapshot] = useState(initial);
  const [now, setNow] = useState(Date.now());
  const phaseStarted = useRef(Date.now());
  useEffect(() => { phaseStarted.current = Date.now(); }, [snapshot.phase]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const [connection, setConnection] = useState("正在连接");
  const [error, setError] = useState("");
  const [gestureReset, setGestureReset] = useState(0);
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
      setError("应用登录已结束，请重新登录后打开此窗口");
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
            ) {
              const queriedCommandId = pendingRef.current;
              void apiRequest<{ item: CommandOutcome }>(
                `/api/crawler-accounts/${next.account_id}/login-operations/${next.operation_id}/commands/${pendingRef.current}`,
                { headers: loginControlHeaders() },
              )
                .then((result) => {
                  if (pendingRef.current !== queriedCommandId) return;
                  needsResultQuery = false;
                  if (result.item.platform_status === "confirmed") {
                    setPending("");
                    pendingRef.current = "";
                    window.sessionStorage.removeItem(pendingStorageKey);
                  } else if (commandHasDefinitiveOutcome(result.item)) {
                    setPending("");
                    pendingRef.current = "";
                    window.sessionStorage.removeItem(pendingStorageKey);
                    setError(
                      result.item.error_message ||
                        "操作未完成，请查看当前登录步骤后重试",
                    );
                  } else
                    setError(
                      "上次操作结果尚未确认，请勿重复提交，可查看当前登录步骤",
                    );
                })
                .catch(() => setError("上次操作结果尚未确认，请勿重复提交"));
            }
          } else if (message.type === "command_result") {
            if (
              ["failed", "rejected", "not_executed"].includes(
                message.execution_status,
              ) &&
              message.error_message
            )
              setError(message.error_message);
            if (message.command_id === pendingRef.current) {
              if (!commandHasDefinitiveOutcome(message))
                setError("正在确认平台处理结果，请勿重复提交");
              else {
                setPending("");
                pendingRef.current = "";
                window.sessionStorage.removeItem(pendingStorageKey);
                if (
                  ["not_executed", "failed", "rejected"].includes(
                    message.execution_status,
                  ) ||
                  message.platform_status === "rejected"
                )
                  setError(
                    message.error_message ||
                      "操作未完成，请查看当前登录步骤后重试",
                  );
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
              commandHasDefinitiveOutcome(message)
            ) {
              setPending("");
              pendingRef.current = "";
              window.sessionStorage.removeItem(pendingStorageKey);
            }
          } else if (message.type === "notice") {
            if (
              ["QUEUE", "GESTURE_EXPIRED", "INPUT_BACKPRESSURE"].includes(
                message.code,
              )
            )
              setGestureReset((value) => value + 1);
            setError(
              message.message || "验证操作未完成，请根据当前画面重新操作",
            );
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
    if (socket.current.bufferedAmount > 64 * 1024) {
      setError("连接发送积压，本次操作已停止，恢复连接后请重新操作");
      socket.current.close(1011, "input congestion");
      return;
    }
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
  const terminal = ["success", "failed", "expired", "cancelled"].includes(
    snapshot.status,
  );
  const verifiedAccountId = snapshot.result?.identity_verified ? snapshot.result.platform_account_id || (snapshot.identity?.status === "verified" ? snapshot.identity.platform_account_id : "") : "";
  const displayPhase = snapshot.status === "scanned" ? "phone_confirm" : snapshot.status === "success" ? "done" : ["failed", "expired", "cancelled"].includes(snapshot.status) ? snapshot.status : snapshot.phase;
  const waiting = !["qr_ready", "qr_expired", "sms_required", "challenge_required", "manual_required", "done", "failed", "expired", "cancelled"].includes(displayPhase);
  const resendSeconds = snapshot.sms?.resend_available_at ? Math.max(0, Math.ceil((Date.parse(snapshot.sms.resend_available_at) - now) / 1000)) : 0;
  const sessionSeconds = Math.max(0, Math.ceil((Date.parse(snapshot.expires_at) - now) / 1000));
  const message =
    typeof snapshot.error === "string"
      ? snapshot.error
      : snapshot.error?.message;
  return (
    <div className="crawler-login-v2">
      <div className="crawler-login-v2-status" role="status">
        {displayPhase === "done" ? <CheckCircle2 size={36} className="crawler-login-v2-success-icon" aria-hidden="true" /> : displayPhase === "phone_confirm" ? <Smartphone size={36} aria-hidden="true" /> : terminal ? <AlertCircle size={32} aria-hidden="true" /> : waiting ? <Loader2 size={30} className="spin" aria-hidden="true" /> : null}
        <strong>{phases[displayPhase] || "正在等待抖音返回登录结果"}</strong>
        {displayPhase === "phone_confirm" ? <p>请打开手机上的抖音，点击确认登录。</p> : ["checking", "preparing"].includes(displayPhase) ? <p>二维码准备好后会显示在这里。</p> : waiting ? <p>请稍候，登录结果确认后会自动更新。</p> : null}
        {waiting && now - phaseStarted.current >= 15000 ? <p className="crawler-login-v2-slow">{displayPhase === "phone_confirm" ? "还没收到抖音确认，请检查手机提示，无需重复扫码。" : "抖音暂未返回新的登录结果，仍在等待。您也可以关闭窗口结束本次登录。"}</p> : null}
        {!terminal && !["正在连接", "已连接", "登录身份已变化，请重新打开"].includes(connection) ? <p>正在恢复登录画面，暂时无法操作。</p> : null}
      </div>
      {["qr_ready", "qr_expired"].includes(snapshot.phase) &&
      (snapshot.qr || snapshot.phase === "qr_expired") ? (
        <div className="crawler-login-v2-qr">
          {snapshot.qr?.image_data_url ? (
            <img
              src={snapshot.qr.image_data_url}
              alt="抖音登录二维码"
              className={snapshot.qr.state === "expired" ? "is-expired" : ""}
            />
          ) : null}
          <p>
            {snapshot.phase === "qr_expired"
              ? "请刷新二维码后重新扫码"
              : "扫码后在手机上确认"}
          </p>
          {!terminal && snapshot.available_actions.includes("refresh_qr") ? (
            <Button
              disabled={!available("refresh_qr")}
              onClick={() => action("refresh_qr")}
            >
              刷新二维码
            </Button>
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
            <Button
              type="button"
              disabled={
                !available("request_sms") ||
                Boolean(
                  snapshot.sms.resend_available_at &&
                  resendSeconds > 0,
                )
              }
              onClick={() => action("request_sms", phone ? { phone } : {})}
            >
              {resendSeconds > 0 ? `${resendSeconds}秒后可重新发送` : "发送验证码"}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={!available("submit_sms") || !code}
            >
              提交验证码
            </Button>
          </div>
          {snapshot.sms.error ? <p role="alert">{snapshot.sms.error}</p> : null}
        </form>
      ) : null}
      {snapshot.challenge &&
      ["challenge_required", "manual_required"].includes(displayPhase) ? (
        <>
          <CrawlerLoginChallengeCanvas
            key={`${snapshot.challenge.challenge_id}:${gestureReset}`}
            area={snapshot.challenge}
            frame={frame}
            enabled={
              enabled &&
              Boolean(frame) &&
              frame?.area_version === snapshot.challenge.area_version
            }
            send={send}
            onError={setError}
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
              <Button disabled={!enabled || !manualText}>输入</Button>
            </form>
          ) : null}
        </>
      ) : null}
      {snapshot.status === "success" && verifiedAccountId ? <div className="crawler-login-v2-identity"><span>已核对抖音账号</span><strong>{verifiedAccountId}</strong></div> : null}
      {snapshot.status === "success" ? (
        <p>
          {snapshot.result?.account_eligibility === "cooling_down"
            ? "账号仍处于冷却中，登录不会解除冷却"
            : snapshot.result?.account_eligibility === "disabled"
              ? "账号仍为停用状态"
              : "现在可以使用此账号"}
          。
        </p>
      ) : null}
      {pending && !terminal ? (
        <p role="status">正在等待平台处理结果，请勿重复提交</p>
      ) : null}
      {error || message ? (
        <p role="alert" className="crawler-login-v2-error">
          {error || message}
        </p>
      ) : null}
      {!terminal && snapshot.available_actions.includes("switch_to_sms") ? (
        <Button
          disabled={!available("switch_to_sms")}
          onClick={() => action("switch_to_sms")}
        >
          使用短信登录
        </Button>
      ) : null}
      {!terminal && snapshot.available_actions.includes("refresh_challenge") ? (
        <Button
          disabled={!available("refresh_challenge")}
          onClick={() => action("refresh_challenge")}
        >
          刷新验证题目
        </Button>
      ) : null}
      {!terminal && ["qr_ready", "qr_expired"].includes(displayPhase) && snapshot.available_actions.includes("open_manual") ? <div className="crawler-login-v2-platform-help"><p>手机确认后仍未完成？查看抖音当前提示。</p><Button disabled={!enabled} onClick={() => action("open_manual")}>查看抖音提示</Button></div> : null}
      {!terminal && displayPhase === "manual_required" && snapshot.available_actions.includes("close_manual") ? <Button disabled={!enabled} onClick={() => action("close_manual")}>返回扫码</Button> : null}
      {!terminal && Number.isFinite(sessionSeconds) && sessionSeconds > 0 ? <p className="crawler-login-v2-session-time">本次登录剩余 {Math.floor(sessionSeconds / 60)}:{String(sessionSeconds % 60).padStart(2, "0")}</p> : null}
    </div>
  );
}
