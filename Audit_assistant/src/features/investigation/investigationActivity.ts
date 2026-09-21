import type { ChatMessage } from "../../types/investigation";
import type { InvestigationActivityEvent } from "../../types/investigations";

const legacyOptionsLabel = "查询可用平台、审核规则和黑话库";

function normalizeLegacyOptionActivities(events: InvestigationActivityEvent[]) {
  let optionQueryIndex = 0;
  return events.map((event) => {
    if (event.label !== legacyOptionsLabel) return event;
    optionQueryIndex += 1;
    return optionQueryIndex === 1
      ? {
        ...event,
        label: "查询可用平台与审核资源",
        summary: event.status === "succeeded" ? "已读取可用平台与审核资源。" : event.summary
      }
      : {
        ...event,
        label: "读取所选审核资源详情",
        summary: event.status === "succeeded" ? "已读取所选审核资源详情。" : event.summary
      };
  });
}

export function mergeInvestigationActivityEvent(
  events: InvestigationActivityEvent[] | undefined,
  incoming: InvestigationActivityEvent
) {
  const current = events || [];
  const visible = incoming.status === "succeeded"
    ? current.filter((event) => !(
      event.label === incoming.label
      && event.activity_id !== incoming.activity_id
      && event.sequence < incoming.sequence
      && (event.status === "failed" || event.status === "interrupted")
    ))
    : current;
  const existingIndex = visible.findIndex(
    (event) => event.activity_id === incoming.activity_id
  );
  if (existingIndex < 0) return [...visible, incoming];
  if (visible[existingIndex].sequence >= incoming.sequence) return visible;
  return visible.map((event, index) => index === existingIndex ? incoming : event);
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
  return normalizeLegacyOptionActivities((events || [])
    .filter((event) => event.turn_id === turnId)
    .reduce<InvestigationActivityEvent[]>(
      (merged, event) => mergeInvestigationActivityEvent(merged, event),
      []
    ));
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
