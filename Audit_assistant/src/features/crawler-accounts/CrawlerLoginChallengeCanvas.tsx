import { useEffect, useRef } from "react";
import type { ChallengeArea, LoginFrame } from "../../services/crawlerLoginV2";
type Point = {
  x: number;
  y: number;
  event: string;
  point_sequence: number;
  dt_ms: number;
};
export function CrawlerLoginChallengeCanvas({
  area,
  frame,
  enabled,
  send,
  onError,
}: {
  area: ChallengeArea;
  frame: LoginFrame | null;
  enabled: boolean;
  send: (command: Record<string, unknown>) => void;
  onError: (message: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gesture = useRef("");
  const points = useRef<Point[]>([]);
  const pointSequence = useRef(0);
  const oldestBufferedAt = useRef<number | null>(null);
  const previousPoint = useRef(0);
  const timer = useRef(0);
  const generation = useRef(0);
  const latest = useRef({ area, enabled, send, onError });
  const queuedFrame = useRef<LoginFrame | null>(null);
  const decoding = useRef(false);
  latest.current = { area, enabled, send, onError };
  const flush = () => {
    if (!points.current.length || !gesture.current) return;
    if (
      oldestBufferedAt.current !== null &&
      performance.now() - oldestBufferedAt.current > 250
    ) {
      abort();
      latest.current.onError("操作发送积压，本次手势已停止，请重新拖动或画图");
      return;
    }
    oldestBufferedAt.current = null;
    latest.current.send({
      type: "pointer_batch",
      challenge_id: latest.current.area.challenge_id,
      area_version: latest.current.area.area_version,
      gesture_id: gesture.current,
      mode: latest.current.area.kind === "slider" ? "slider" : "draw",
      points: points.current.splice(0),
    });
  };
  const abort = () => {
    window.clearTimeout(timer.current);
    timer.current = 0;
    points.current = [];
    oldestBufferedAt.current = null;
    if (gesture.current && latest.current.enabled)
      latest.current.send({
        type: "abort_gesture",
        gesture_id: gesture.current,
        challenge_id: latest.current.area.challenge_id,
        area_version: latest.current.area.area_version,
      });
    gesture.current = "";
  };
  useEffect(() => {
    abort();
    queuedFrame.current = null;
    generation.current++;
    canvas.current
      ?.getContext("2d")
      ?.clearRect(0, 0, canvas.current.width, canvas.current.height);
    const stop = () => abort();
    window.addEventListener("blur", stop);
    document.addEventListener("visibilitychange", stop);
    return () => {
      abort();
      queuedFrame.current = null;
      generation.current++;
      window.removeEventListener("blur", stop);
      document.removeEventListener("visibilitychange", stop);
    };
  }, [area.challenge_id, area.area_version, enabled]);
  useEffect(() => {
    if (!frame || frame.area_version !== area.area_version) return;
    queuedFrame.current = frame;
    const drain = async () => {
      if (decoding.current) return;
      decoding.current = true;
      try {
        while (queuedFrame.current) {
          const next = queuedFrame.current;
          queuedFrame.current = null;
          const version = generation.current;
          try {
            const bitmap = await createImageBitmap(next.image);
            if (
              version === generation.current &&
              next.area_version === latest.current.area.area_version &&
              canvas.current
            ) {
              canvas.current.width = bitmap.width;
              canvas.current.height = bitmap.height;
              canvas.current.getContext("2d")?.drawImage(bitmap, 0, 0);
            }
            bitmap.close();
          } catch {
            /* The next complete frame can recover from a corrupt image. */
          }
        }
      } finally {
        decoding.current = false;
      }
    };
    void drain();
  }, [frame, area.area_version]);
  const position = (event: { clientX: number; clientY: number }) => {
    const rect = canvas.current!.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  };
  const append = (event: React.PointerEvent, kind: string) => {
    if (!enabled || !gesture.current) return;
    const now = performance.now();
    if (
      oldestBufferedAt.current !== null &&
      now - oldestBufferedAt.current > 250
    ) {
      abort();
      latest.current.onError("操作发送积压，本次手势已停止，请重新拖动或画图");
      return;
    }
    const firstInBatch = points.current.length === 0;
    if (firstInBatch) oldestBufferedAt.current = now;
    const point = {
      ...position(event),
      event: kind,
      point_sequence: ++pointSequence.current,
      dt_ms: firstInBatch
        ? 0
        : Math.min(1000, Math.round(now - previousPoint.current)),
    };
    previousPoint.current = now;
    if (
      kind === "move" &&
      area.kind === "slider" &&
      points.current[points.current.length - 1]?.event === "move"
    ) {
      const previous = points.current[points.current.length - 1];
      point.dt_ms =
        points.current.length === 1
          ? 0
          : Math.min(1000, previous.dt_ms + point.dt_ms);
      points.current[points.current.length - 1] = point;
    } else points.current.push(point);
    if (points.current.length > 64) {
      abort();
      latest.current.onError("操作轨迹发送积压，本次手势已停止，请重新操作");
      return;
    }
    if (kind !== "move") {
      flush();
      return;
    }
    if (!timer.current)
      timer.current = window.setTimeout(() => {
        timer.current = 0;
        flush();
      }, 24);
  };
  return (
    <div className="crawler-login-v2-challenge">
      <p>按原始提示完成验证，可点击、拖动或画图。</p>
      <canvas
        ref={canvas}
        aria-label="人工验证区域"
        tabIndex={enabled ? 0 : -1}
        className={enabled ? "" : "is-disabled"}
        onPointerDown={(event) => {
          if (!enabled) return;
          abort();
          gesture.current = crypto.randomUUID();
          previousPoint.current = performance.now();
          event.currentTarget.setPointerCapture(event.pointerId);
          append(event, "down");
        }}
        onPointerMove={(event) => append(event, "move")}
        onPointerUp={(event) => {
          append(event, "up");
          gesture.current = "";
        }}
        onPointerCancel={abort}
        onWheel={(event) => {
          if (!enabled) return;
          abort();
          send({
            type: "scroll",
            challenge_id: area.challenge_id,
            area_version: area.area_version,
            ...position(event),
            delta_x: Math.max(-760, Math.min(760, event.deltaX)),
            delta_y: Math.max(-760, Math.min(760, event.deltaY)),
          });
        }}
        onKeyDown={(event) => {
          if (
            !enabled ||
            ![
              "Tab",
              "Enter",
              "Escape",
              "Backspace",
              "ArrowLeft",
              "ArrowRight",
              "ArrowUp",
              "ArrowDown",
            ].includes(event.key)
          )
            return;
          event.preventDefault();
          send({
            type: "key",
            key: event.key,
            challenge_id: area.challenge_id,
            area_version: area.area_version,
          });
        }}
      />
      {!frame ? <span role="status">正在获取验证画面…</span> : null}
    </div>
  );
}
