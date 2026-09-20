import { useEffect, useState } from "react";
import { apiRequest } from "../../services/apiClient";

type Quota = { enabled: boolean; used: number; remaining: number; limit: number; reset_at: string; legacy_record_count: number };

export function TaskQuota() {
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
  return <div aria-label="今日任务额度">
    <div><small>今日已用 {quota.used} / {quota.limit} 次 · 剩余 {quota.remaining} 次</small></div>
    <div><small>北京时间每日 00:00 重置；确认执行并接受任务后计次。</small></div>
    {quota.legacy_record_count > 0 ? <small>包含按旧规则统计的历史记录。</small> : null}
  </div>;
}
