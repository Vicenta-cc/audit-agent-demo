import type {
  InvestigationAnswerDeltaEvent,
  InvestigationAnswerDraft,
  InvestigationAnswerResetEvent
} from "../../types/investigations";

export function mergeInvestigationAnswerDelta(
  current: InvestigationAnswerDraft | undefined,
  incoming: InvestigationAnswerDeltaEvent
): InvestigationAnswerDraft {
  if (current && incoming.sequence <= current.event_sequence) return current;
  if (current && incoming.message_id !== current.message_id) return current;
  if (current && incoming.revision < current.revision) return current;
  const continuing = current && incoming.revision === current.revision;
  return {
    message_id: incoming.message_id,
    revision: incoming.revision,
    text: `${continuing ? current.text : ""}${incoming.delta}`,
    event_sequence: incoming.sequence
  };
}

export function applyInvestigationAnswerReset(
  current: InvestigationAnswerDraft | undefined,
  incoming: InvestigationAnswerResetEvent
): InvestigationAnswerDraft {
  if (current && incoming.sequence <= current.event_sequence) return current;
  if (current && incoming.message_id !== current.message_id) return current;
  if (current && incoming.revision < current.revision) return current;
  return {
    message_id: incoming.message_id,
    revision: incoming.revision,
    text: "",
    event_sequence: incoming.sequence
  };
}
