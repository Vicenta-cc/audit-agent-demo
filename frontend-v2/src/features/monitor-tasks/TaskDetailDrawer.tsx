import { X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import { StatusTag } from "../../components/common/StatusTag";
import type { MonitorTask } from "../../types/jobs";
import { formatDateTime, getResultTime } from "../../services/jobs";
import { TaskMetrics } from "./TaskMetrics";

interface TaskDetailDrawerProps {
  task: MonitorTask | null;
  mode: "detail" | "outputs";
  onClose: () => void;
  onControl: (task: MonitorTask, action: string, label: string) => Promise<void>;
}

export function TaskDetailDrawer({ task, mode, onClose, onControl }: TaskDetailDrawerProps) {
  if (!task) {
    return null;
  }

  const revision = task.raw.current_audit_config_revision;
  const auditConfig = revision?.audit_config;
  const capabilities = auditConfig?.capabilities || task.raw.capabilities || [];
  const libraries = auditConfig?.library_ids || task.raw.library_ids || [];

  return (
    <div className="drawer-backdrop" role="presentation">
      <aside className="task-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="task-drawer-title">
        <header className="drawer-header">
          <div>
            <span className="drawer-kicker">{mode === "outputs" ? "任务产出视图" : "任务详情"}</span>
            <h2 id="task-drawer-title">{task.name}</h2>
            <div className="drawer-meta-line">
              <StatusTag tone={task.statusTone}>{task.status}</StatusTag>
              <span>ID：{task.id}</span>
            </div>
          </div>
          <IconButton type="button" aria-label="关闭任务详情" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>

        <div className="drawer-body">
          <section className="drawer-section">
            <h3>来源与账号</h3>
            <div className="drawer-kv-grid">
              <DrawerKv label="任务来源" value={task.sourceLabel} />
              <DrawerKv label="平台" value={task.platformLabel} />
              <DrawerKv label="任务对象" value={task.objectLabel} />
              {task.raw.run_crawler ? <DrawerKv label="执行账号" value={task.raw.crawler_account_display_name || "共享登录方式"} /> : null}
              {task.raw.run_crawler ? <DrawerKv label="抓取频率" value={`${task.raw.max_items_per_minute || 5} 条/分钟`} /> : null}
              <DrawerKv label="引用方案" value={task.referencePlan} />
              <DrawerKv label="创建时间" value={formatDateTime(task.createdAt)} />
              <DrawerKv label="更新时间" value={task.updatedDisplay} />
            </div>
          </section>

          <section className="drawer-section">
            <h3>运行统计</h3>
            <div className="drawer-metrics">
              <TaskMetrics metrics={task.metrics} />
            </div>
          </section>

          <section className="drawer-section">
            <h3>检测配置摘要</h3>
            <div className="rule-summary">
              <DrawerKv label="绑定词库" value={libraries.length ? libraries.join(" / ") : "按任务配置"} />
              <DrawerKv label="分析能力" value={capabilities.length ? capabilities.join(" / ") : "默认能力"} />
              <DrawerKv label="Prompt" value={revision?.prompt_version || task.raw.prompt_profile_snapshot?.prompt_version || "-"} />
            </div>
          </section>

          <section className="drawer-section">
            <h3>最近日志</h3>
            <div className="drawer-log-list">
              {task.logs.map((log, index) => (
                <div className="drawer-log-item" key={`${log.time || ""}-${index}`}>
                  <span>{formatDateTime(log.time || "")}</span>
                  <strong>{log.message}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="drawer-section">
            <h3>最近审核产出</h3>
            {task.recentOutputs.length ? (
              <div className="drawer-output-list">
                {task.recentOutputs.map((output) => (
                  <div className="drawer-output-item" key={output.audit_result_id || output.id}>
                    <strong>{output.title || output.content_title || "未命名内容"}</strong>
                    <span>
                      {output.risk_level || "unknown"} · {formatDateTime(getResultTime(output))}
                    </span>
                    <p>{output.summary || output.primary_risk || "暂无摘要"}</p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="drawer-empty">暂无风险产出</div>
            )}
          </section>

          <section className="drawer-section">
            <h3>任务操作</h3>
            <div className="drawer-actions">
              <Button
                type="button"
                variant="secondary"
                disabled={!task.raw.available_actions?.pause_crawl}
                onClick={() => void onControl(task, "pause_crawl", "暂停抓取")}
              >
                暂停抓取
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={!task.raw.available_actions?.resume_crawl}
                onClick={() => void onControl(task, "resume_crawl", "继续抓取")}
              >
                继续抓取
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={!task.raw.available_actions?.stop_analysis}
                onClick={() => void onControl(task, "pause_analysis", "暂停分析")}
              >
                暂停分析
              </Button>
              <Button
                type="button"
                variant="primary"
                disabled={!task.raw.available_actions?.backfill_analysis}
                onClick={() => void onControl(task, "backfill_analysis", "继续分析")}
              >
                继续分析
              </Button>
            </div>
          </section>
        </div>
      </aside>
    </div>
  );
}

interface DrawerKvProps {
  label: string;
  value: string;
}

function DrawerKv({ label, value }: DrawerKvProps) {
  return (
    <div className="drawer-kv">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}
