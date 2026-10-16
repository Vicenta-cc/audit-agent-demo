import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import type {
  ChallengeArea,
  LoginFrame,
  LoginEditableTarget,
  LoginCommandReceipt,
  LoginCommandResult,
} from "../../services/crawlerLoginV2";
export interface LoginKeyboardBridge {
  activate: (
    field: LoginEditableTarget,
    point: { x: number; y: number },
  ) => boolean;
}
type InputIntent = { kind: string; data?: string; shift?: boolean };
interface Editing {
  fieldId: string;
  value: string;
  revision: number;
  confirmed: number;
  start: number;
  end: number;
  direction: string;
  composing: boolean;
  focus: LoginCommandReceipt & { acknowledged: boolean; proven?: boolean };
  intents: InputIntent[];
  frameSequence: number;
  controllerEpoch: number;
  areaVersion: number;
  challengeId: string;
  edit?: LoginCommandReceipt & { revision: number; acknowledged: boolean };
}
export const CrawlerLoginKeyboardBridge = forwardRef<
  LoginKeyboardBridge,
  {
    area: ChallengeArea;
    frame: LoginFrame | null;
    enabled: boolean;
    send: (command: Record<string, unknown>) => LoginCommandReceipt | null;
    result: LoginCommandResult | null;
    onError: (message: string) => void;
    onPending: (pending: boolean) => void;
    onCommit: (fieldId: string) => void;
  }
>(function CrawlerLoginKeyboardBridge(
  { area, frame, enabled, send, result, onError, onPending, onCommit },
  ref,
) {
  const relay = useRef<HTMLTextAreaElement>(null);
  const editing = useRef<Editing | null>(null);
  const pasteText = useRef("");
  const compositionText = useRef("");
  const [networkToken, setNetworkToken] = useState("");
  const [slow, setSlow] = useState(false);
  const pageActive = useRef(document.hasFocus());
  const [projectedCaret, setProjectedCaret] = useState<{
    frame: LoginFrame;
    rect: LoginEditableTarget["rect"];
    color: string;
  } | null>(null);
  const current = useRef({ area, frame, enabled, send, onError, onPending });
  current.current = { area, frame, enabled, send, onError, onPending };
  const clear = (message?: string) => {
    editing.current = null;
    setProjectedCaret(null);
    if (relay.current) {
      relay.current.value = "";
      relay.current.blur();
    }
    if (message) current.current.onError(message);
    current.current.onPending(false);
    setNetworkToken("");
  };
  const field = () =>
    current.current.frame?.metadata.editable_targets?.find(
      (target) => target.field_id === editing.current?.fieldId,
    );
  const report = () => {
    const state = editing.current;
    const target = field();
    const focusWaiting = Boolean(
      state &&
      (!state.focus.acknowledged ||
        !target?.focused ||
        (current.current.frame?.metadata.applied_input_sequence || 0) <
          state.focus.sequence),
    );
    current.current.onPending(
      Boolean(
        state &&
        (focusWaiting ||
          state.edit ||
          state.composing ||
          state.revision > state.confirmed),
      ),
    );
    const picture = current.current.frame;
    const rect = target?.caret?.rect;
    const confirmed = Boolean(
      current.current.enabled &&
      picture && state && target && rect &&
      pageActive.current &&
      document.visibilityState === "visible" &&
      document.activeElement === relay.current &&
      state.focus.proven && state.focus.acknowledged && target.focused &&
      !focusWaiting && !state.edit && !state.composing &&
      state.revision <= state.confirmed &&
      target.applied_edit_revision >= state.confirmed &&
      target.value === state.value &&
      Number.isInteger(target.selection_start) &&
      target.selection_start === target.selection_end &&
      typeof target.caret?.color === "string" &&
      CSS.supports("color", target.caret.color) &&
      picture.sequence >= state.frameSequence &&
      picture.metadata.controller_epoch === state.controllerEpoch &&
      picture.area_version === state.areaVersion &&
      current.current.area.area_version === state.areaVersion &&
      current.current.area.challenge_id === state.challengeId &&
      picture.metadata.challenge_id === state.challengeId &&
      Object.values(rect).every(Number.isFinite) &&
      rect.x >= 0 && rect.y >= 0 && rect.width >= 0 && rect.height > 0 &&
      rect.x + rect.width <= 1 && rect.y + rect.height <= 1
    );
    setProjectedCaret(confirmed ? { frame: picture!, rect: rect!, color: target!.caret!.color } : null);
    setNetworkToken(
      state?.edit?.command_id ||
        (focusWaiting ? state?.focus.command_id || "" : ""),
    );
  };
  const pump = () => {
    const state = editing.current;
    const target = field();
    const picture = current.current.frame;
    if (!state || !target || !picture || !current.current.enabled) return;
    if (
      state.edit?.acknowledged &&
      target.applied_edit_revision >= state.edit.revision &&
      (picture.metadata.applied_input_sequence || 0) >= state.edit.sequence
    ) {
      state.confirmed = state.edit.revision;
      state.edit = undefined;
    }
    if (
      !state.focus.proven &&
      !state.composing &&
      state.focus.acknowledged &&
      target.focused &&
      (picture.metadata.applied_input_sequence || 0) >= state.focus.sequence
    ) {
      let value = target.value;
      let start = target.selection_start ?? value.length;
      let end = target.selection_end ?? start;
      let direction = target.selection_direction || "none";
      for (const intent of state.intents) {
        if (intent.kind === "selectAll") {
          start = 0;
          end = value.length;
        } else if (
          intent.kind === "ArrowLeft" ||
          intent.kind === "ArrowRight"
        ) {
          if (intent.shift) {
            const anchor = direction === "backward" ? end : start;
            const caret = direction === "backward" ? start : end;
            const next = Math.max(
              0,
              Math.min(
                value.length,
                caret + (intent.kind === "ArrowLeft" ? -1 : 1),
              ),
            );
            start = Math.min(anchor, next);
            end = Math.max(anchor, next);
            direction = next < anchor ? "backward" : "forward";
          } else {
            start =
              intent.kind === "ArrowLeft"
                ? start === end
                  ? Math.max(0, start - 1)
                  : start
                : start === end
                  ? Math.min(value.length, end + 1)
                  : end;
            end = start;
            direction = "none";
          }
        } else if (intent.kind.startsWith("insert")) {
          const text = intent.data || "";
          value = (value.slice(0, start) + text + value.slice(end)).slice(
            0,
            target.max_length,
          );
          start = Math.min(value.length, start + text.length);
          end = start;
        } else if (intent.kind.startsWith("delete")) {
          if (start === end) {
            if (intent.kind.includes("Backward"))
              start = Math.max(0, start - 1);
            else if (intent.kind.includes("Forward"))
              end = Math.min(value.length, end + 1);
          }
          value = value.slice(0, start) + value.slice(end);
          end = start;
        }
      }
      state.value = value;
      state.start = start;
      state.end = end;
      state.direction = direction;
      state.intents = [];
      state.focus.proven = true;
      if (relay.current) {
        relay.current.value = value;
        relay.current.setSelectionRange(
          start,
          end,
          direction as "none" | "forward" | "backward",
        );
      }
    }
    if (
      !state.edit &&
      !state.composing &&
      state.focus.proven &&
      state.focus.acknowledged &&
      target.focused &&
      (picture.metadata.applied_input_sequence || 0) >= state.focus.sequence &&
      state.revision > state.confirmed
    ) {
      const receipt = current.current.send({
        type: "edit_input",
        challenge_id: area.challenge_id,
        area_version: area.area_version,
        field_id: state.fieldId,
        value: state.value,
        client_edit_revision: state.revision,
        selection_start: state.start,
        selection_end: state.end,
        selection_direction: state.direction,
      });
      if (receipt)
        state.edit = {
          ...receipt,
          revision: state.revision,
          acknowledged: false,
        };
    }
    report();
  };
  useImperativeHandle(ref, () => ({
    activate: (target, point) => {
      if (
        !current.current.enabled ||
        !current.current.frame?.metadata.editable_targets?.some(
          (field) => field.field_id === target.field_id,
        ) ||
        !relay.current
      )
        return false;
      const previous = editing.current;
      if (previous && (previous.edit || previous.revision > previous.confirmed))
        clear("输入尚未同步，重新选择输入框后请重新输入未完成的内容");
      const gestureId = crypto.randomUUID();
      const receipt = current.current.send({
        type: "pointer_batch",
        challenge_id: current.current.area.challenge_id,
        area_version: current.current.area.area_version,
        gesture_id: gestureId,
        mode: "click",
        focus_field_id: target.field_id,
        points: [
          { ...point, point_sequence: 1, dt_ms: 0, event: "down" },
          { ...point, point_sequence: 2, dt_ms: 0, event: "up" },
        ],
      });
      if (!receipt) return false;
      setProjectedCaret(null);
      editing.current = {
        frameSequence: current.current.frame.sequence,
        controllerEpoch: current.current.frame.metadata.controller_epoch,
        areaVersion: current.current.area.area_version,
        challengeId: current.current.area.challenge_id,
        fieldId: target.field_id,
        value: target.value,
        revision: target.applied_edit_revision,
        confirmed: target.applied_edit_revision,
        start: target.value.length,
        end: target.value.length,
        direction: "none",
        composing: false,
        intents: [],
        focus: { ...receipt, acknowledged: false },
      };
      relay.current.value = target.value;
      relay.current.maxLength = target.max_length;
      relay.current.inputMode = target.input_mode;
      relay.current.focus({ preventScroll: true });
      relay.current.setSelectionRange(target.value.length, target.value.length);
      report();
      return true;
    },
  }));
  useEffect(() => {
    if (!enabled) {
      clear();
      return;
    }
    const state = editing.current;
    if (state && frame && (
      frame.metadata.controller_epoch !== state.controllerEpoch ||
      frame.area_version !== state.areaVersion ||
      area.challenge_id !== state.challengeId ||
      area.area_version !== state.areaVersion
    )) {
      clear();
      return;
    }
    if (state && !field()) {
      clear("输入框已变化，请点击当前页面的输入框重新输入");
      return;
    }
    const targetBeforePump = field();
    if (
      state?.focus.proven &&
      targetBeforePump &&
      !targetBeforePump.focused &&
      (frame?.metadata.applied_input_sequence || 0) >= state.focus.sequence
    ) {
      clear(
        state.edit || state.revision > state.confirmed
          ? "输入框焦点已变化，请重新点击后输入"
          : undefined,
      );
      return;
    }
    if (
      state &&
      targetBeforePump?.focused &&
      state.focus.acknowledged &&
      (frame?.metadata.applied_input_sequence || 0) >= state.focus.sequence &&
      state.revision <= state.confirmed &&
      !state.composing
    ) {
      state.start =
        targetBeforePump.selection_start ?? targetBeforePump.value.length;
      state.end = targetBeforePump.selection_end ?? state.start;
      state.direction = targetBeforePump.selection_direction || "none";
      if (relay.current && document.activeElement === relay.current)
        relay.current.setSelectionRange(
          state.start,
          state.end,
          state.direction as "none" | "forward" | "backward",
        );
    }
    pump();
    const target = field();
    if (
      state &&
      target &&
      !state.edit &&
      !state.composing &&
      state.revision <= state.confirmed &&
      state.value !== target.value
    ) {
      state.value = target.value;
      state.revision = Math.max(state.revision, target.applied_edit_revision);
      state.confirmed = state.revision;
      if (relay.current) relay.current.value = target.value;
    }
  }, [frame, enabled, area.challenge_id, area.area_version]);
  useEffect(() => {
    const state = editing.current;
    if (!state || !result) return;
    if (result.command_id === state.focus.command_id) {
      if (
        result.execution_status === "executed" &&
        result.focused_field_id === state.fieldId
      )
        state.focus.acknowledged = true;
      else {
        clear(
          result.error_message ||
            result.message ||
            "输入框未准备好，请重新点击输入",
        );
        return;
      }
    }
    if (result.command_id === state.edit?.command_id) {
      if (
        result.execution_status === "executed" &&
        result.field_id === state.fieldId &&
        result.client_edit_revision === state.edit.revision
      )
        state.edit.acknowledged = true;
      else {
        clear(
          result.error_message ||
            result.message ||
            "本次输入未完成，请重新点击原页面输入框",
        );
        return;
      }
    }
    pump();
  }, [result]);
  useEffect(() => {
    setSlow(false);
    if (!networkToken) return;
    const slowTimer = window.setTimeout(() => setSlow(true), 500);
    const timeout = window.setTimeout(
      () => clear("输入同步结果暂未确认，请检查原页面后重新点击输入框"),
      8000,
    );
    return () => {
      window.clearTimeout(slowTimer);
      window.clearTimeout(timeout);
    };
  }, [networkToken]);
  useEffect(() => {
    const element = relay.current;
    if (!element) return;
    const beforeInput = (event: InputEvent) => {
      const state = editing.current;
      if (!state || state.focus.proven || state.composing || event.isComposing)
        return;
      if (
        ![
          "insertText",
          "insertFromPaste",
          "insertLineBreak",
          "deleteContentBackward",
          "deleteContentForward",
          "deleteByCut",
        ].includes(event.inputType)
      ) {
        event.preventDefault();
        current.current.onError("输入框正在准备，请稍候再使用此键盘操作");
        return;
      }
      if (
        event.inputType.startsWith("insert") ||
        event.inputType.startsWith("delete")
      )
        state.intents.push({
          kind: event.inputType,
          data:
            event.inputType === "insertFromPaste"
              ? event.data || pasteText.current
              : event.data || undefined,
        });
    };
    element.addEventListener("beforeinput", beforeInput);
    return () => element.removeEventListener("beforeinput", beforeInput);
  }, []);
  useEffect(() => {
    const hideFocus = () => {
      pageActive.current = false;
      setProjectedCaret(null);
    };
    const restoreFocus = () => {
      pageActive.current = document.hasFocus();
    };
    const visibilityChanged = () => {
      pageActive.current = document.visibilityState === "visible" && document.hasFocus();
      setProjectedCaret(null);
    };
    window.addEventListener("blur", hideFocus);
    window.addEventListener("focus", restoreFocus);
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => {
      window.removeEventListener("blur", hideFocus);
      window.removeEventListener("focus", restoreFocus);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, []);
  useEffect(
    () => () => {
      editing.current = null;
      current.current.onPending(false);
    },
    [],
  );
  const update = () => {
    const state = editing.current;
    const target = field();
    const element = relay.current;
    if (!state || !target || !element) return;
    state.value = element.value.slice(0, target.max_length);
    state.revision++;
    state.start = element.selectionStart;
    state.end = element.selectionEnd;
    state.direction = element.selectionDirection || "none";
    pump();
  };
  return (
    <>
      <textarea
        ref={relay}
        className="crawler-login-keyboard-relay"
        tabIndex={-1}
        aria-label="原页面键盘输入"
        autoComplete="off"
        spellCheck={false}
        onChange={update}
        onBlur={() => setProjectedCaret(null)}
        onSelect={() => {
          const state = editing.current;
          const element = relay.current;
          if (
            state &&
            element &&
            (state.start !== element.selectionStart ||
              state.end !== element.selectionEnd)
          )
            update();
        }}
        onPaste={(event) => {
          pasteText.current = event.clipboardData.getData("text");
        }}
        onCompositionStart={() => {
          if (editing.current) {
            compositionText.current = editing.current.value;
            editing.current.composing = true;
            report();
          }
        }}
        onCompositionEnd={(event) => {
          if (editing.current) {
            const state = editing.current;
            state.composing = false;
            if (!state.focus.proven)
              state.intents.push({
                kind: "insertText",
                data:
                  event.data ||
                  relay.current?.value.slice(compositionText.current.length) ||
                  "",
              });
            update();
          }
        }}
        onKeyDown={(event) => {
          const state = editing.current;
          if (state && !state.focus.proven && !state.composing) {
            if (
              (event.metaKey || event.ctrlKey) &&
              event.key.toLowerCase() === "a"
            )
              state.intents.push({ kind: "selectAll" });
            else if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
              if (event.ctrlKey || event.metaKey || event.altKey) {
                event.preventDefault();
                onError("输入框正在准备，请稍候再移动光标");
              } else
                state.intents.push({ kind: event.key, shift: event.shiftKey });
            }
          }
          if (
            event.key === "Enter" &&
            state &&
            field()?.input_type !== "textarea" &&
            !event.nativeEvent.isComposing &&
            !state.composing
          ) {
            event.preventDefault();
            pump();
            onCommit(state.fieldId);
          }
        }}
      />
      {enabled && projectedCaret?.frame === frame ? (
          <span className="crawler-login-caret-layer" aria-hidden="true">
            <span
              className="crawler-login-projected-caret"
              style={{
                left: `${projectedCaret.rect.x * 100}%`,
                top: `${projectedCaret.rect.y * 100}%`,
                width: `max(1px, ${projectedCaret.rect.width * 100}%)`,
                backgroundColor: projectedCaret.color,
                height: `${projectedCaret.rect.height * 100}%`,
              }}
            />
          </span>
      ) : null}
      {slow ? (
        <span className="crawler-login-native-sync" role="status">
          正在同步输入，请稍候…
        </span>
      ) : null}
    </>
  );
});
