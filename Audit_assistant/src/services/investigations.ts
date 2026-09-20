import { API_BASE, apiRequest } from "./apiClient";

export function deleteInvestigationWorkspace(workspaceId: string, historical: boolean) {
  const resource = historical ? "historical-report-workspaces" : "investigation-workspaces";
  return apiRequest<void>(`/api/${resource}/${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
}
import type {
  InvestigationActivityEvent,
  InvestigationApiSession,
  InvestigationAnswerDeltaEvent,
  InvestigationAnswerResetEvent,
  InvestigationTurnAcceptedResponse,
  InvestigationTurnEvent,
  InvestigationTurnResponse
} from "../types/investigations";

export interface InvestigationTurnWaitOptions {
  onEvent?: (event: InvestigationTurnEvent) => void;
  onActivity?: (event: InvestigationActivityEvent) => void;
  onAnswerDelta?: (event: InvestigationAnswerDeltaEvent) => void;
  onAnswerReset?: (event: InvestigationAnswerResetEvent) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
  afterSequence?: number;
  resumeReplay?: boolean;
}

export function createInvestigationSession(reportVersionId: string) {
  return apiRequest<InvestigationApiSession>(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}/investigation-sessions`,
    { method: "POST" }
  );
}

export function sendInvestigationTurn(
  sessionId: string,
  request: { clientMessageId: string; content: string }
) {
  return apiRequest<InvestigationTurnAcceptedResponse>(
    `/api/investigation-sessions/${encodeURIComponent(sessionId)}/turns`,
    {
      method: "POST",
      body: JSON.stringify({
        client_message_id: request.clientMessageId,
        content: request.content
      })
    }
  );
}

export function getInvestigationTurn(turnId: string) {
  return apiRequest<InvestigationTurnResponse>(
    `/api/investigation-turns/${encodeURIComponent(turnId)}`
  );
}

export function resumeInvestigationTurn(turnId: string) {
  return apiRequest<InvestigationTurnAcceptedResponse>(
    `/api/investigation-turns/${encodeURIComponent(turnId)}/resume`,
    { method: "POST" }
  );
}

export function waitForInvestigationTurn(
  turnId: string,
  options: InvestigationTurnWaitOptions = {}
) {
  return waitForTurn(
    turnId,
    {
      statusPath: `/api/investigation-turns/${encodeURIComponent(turnId)}`,
      eventPath: `/api/investigation-turns/${encodeURIComponent(turnId)}/events`
    },
    options
  );
}

export function waitForTurn(
  turnId: string,
  paths: { statusPath: string; eventPath: string },
  options: InvestigationTurnWaitOptions = {}
) {
  return new Promise<InvestigationTurnResponse>((resolve, reject) => {
    const timeoutMs = options.timeoutMs ?? 180_000;
    const startedAt = Date.now();
    let settled = false;
    let pollTimer = 0;
    let priorTerminalSeen = false;
    let resumeBoundaryReached = !options.resumeReplay;
    let latestEventSequence = Math.max(0, options.afterSequence ?? 0);
    const eventPath = new URL(
      paths.eventPath,
      API_BASE()
    );
    if (options.afterSequence) {
      eventPath.searchParams.set("after_sequence", String(options.afterSequence));
    }
    const source = new EventSource(
      eventPath.toString()
    );

    const cleanup = () => {
      source.close();
      window.clearTimeout(pollTimer);
      options.signal?.removeEventListener("abort", handleAbort);
    };
    const finish = (result: InvestigationTurnResponse) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(result);
    };
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const handleAbort = () => fail(new Error("Investigation Turn wait was cancelled"));
    const acceptSequence = (sequence: number) => {
      if (sequence <= latestEventSequence) return false;
      latestEventSequence = sequence;
      return true;
    };
    const handleEvent = (message: MessageEvent<string>) => {
      const event = parseTurnEvent(message.data);
      if (
        !event
        || !eventBelongsToInvestigationTurn(event, turnId)
        || !acceptSequence(event.sequence)
      ) return;
      const terminal = event.stage === "completed"
        || event.stage === "interrupted"
        || event.stage === "failed";
      if (!resumeBoundaryReached) {
        if (terminal) {
          priorTerminalSeen = true;
          return;
        }
        if (event.stage === "accepted" && priorTerminalSeen) {
          resumeBoundaryReached = true;
        } else {
          return;
        }
      }
      notify(options.onEvent, event);
      if (event.stage === "completed") {
        finish({
          session_id: "",
          turn_id: event.turn_id,
          status: "completed",
          stage: event.stage,
          answer: event.answer,
          safe_message: "",
          retryable: false,
          artifact: event.artifact,
          updated_at: event.occurred_at,
          event_sequence: event.sequence
        });
      } else if (event.stage === "interrupted" || event.stage === "failed") {
        finish({
          session_id: "",
          turn_id: event.turn_id,
          status: event.stage === "interrupted" ? "interrupted" : "error",
          stage: event.stage,
          answer: "",
          safe_message: event.safe_message,
          retryable: event.retryable,
          artifact: event.artifact,
          updated_at: event.occurred_at,
          event_sequence: event.sequence
        });
      }
    };
    const handleActivity = (message: MessageEvent<string>) => {
      const event = parseActivityEvent(message.data);
      if (
        !event
        || !eventBelongsToInvestigationTurn(event, turnId)
        || !acceptSequence(event.sequence)
        || !resumeBoundaryReached
      ) return;
      notify(options.onActivity, event);
    };
    const handleAnswerDelta = (message: MessageEvent<string>) => {
      const event = parseAnswerDeltaEvent(message.data);
      if (
        !event
        || !eventBelongsToInvestigationTurn(event, turnId)
        || !acceptSequence(event.sequence)
        || !resumeBoundaryReached
      ) return;
      notify(options.onAnswerDelta, event);
    };
    const handleAnswerReset = (message: MessageEvent<string>) => {
      const event = parseAnswerResetEvent(message.data);
      if (
        !event
        || !eventBelongsToInvestigationTurn(event, turnId)
        || !acceptSequence(event.sequence)
        || !resumeBoundaryReached
      ) return;
      notify(options.onAnswerReset, event);
    };
    const poll = async () => {
      if (settled) return;
      if (Date.now() - startedAt >= timeoutMs) {
        fail(new Error("Investigation Turn timed out"));
        return;
      }
      try {
        const status = await apiRequest<InvestigationTurnResponse>(paths.statusPath);
        if (status.status !== "running") {
          finish(status);
          return;
        }
      } catch {
        // SSE may still be healthy; retry the status endpoint on the next interval.
      }
      pollTimer = window.setTimeout(poll, 1_500);
    };

    source.addEventListener("auth_expired", () => {
      fail(new Error("登录已失效，请重新登录。"));
      window.dispatchEvent?.(new CustomEvent("application-auth-expired", { detail: "AUTHENTICATION_EXPIRED" }));
    });
    source.addEventListener("turn", handleEvent as EventListener);
    source.addEventListener("activity", handleActivity as EventListener);
    source.addEventListener("answer_delta", handleAnswerDelta as EventListener);
    source.addEventListener("answer_reset", handleAnswerReset as EventListener);
    options.signal?.addEventListener("abort", handleAbort, { once: true });
    if (options.signal?.aborted) {
      handleAbort();
      return;
    }
    pollTimer = window.setTimeout(poll, 1_000);
  });
}

type PublicEventEnvelope = {
  event_id: string;
  turn_id: string;
  sequence: number;
  occurred_at: string;
};

export function eventBelongsToInvestigationTurn(
  event: Pick<PublicEventEnvelope, "turn_id">,
  turnId: string
) {
  return event.turn_id === turnId;
}

function parsePublicEvent(value: string): [Record<string, unknown>, PublicEventEnvelope] | null {
  try {
    const record = JSON.parse(value) as Record<string, unknown>;
    if (
      !record
      || typeof record !== "object"
      || typeof record.event_id !== "string"
      || typeof record.turn_id !== "string"
      || !Number.isInteger(record.sequence)
      || Number(record.sequence) < 1
      || typeof record.occurred_at !== "string"
    ) return null;
    return [record, {
      event_id: record.event_id,
      turn_id: record.turn_id,
      sequence: Number(record.sequence),
      occurred_at: record.occurred_at
    }];
  } catch {
    return null;
  }
}

function parseTurnEvent(value: string): InvestigationTurnEvent | null {
  const parsed = parsePublicEvent(value);
  if (!parsed) return null;
  const [record, envelope] = parsed;
  const stages = new Set([
    "accepted", "planning", "preparing_sources", "acquiring_source", "answering",
    "completed", "interrupted", "failed"
  ]);
  if (
    typeof record.stage !== "string"
    || !stages.has(record.stage)
    || typeof record.answer !== "string"
    || typeof record.safe_message !== "string"
    || typeof record.retryable !== "boolean"
  ) return null;
  return {
    ...envelope,
    stage: record.stage as InvestigationTurnEvent["stage"],
    answer: record.answer,
    safe_message: record.safe_message,
    retryable: record.retryable,
    artifact: record.artifact as InvestigationTurnEvent["artifact"]
  };
}

function parseActivityEvent(value: string): InvestigationActivityEvent | null {
  const parsed = parsePublicEvent(value);
  if (!parsed) return null;
  const [record, envelope] = parsed;
  const statuses = new Set(["running", "succeeded", "failed", "interrupted"]);
  if (
    typeof record.activity_id !== "string"
    || !/^public-activity:[0-9a-f]{16,64}$/.test(record.activity_id)
    || typeof record.status !== "string"
    || !statuses.has(record.status)
    || typeof record.label !== "string"
    || typeof record.summary !== "string"
    || !(
      record.result_count === null
      || record.result_count === undefined
      || (Number.isInteger(record.result_count) && Number(record.result_count) >= 0)
    )
  ) return null;
  return {
    ...envelope,
    activity_id: record.activity_id,
    status: record.status as InvestigationActivityEvent["status"],
    label: record.label,
    summary: record.summary,
    result_count: record.result_count === undefined
      ? undefined
      : record.result_count as number | null
  };
}

function parseAnswerDeltaEvent(value: string): InvestigationAnswerDeltaEvent | null {
  const parsed = parsePublicEvent(value);
  if (!parsed) return null;
  const [record, envelope] = parsed;
  if (
    typeof record.message_id !== "string"
    || !/^public-answer:[0-9a-f]{16,64}$/.test(record.message_id)
    || !Number.isInteger(record.revision)
    || Number(record.revision) < 1
    || typeof record.delta !== "string"
    || record.delta.length < 1
    || record.delta.length > 4_096
  ) return null;
  return {
    ...envelope,
    message_id: record.message_id,
    revision: Number(record.revision),
    delta: record.delta
  };
}

function parseAnswerResetEvent(value: string): InvestigationAnswerResetEvent | null {
  const parsed = parsePublicEvent(value);
  if (!parsed) return null;
  const [record, envelope] = parsed;
  if (
    typeof record.message_id !== "string"
    || !/^public-answer:[0-9a-f]{16,64}$/.test(record.message_id)
    || !Number.isInteger(record.revision)
    || Number(record.revision) < 1
  ) return null;
  return {
    ...envelope,
    message_id: record.message_id,
    revision: Number(record.revision)
  };
}

function notify<T>(callback: ((event: T) => void) | undefined, event: T) {
  try {
    callback?.(event);
  } catch {
    // Consumer rendering errors must not disable terminal handling or polling fallback.
  }
}
