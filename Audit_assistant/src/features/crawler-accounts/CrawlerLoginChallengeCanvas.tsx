import { useEffect, useRef, useState } from "react";
import {
  CrawlerLoginKeyboardBridge,
  type LoginKeyboardBridge,
} from "./CrawlerLoginKeyboardBridge";
import type {
  ChallengeArea,
  LoginFrame,
  LoginCommandReceipt,
  LoginCommandResult,
} from "../../services/crawlerLoginV2";
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
  commandResult,
}: {
  area: ChallengeArea;
  frame: LoginFrame | null;
  enabled: boolean;
  send: (command: Record<string, unknown>) => LoginCommandReceipt | null;
  commandResult: LoginCommandResult | null;
  onError: (message: string) => void;
}) {
  const [renderedFrame, setRenderedFrame] = useState<LoginFrame | null>(null);
  const keyboard = useRef<LoginKeyboardBridge>(null);
  const inputClick = useRef(false);
  const inputsPending = useRef(false);
  const deferredAction = useRef<{
    command: Record<string, unknown>;
    created: number;
    complete: boolean;
  } | null>(null);
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
    deferredAction.current = null;
    inputsPending.current = false;
    setRenderedFrame(null);
    queuedFrame.current = null;
    generation.current++;
    canvas.current
      ?.getContext("2d")
      ?.clearRect(0, 0, canvas.current.width, canvas.current.height);
    const stop = () => {
      deferredAction.current = null;
      abort();
    };
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
              setRenderedFrame(next);
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
  const finishDeferred = () => {
    const pending = deferredAction.current;
    if (!pending || !pending.complete || inputsPending.current) return;
    deferredAction.current = null;
    if (
      !latest.current.enabled ||
      pending.command.area_version !== latest.current.area.area_version ||
      pending.command.challenge_id !== latest.current.area.challenge_id
    )
      return;
    if (performance.now() - pending.created > 3000) {
      latest.current.onError("输入同步较慢，请确认内容后重新点击");
      return;
    }
    latest.current.send(pending.command);
  };
  return (
    <div className="crawler-login-v2-challenge">
      <p>按页面提示直接输入，或点击、拖动、画图完成验证。</p>
      <div className="crawler-login-native-surface">
        <canvas
          ref={canvas}
          aria-label="人工验证区域"
          tabIndex={enabled ? 0 : -1}
          className={enabled ? "" : "is-disabled"}
          onPointerDown={(event) => {
            if (!enabled) return;
            abort();
            const point = position(event);
            const target = renderedFrame?.metadata.editable_targets?.find(
              (field) =>
                point.x >= field.rect.x &&
                point.x <= field.rect.x + field.rect.width &&
                point.y >= field.rect.y &&
                point.y <= field.rect.y + field.rect.height,
            );
            if (target && keyboard.current?.activate(target, point)) {
              inputClick.current = true;
              event.preventDefault();
              return;
            }
            if (inputsPending.current) {
              deferredAction.current = {
                created: performance.now(),
                complete: false,
                command: {
                  type: "pointer_batch",
                  challenge_id: area.challenge_id,
                  area_version: area.area_version,
                  gesture_id: crypto.randomUUID(),
                  mode: "click",
                  points: [
                    {
                      ...position(event),
                      point_sequence: 1,
                      dt_ms: 0,
                      event: "down",
                    },
                  ],
                },
              };
              event.currentTarget.setPointerCapture(event.pointerId);
              return;
            }
            gesture.current = crypto.randomUUID();
            previousPoint.current = performance.now();
            event.currentTarget.setPointerCapture(event.pointerId);
            append(event, "down");
          }}
          onPointerMove={(event) => {
            if (inputClick.current) return;
            const queued = deferredAction.current;
            if (queued && queued.command.type === "pointer_batch") {
              const point = (
                queued.command.points as { x: number; y: number }[]
              )[0];
              const next = position(event);
              if (
                Math.abs(next.x - point.x) > 0.015 ||
                Math.abs(next.y - point.y) > 0.015
              ) {
                deferredAction.current = null;
                onError("请等待输入同步后再拖动或画图");
              }
              return;
            }
            append(event, "move");
          }}
          onPointerUp={(event) => {
            if (inputClick.current) {
              inputClick.current = false;
              return;
            }
            if (deferredAction.current) {
              const queued = deferredAction.current;
              (queued.command.points as Record<string, unknown>[]).push({
                ...position(event),
                point_sequence: 2,
                dt_ms: 0,
                event: "up",
              });
              queued.complete = true;
              finishDeferred();
              return;
            }
            append(event, "up");
            gesture.current = "";
          }}
          onPointerCancel={() => {
            inputClick.current = false;
            deferredAction.current = null;
            abort();
          }}
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
            if (inputsPending.current && event.key === "Enter") {
              deferredAction.current = {
                created: performance.now(),
                complete: true,
                command: {
                  type: "key",
                  key: "Enter",
                  challenge_id: area.challenge_id,
                  area_version: area.area_version,
                },
              };
              return;
            }
            send({
              type: "key",
              key: event.key,
              challenge_id: area.challenge_id,
              area_version: area.area_version,
            });
          }}
        />
        <CrawlerLoginKeyboardBridge
          ref={keyboard}
          area={area}
          frame={renderedFrame}
          enabled={enabled}
          send={send}
          result={commandResult}
          onError={(message) => {
            deferredAction.current = null;
            onError(message);
          }}
          onPending={(pending) => {
            inputsPending.current = pending;
            if (!pending) finishDeferred();
          }}
          onCommit={(fieldId) => {
            deferredAction.current = {
              created: performance.now(),
              complete: true,
              command: {
                type: "key",
                key: "Enter",
                field_id: fieldId,
                challenge_id: area.challenge_id,
                area_version: area.area_version,
              },
            };
            finishDeferred();
          }}
        />
      </div>
      {!frame ? <span role="status">正在获取验证画面…</span> : null}
    </div>
  );
}
