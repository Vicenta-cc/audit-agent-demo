import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { API_BASE, apiRequest } from "../../services/apiClient";
import { loginControlHeaders } from "../../services/crawlerAccounts";

const WIDTH = 1000;
const HEIGHT = 760;
const keys = new Set(["Enter", "Backspace", "Delete", "Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"]);

export function CrawlerLoginInteractiveView({ sessionId }: { sessionId: string }) {
  const [frame, setFrame] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const dragging = useRef(false);
  const pressedAt = useRef<{ x: number; y: number } | null>(null);
  const lastMove = useRef(0);
  const queue = useRef(Promise.resolve());
  const queued = useRef(0);
  const mounted = useRef(true);
  const base = `/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`;

  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    let timer = 0;
    let sequence = 0;
    let url = "";
    let closed = false;
    async function poll() {
      try {
        const response = await fetch(`${API_BASE()}${base}/frame?after=${sequence}`, {
          headers: loginControlHeaders(), signal: abort.signal, cache: "no-store"
        });
        if (response.status === 410 || response.status === 403) {
          closed = true;
          if (!abort.signal.aborted) setError("登录画面已关闭，请查看登录状态或重新打开窗口");
          return;
        }
        if (!response.ok) throw new Error("暂时无法连接登录画面，正在重连");
        if (response.status === 200) {
          const blob = await response.blob();
          if (abort.signal.aborted) return;
          sequence = Number(response.headers.get("X-Frame-Sequence") || 0);
          const next = URL.createObjectURL(blob);
          setFrame(next);
          if (url) URL.revokeObjectURL(url);
          url = next;
        }
        if (!abort.signal.aborted) setError("");
      } catch {
        if (!abort.signal.aborted) setError("暂时无法连接登录画面，正在重连");
      } finally {
        if (!abort.signal.aborted && !closed) timer = window.setTimeout(poll, 550);
      }
    }
    void poll();
    return () => {
      mounted.current = false;
      abort.abort();
      window.clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
    };
  }, [base]);

  function send(event: Record<string, unknown>) {
    if (queued.current >= 40) {
      setError("操作较快，请稍等画面更新后继续输入");
      return;
    }
    queued.current++;
    queue.current = queue.current.then(async () => {
      if (!mounted.current) return;
      try {
        await apiRequest(`${base}/input`, { method: "POST", headers: loginControlHeaders(), body: JSON.stringify(event) });
        if (event.type === "dismiss_browser_prompt" && mounted.current) setNotice("已发送取消操作，请再试一下页面按钮。");
      } catch (reason) {
        if (mounted.current) setError(reason instanceof Error ? reason.message : "操作未送达，请重试");
      }
    }).finally(() => { queued.current--; });
  }

  function point(event: PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(WIDTH - 1, (event.clientX - box.left) / box.width * WIDTH)),
      y: Math.max(0, Math.min(HEIGHT - 1, (event.clientY - box.top) / box.height * HEIGHT)) };
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || composing.current) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault(); send({ type: "key", key: "Control+a" });
    } else if (keys.has(event.key)) {
      event.preventDefault(); send({ type: "key", key: event.shiftKey && event.key === "Tab" ? "Shift+Tab" : event.key });
    }
  }

  return <div className="crawler-login-interactive-view">
    <div className="crawler-login-prompt-controls">
      <span>页面点不动时，可先取消浏览器提示。</span>
      <button type="button" className="button button-secondary" onClick={() => send({ type: "dismiss_browser_prompt" })}>取消浏览器提示</button>
    </div>
    {notice ? <p role="status" className="crawler-login-input-notice">{notice}</p> : null}
    {error ? <p role="status" className="crawler-login-connection-error">{error}</p> : null}
    <div className="crawler-login-screen" aria-label="平台登录页面" role="group"
      onContextMenu={event => event.preventDefault()}
      onPointerDown={event => {
        if (event.button !== 0 || !frame) return;
        event.preventDefault(); dragging.current = false;
        pressedAt.current = point(event);
        event.currentTarget.setPointerCapture(event.pointerId);
        inputRef.current?.focus({ preventScroll: true });
      }}
      onPointerMove={event => {
        const start = pressedAt.current;
        if (!start) return;
        const next = point(event);
        if (!dragging.current) {
          if (Math.hypot(next.x - start.x, next.y - start.y) < 5) return;
          dragging.current = true;
          send({ type: "pointer_down", ...start });
        }
        if (Date.now() - lastMove.current < 75) return;
        lastMove.current = Date.now(); send({ type: "pointer_move", ...next });
      }}
      onPointerUp={event => {
        if (!pressedAt.current) return;
        send({ type: dragging.current ? "pointer_up" : "click", ...point(event) });
        pressedAt.current = null;
        dragging.current = false;
      }}
      onPointerCancel={event => {
        if (dragging.current) send({ type: "pointer_up", ...point(event) });
        pressedAt.current = null;
        dragging.current = false;
      }}
      onWheel={event => send({ type: "scroll", delta: Math.max(-760, Math.min(760, event.deltaY)) })}>
      {frame ? <img src={frame} alt="可操作的平台登录页面" draggable={false} /> : <span>正在载入登录页面…</span>}
      <textarea ref={inputRef} className="crawler-login-keyboard" aria-label="向登录页面输入"
        autoComplete="off" spellCheck={false} maxLength={256} onKeyDown={onKey}
        onCompositionStart={() => { composing.current = true; }}
        onCompositionEnd={event => {
          composing.current = false;
          if (event.data) send({ type: "text", text: event.data.slice(0, 256) });
          event.currentTarget.value = "";
        }}
        onChange={event => {
          if (composing.current) return;
          if (event.target.value) send({ type: "text", text: event.target.value.slice(0, 256) });
          event.target.value = "";
        }}
        onPaste={event => {
          event.preventDefault();
          const text = event.clipboardData.getData("text").replace(/[\x00-\x1f]/g, "").slice(0, 256);
          if (text) send({ type: "text", text });
        }} />
    </div>
  </div>;
}
