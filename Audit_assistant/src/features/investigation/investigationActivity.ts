import type { ChatMessage } from "../../types/investigation";
import type { InvestigationActivityEvent } from "../../types/investigations";

export function mergeInvestigationActivityEvent(
  events: InvestigationActivityEvent[] | undefined,
  incoming: InvestigationActivityEvent
) {
  const current = events || [];
  const existingIndex = current.findIndex(
    (event) => event.activity_id === incoming.activity_id
  );
  if (existingIndex < 0) return [...current, incoming];
  if (current[existingIndex].sequence >= incoming.sequence) return current;
  return current.map((event, index) => index === existingIndex ? incoming : event);
}

export function activityTimelineMessage(
  turnId: string,
  events: InvestigationActivityEvent[] | undefined,
  timestamp = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
): ChatMessage | null {
  if (!events?.length) return null;
  return {
    id: `msg-activity-${turnId}`,
    sender: "assistant",
    timestamp,
    type: "activity_timeline",
    activityEvents: events
  };
}

export function activityTimelineMessages(
  turnId: string,
  events: InvestigationActivityEvent[] | undefined
) {
  const message = activityTimelineMessage(turnId, events);
  return message ? [message] : [];
}

export function activityEventsForTurn(
  events: InvestigationActivityEvent[] | undefined,
  turnId: string
) {
  return (events || [])
    .filter((event) => event.turn_id === turnId)
    .reduce<InvestigationActivityEvent[]>(
      (merged, event) => mergeInvestigationActivityEvent(merged, event),
      []
    );
}

export function restoreActivityTimelines(
  messages: ChatMessage[],
  sourceMessages: Array<{ turn_id: string; role: "user" | "assistant"; created_at: string }>,
  events: InvestigationActivityEvent[] | undefined
) {
  const restored: ChatMessage[] = [];
  const inserted = new Set<string>();
  messages.forEach((message, index) => {
    const source = sourceMessages[index];
    const turnId = source?.turn_id || "";
    if (source?.role === "assistant" && turnId && !inserted.has(turnId)) {
      const timeline = activityTimelineMessage(
        turnId,
        activityEventsForTurn(events, turnId),
        message.timestamp
      );
      if (timeline) {
        restored.push(timeline);
        inserted.add(turnId);
      }
    }
    restored.push(message);
  });
  return restored;
}

export function insertActivityTimelineBeforeLatestAssistant(
  messages: ChatMessage[],
  turnId: string,
  events: InvestigationActivityEvent[] | undefined
) {
  const activityMessage = activityTimelineMessage(turnId, events);
  if (!activityMessage || messages.some((message) => message.id === activityMessage.id)) {
    return messages;
  }
  const latestAssistantIndex = messages.reduce(
    (latest, message, index) => message.sender === "assistant" ? index : latest,
    -1
  );
  if (latestAssistantIndex < 0) return [...messages, activityMessage];
  return [
    ...messages.slice(0, latestAssistantIndex),
    activityMessage,
    ...messages.slice(latestAssistantIndex)
  ];
}

export function activityTimelineSummary(
  events: InvestigationActivityEvent[],
  active: boolean
) {
  const succeeded = events.filter((event) => event.status === "succeeded").length;
  const unfinished = events.filter((event) => event.status === "running").length;
  const unsuccessful = events.length - succeeded - unfinished;
  if (active || unfinished > 0) {
    return `正在执行 · ${events.length} 个步骤`;
  }
  if (unsuccessful > 0) {
    return `已完成 · ${succeeded} 个成功，${unsuccessful} 个未完成`;
  }
  return `已完成 ${succeeded} 个步骤`;
}
