import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, QrCode, RefreshCw, ShieldCheck, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import {
  cancelCrawlerAccountLoginSession,
  fetchCrawlerAccountLoginSession,
  startCrawlerAccountLogin
} from "../../services/crawlerAccounts";
import type { CrawlerAccount, CrawlerAccountLoginSession } from "../../types/crawlerAccounts";

const platformNames = {
  xhs: "小红书",
  dy: "抖音",
  ks: "快手"
} as const;

const activeLoginStatuses = ["starting", "waiting_scan", "scanned", "finalizing"];

interface CrawlerAccountLoginDialogProps {
  account: CrawlerAccount | null;
  onClose: () => void;
  onSuccess: () => void;
}

export function CrawlerAccountLoginDialog({ account, onClose, onSuccess }: CrawlerAccountLoginDialogProps) {
  const [session, setSession] = useState<CrawlerAccountLoginSession | null>(null);
  const [startError, setStartError] = useState("");
  const [starting, setStarting] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const requestedAccountRef = useRef("");
  const successHandledRef = useRef("");

  const beginLogin = useCallback(async () => {
    if (!account) return;
    requestedAccountRef.current = account.id;
    successHandledRef.current = "";
    setStarting(true);
    setStartError("");
    setSession(null);
    try {
      setSession(await startCrawlerAccountLogin(account.id));
    } catch (error) {
      setStartError(readApiError(error));
    } finally {
      setStarting(false);
    }
  }, [account]);

  useEffect(() => {
    if (!account) {
      requestedAccountRef.current = "";
      setSession(null);
      setStartError("");
      return;
    }
    if (requestedAccountRef.current !== account.id) {
      void beginLogin();
    }
  }, [account, beginLogin]);

  useEffect(() => {
    if (!session || !activeLoginStatuses.includes(session.status)) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await fetchCrawlerAccountLoginSession(session.id);
        if (stopped) return;
        setSession(next);
        if (activeLoginStatuses.includes(next.status)) {
          timer = window.setTimeout(poll, 1200);
        }
      } catch (error) {
        if (!stopped) setStartError(readApiError(error));
      }
    };
    timer = window.setTimeout(poll, 900);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [session?.id, session?.status]);

  useEffect(() => {
    if (!session?.expiresAt || !["starting", "waiting_scan", "scanned"].includes(session.status)) {
      setSecondsLeft(0);
      return;
    }
    const update = () => {
      setSecondsLeft(Math.max(0, Math.ceil((new Date(session.expiresAt).getTime() - Date.now()) / 1000)));
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [session?.expiresAt, session?.status]);

  useEffect(() => {
    if (session?.status !== "success" || successHandledRef.current === session.id) return;
    successHandledRef.current = session.id;
    const timer = window.setTimeout(onSuccess, 900);
    return () => window.clearTimeout(timer);
  }, [onSuccess, session?.id, session?.status]);

  if (!account) return null;

  const platformName = platformNames[account.platform];
  const isWaiting = session?.status === "waiting_scan";
  const isScanned = session?.status === "scanned";
  const isFinalizing = session?.status === "finalizing";
  const isSuccess = session?.status === "success";
  const isTerminalError = Boolean(
    startError || (session && ["failed", "expired", "cancelled"].includes(session.status))
  );

  const close = () => {
    if (session && activeLoginStatuses.includes(session.status)) {
      void cancelCrawlerAccountLoginSession(session.id).catch(() => undefined);
    }
    onClose();
  };

  const retry = () => {
    if (session && activeLoginStatuses.includes(session.status)) {
      void cancelCrawlerAccountLoginSession(session.id).catch(() => undefined);
    }
    requestedAccountRef.current = "";
    void beginLogin();
  };

  return (
    <div className="crawler-login-backdrop" role="presentation">
      <section className="crawler-login-dialog" role="dialog" aria-modal="true" aria-labelledby="crawler-login-title">
        <header className="crawler-login-header">
          <div>
            <span className="crawler-login-title-icon" aria-hidden="true">
              <QrCode size={20} />
            </span>
            <span>
              <small>{platformName}</small>
              <h2 id="crawler-login-title">登录 {account.displayName}</h2>
            </span>
          </div>
          <IconButton type="button" aria-label="关闭登录窗口" title="关闭" onClick={close}>
            <X size={18} />
          </IconButton>
        </header>

        <div className="crawler-login-body">
          {!isTerminalError && (starting || session?.status === "starting") ? (
            <div className="crawler-login-state">
              <Loader2 className="spin" size={34} />
              <strong>正在准备二维码</strong>
              <span>正在连接 {platformName}</span>
            </div>
          ) : null}

          {!isTerminalError && isWaiting && session.qrImageDataUrl ? (
            <>
              <div className="crawler-login-qr-frame">
                <img src={session.qrImageDataUrl} alt={`${platformName}登录二维码`} />
              </div>
              <div className="crawler-login-scan-copy">
                <strong>请使用 {platformName} 扫码</strong>
                <span>扫码后在手机上确认登录</span>
              </div>
              <div className="crawler-login-countdown">本次登录会话剩余 {formatCountdown(secondsLeft)}</div>
            </>
          ) : null}

          {!isTerminalError && isFinalizing ? (
            <div className="crawler-login-state crawler-login-finalizing">
              <LoginFinalizingProgress session={session} />
              <strong>登录已确认，正在保存</strong>
              <span>正在等待登录凭证写入完整并加密保存</span>
            </div>
          ) : null}

          {!isTerminalError && isScanned ? (
            <div className="crawler-login-state crawler-login-finalizing">
              <Loader2 className="spin" size={34} />
              <strong>已扫码，请在 {platformName} 确认</strong>
              <span>确认完成后会自动保存登录状态</span>
            </div>
          ) : null}

          {isSuccess ? (
            <div className="crawler-login-state is-success">
              <CheckCircle2 size={42} />
              <strong>登录成功</strong>
              <span>
                {session.platformAccountId
                  ? `已识别账号 ID：${session.platformAccountId}`
                  : "登录状态已安全保存，暂未从当前页面识别到账号 ID"}
              </span>
            </div>
          ) : null}

          {isTerminalError ? (
            <div className="crawler-login-state is-error">
              <AlertCircle size={38} />
              <strong>未完成登录</strong>
              <span>{startError || session?.error || "登录会话已结束"}</span>
              <Button type="button" variant="primary" onClick={retry}>
                <RefreshCw size={16} />
                重新获取二维码
              </Button>
            </div>
          ) : null}
        </div>

        <footer className="crawler-login-footer">
          <ShieldCheck size={16} aria-hidden="true" />
          登录凭证仅加密保存在服务端
        </footer>
      </section>
    </div>
  );
}

function LoginFinalizingProgress({ session }: { session: CrawlerAccountLoginSession }) {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const durationMs = Math.max(1, session.finalizingDurationSeconds || 3) * 1000;
    const parsedStartedAt = Date.parse(session.finalizingStartedAt);
    const startedAt = Number.isFinite(parsedStartedAt) ? parsedStartedAt : Date.now();
    const update = () => {
      const elapsed = Math.max(0, Date.now() - startedAt);
      setProgress(Math.min(96, Math.max(4, (elapsed / durationMs) * 100)));
    };
    update();
    const timer = window.setInterval(update, 50);
    return () => window.clearInterval(timer);
  }, [session.finalizingDurationSeconds, session.finalizingStartedAt]);

  const roundedProgress = Math.round(progress);
  return (
    <div
      className="crawler-login-progress-ring"
      role="progressbar"
      aria-label="正在保存登录状态"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={roundedProgress}
    >
      <svg viewBox="0 0 72 72" aria-hidden="true">
        <circle className="crawler-login-progress-track" cx="36" cy="36" r="30" pathLength="100" />
        <circle
          className="crawler-login-progress-value"
          cx="36"
          cy="36"
          r="30"
          pathLength="100"
          strokeDasharray="100"
          strokeDashoffset={100 - progress}
        />
      </svg>
      <span>{roundedProgress}%</span>
    </div>
  );
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function readApiError(error: unknown): string {
  const message = error instanceof Error ? error.message : "请求失败";
  try {
    const parsed = JSON.parse(message) as { detail?: string };
    return parsed.detail || message;
  } catch {
    return message;
  }
}
