import { useState } from "react";
import {
  Ban,
  CheckCircle2,
  ChevronDown,
  CircleX,
  Loader2,
  Workflow
} from "lucide-react";
import type { InvestigationActivityEvent } from "../../types/investigations";
import { activityTimelineSummary } from "./investigationActivity";

interface InvestigationActivityTimelineProps {
  events: InvestigationActivityEvent[];
  active?: boolean;
  activeLabel?: string;
}

function ActivityStatusIcon({ event }: { event: InvestigationActivityEvent }) {
  if (event.status === "running") return <Loader2 className="spin" aria-label="进行中" />;
  if (event.status === "succeeded") return <CheckCircle2 aria-label="已完成" />;
  if (event.status === "failed") return <CircleX aria-label="未成功" />;
  return <Ban aria-label="已中断" />;
}

export function InvestigationActivityTimeline({
  events,
  active = false,
  activeLabel = "正在组织回答……"
}: InvestigationActivityTimelineProps) {
  const [expanded, setExpanded] = useState(active);
  if (!events.length) return null;

  return (
    <section className={`inv-activity-timeline${active ? " is-active" : ""}`} aria-label="调用过程">
      <button
        type="button"
        className="inv-activity-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="inv-activity-title"><Workflow size={15} />调用过程</span>
        <span className="inv-activity-summary">{activityTimelineSummary(events, active)}</span>
        <ChevronDown className={expanded ? "is-expanded" : ""} size={15} />
      </button>
      {expanded ? (
        <div className="inv-activity-body">
          <ol className="inv-activity-list">
            {events.map((event) => (
              <li key={event.activity_id} className={`is-${event.status}`}>
                <ActivityStatusIcon event={event} />
                <div>
                  <strong>{event.label}</strong>
                  {event.status === "running" ? null : <span>{event.summary}</span>}
                </div>
                {typeof event.result_count === "number" ? <em>{event.result_count} 项</em> : null}
              </li>
            ))}
          </ol>
          {active ? (
            <div className="inv-activity-current" role="status" aria-live="polite">
              <Loader2 className="spin" size={14} />
              <span>{activeLabel}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
