import type { ChatMessage } from "../../types/investigation";
import type { InvestigationWorkspaceMessage, RegionSelectionHistoryEntry } from "../../types/investigationCreation";

/** Sequence anchors preserve the place of UI actions without fabricating chat turns. */
export function restoreRegionSelectionHistory(
  messages: ChatMessage[],
  sourceMessages: InvestigationWorkspaceMessage[],
  history: RegionSelectionHistoryEntry[] = []
): ChatMessage[] {
  if (!history.length) return messages;
  const restored = [...messages];
  const source = [...sourceMessages].sort((left, right) => left.sequence - right.sequence);
  const events = [...history].sort((left, right) => left.after_sequence - right.after_sequence
    || left.revision - right.revision || left.created_at.localeCompare(right.created_at)
    || left.event_id.localeCompare(right.event_id));
  for (const event of events) {
    const id = `region-selection:${event.event_id}`;
    if (restored.some(message => message.id === id)) continue;
    const anchors = source.filter(message => message.sequence <= event.after_sequence);
    const anchor = anchors[anchors.length - 1];
    let position = anchor ? restored.findIndex(message => message.id === anchor.message_id) + 1 : 0;
    // Several UI choices may occur between the same two conversation messages.
    while (restored[position]?.type === "region_selection") position += 1;
    const date = new Date(event.created_at);
    restored.splice(position, 0, {
      id, sender: "system", type: "region_selection", occurredAt: event.created_at,
      timestamp: Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { hour12: false }),
      regionSelection: event
    });
  }
  return restored;
}
