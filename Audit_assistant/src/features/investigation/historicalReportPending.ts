export interface HistoricalPendingTurnRecovery {
  client_message_id: string;
  turn_id: string;
  after_sequence: number;
}

const PREFIX = "xhs-audit:historical-report-pending:";
const ALLOWED_KEYS = new Set(["client_message_id", "turn_id", "after_sequence"]);

function storageKey(workspaceId: string) {
  return `${PREFIX}${workspaceId}`;
}

export function readHistoricalPendingTurn(
  workspaceId: string
): HistoricalPendingTurnRecovery | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(workspaceId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (
      Object.keys(parsed).some((key) => !ALLOWED_KEYS.has(key))
      || typeof parsed.client_message_id !== "string"
      || typeof parsed.turn_id !== "string"
      || typeof parsed.after_sequence !== "number"
      || !Number.isInteger(parsed.after_sequence)
      || parsed.after_sequence < 0
    ) {
      window.sessionStorage.removeItem(storageKey(workspaceId));
      return null;
    }
    return parsed as unknown as HistoricalPendingTurnRecovery;
  } catch {
    return null;
  }
}

export function storeHistoricalPendingTurn(
  workspaceId: string,
  recovery: HistoricalPendingTurnRecovery
) {
  try {
    window.sessionStorage.setItem(storageKey(workspaceId), JSON.stringify({
      client_message_id: recovery.client_message_id,
      turn_id: recovery.turn_id,
      after_sequence: recovery.after_sequence
    }));
  } catch {
    // A running Turn remains recoverable from the server workspace state.
  }
}

export function clearHistoricalPendingTurn(workspaceId: string) {
  try {
    window.sessionStorage.removeItem(storageKey(workspaceId));
  } catch {
    // Browser storage is optional.
  }
}
