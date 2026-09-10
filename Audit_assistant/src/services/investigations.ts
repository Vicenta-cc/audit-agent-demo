import { API_BASE, apiRequest } from "./apiClient";
import type {
  InvestigationApiSession,
  InvestigationTurnAcceptedResponse,
  InvestigationTurnEvent,
  InvestigationTurnResponse
} from "../types/investigations";

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
  options: {
    onEvent?: (event: InvestigationTurnEvent) => void;
    signal?: AbortSignal;
    timeoutMs?: number;
    afterSequence?: number;
    resumeReplay?: boolean;
  } = {}
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
  options: {
    onEvent?: (event: InvestigationTurnEvent) => void;
    signal?: AbortSignal;
    timeoutMs?: number;
    afterSequence?: number;
    resumeReplay?: boolean;
  } = {}
) {
  return new Promise<InvestigationTurnResponse>((resolve, reject) => {
    const timeoutMs = options.timeoutMs ?? 180_000;
    const startedAt = Date.now();
    let settled = false;
    let pollTimer = 0;
    let priorTerminalSeen = false;
    let resumeBoundaryReached = !options.resumeReplay;
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
    const handleEvent = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as InvestigationTurnEvent;
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
        options.onEvent?.(event);
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
      } catch {
        // The polling fallback remains authoritative if an event cannot be decoded.
      }
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

    source.addEventListener("turn", handleEvent as EventListener);
    options.signal?.addEventListener("abort", handleAbort, { once: true });
    if (options.signal?.aborted) {
      handleAbort();
      return;
    }
    pollTimer = window.setTimeout(poll, 1_000);
  });
}
