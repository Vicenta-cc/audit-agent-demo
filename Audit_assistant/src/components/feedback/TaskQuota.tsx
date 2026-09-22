import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listInvestigationWorkspaces } from "../../services/investigationCreation";
import { apiRequest } from "../../services/apiClient";

type ActiveTask = { task_id: string; job_id: string; kind: string; queue_state: string; decision: string; waiting_reason?: string };
export function activeTaskMessage(task: ActiveTask) {
  if (task.decision === "CANCELLED") return "正在结束任务，等待执行停止。";
  if (task.decision === "PUBLISHING" || task.decision === "PUBLISHED") return "正在完成报告与收尾。";
  if (task.queue_state === "HELD") return "任务已暂停或等待处理，请返回当前任务查看。";
  const reasons: Record<string, string> = {
    system_capacity: "正在排队：执行位置暂满。",
    account_busy: "正在排队：采集账号正在被使用。",
    no_available_authorized_account: "正在等待可用采集账号；请等待公共池空闲，或添加、重新扫码登录自己的私有账号。"
  };
  if (task.waiting_reason && reasons[task.waiting_reason]) return reasons[task.waiting_reason];
  return task.queue_state === "QUEUED" ? "任务已接受，正在等待执行。" : "当前任务尚未结束。";
}

type Quota = { enabled: boolean; used: number; remaining: number | null; limit: number | null; unlimited?: boolean; reset_at: string; legacy_record_count: number; active_task?: ActiveTask | null };

export function TaskQuota({ compact = false }: { compact?: boolean }) {
  const navigate = useNavigate();
  const [opening, setOpening] = useState(false);
  const [navigationError, setNavigationError] = useState("");
  const openCurrentTask = async () => {
    const task = quota?.active_task;
    if (!task || opening) return;
    setOpening(true); setNavigationError("");
    try {
      if (task.kind === "investigation") {
        const workspace = (await listInvestigationWorkspaces()).find(item => item.run_id === task.task_id);
        if (!workspace) throw new Error("当前任务入口暂不可用，请刷新后重试。");
        navigate(`/investigation/${encodeURIComponent(workspace.workspace_session_id)}`);
      } else if (task.job_id) navigate(`/tasks?q=${encodeURIComponent(task.job_id)}`);
      else throw new Error("任务正在入队，请稍后重试。");
    } catch { setNavigationError("当前任务入口暂不可用，请刷新后重试。"); }
    finally { setOpening(false); }
  };
  const [quota, setQuota] = useState<Quota | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let active = true;
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await apiRequest<Quota>("/api/me/task-quota");
        if (active) { setQuota(result.enabled ? result : null); setUnavailable(false); }
      } catch {
        if (active) { setQuota(null); setUnavailable(true); }
      } finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    window.addEventListener("focus", refresh);
    window.addEventListener("task-quota-changed", refresh);
    return () => { active = false; clearInterval(timer); window.removeEventListener("focus", refresh); window.removeEventListener("task-quota-changed", refresh); };
  }, []);
  if (unavailable) return <small role="status">任务额度暂不可用，请刷新或重新登录。</small>;
  if (!quota) return null;
  const content = <div aria-label="今日任务额度">
    <div><small>{quota.unlimited ? `管理员每日任务次数不限 · 今日已用 ${quota.used} 次` : `今日已用 ${quota.used} / ${quota.limit} 次 · 剩余 ${quota.remaining} 次`}</small></div>
    <div><small>{quota.unlimited ? "当前任务结束后即可发起下一个任务。" : "北京时间每日 00:00 重置；确认执行并接受任务后计次。"}</small></div>
    {quota.active_task ? <div className="task-quota-active">
      <small>{activeTaskMessage(quota.active_task)}</small>
      <button className="task-quota-link" type="button" disabled={opening} onClick={() => void openCurrentTask()}>{opening ? "正在打开…" : "返回当前任务"}</button>
      {navigationError ? <small role="alert">{navigationError}</small> : null}
    </div> : null}
    {quota.legacy_record_count > 0 ? <small>包含按旧规则统计的历史记录。</small> : null}
  </div>;
  if (!compact) return content;
  const waiting = quota.active_task?.queue_state === "QUEUED";
  return <details className="inv-toolbar-menu inv-quota-menu">
    <summary>{quota.unlimited ? "每日任务次数不限" : `今日剩余 ${quota.remaining} / ${quota.limit} 次`}{waiting ? <span className="inv-quota-waiting">排队中</span> : null}<span aria-hidden="true">⌄</span></summary>
    <div className="inv-toolbar-popover">{content}</div>
  </details>;
}
