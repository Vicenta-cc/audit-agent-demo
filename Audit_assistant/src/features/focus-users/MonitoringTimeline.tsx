import { Activity, AlertTriangle, FileSearch, Link2, Settings2 } from "lucide-react";
import { EmptyState } from "../../components/feedback/EmptyState";
import type { TimelineEvent } from "../../types/focusUsers";

interface MonitoringTimelineProps {
  events: TimelineEvent[];
}

const eventIcons = {
  crawl: Activity,
  analysis: FileSearch,
  risk: AlertTriangle,
  relation: Link2,
  plan: Settings2
};

export function MonitoringTimeline({ events }: MonitoringTimelineProps) {
  return (
    <article className="dashboard-card timeline-card">
      <div className="card-heading">
        <div>
          <h2>监控时间线</h2>
          <p className="card-heading-helper">审核结果、来源任务和评论关系的真实事件</p>
        </div>
      </div>

      {events.length ? (
        <div className="timeline-list">
          {events.map((event) => {
            const Icon = eventIcons[event.type];
            return (
              <div className={`timeline-item timeline-${event.type}`} key={event.id}>
                <div className="timeline-icon" aria-hidden="true">
                  <Icon size={17} />
                </div>
                <div className="timeline-content">
                  <div className="timeline-title-row">
                    <strong>{event.title}</strong>
                    <time>{event.time}</time>
                  </div>
                  <p>{event.description}</p>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState title="暂无监控事件" description="当前来源任务还没有可展示的事件" />
      )}
    </article>
  );
}
