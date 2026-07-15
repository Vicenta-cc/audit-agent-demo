import { useState } from "react";
import {
  ChevronRight,
  ClipboardCopy,
  Clock3,
  Loader2,
  MoreHorizontal,
  Music2,
  RefreshCw,
  Slash,
  UsersRound
} from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import type { MonitoredAccount } from "../../types/focusUsers";

interface AccountHeaderProps {
  account: MonitoredAccount;
  isRefetching: boolean;
  onCopyId: () => void;
  onRefetch: () => void;
  onCancelWatch: () => void;
  onTaskView: () => void;
  onMenuAction: (label: string) => void;
}

const taskTone: Record<MonitoredAccount["metrics"]["currentTaskStatus"], "blue" | "green" | "orange"> = {
  running: "green",
  paused: "orange",
  completed: "blue"
};

export function AccountHeader({
  account,
  isRefetching,
  onCopyId,
  onRefetch,
  onCancelWatch,
  onTaskView,
  onMenuAction
}: AccountHeaderProps) {
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [isMoreOpen, setIsMoreOpen] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(account.platformAccountId);
    } catch {
      // Clipboard can be unavailable on insecure origins; the UI feedback remains useful in demo mode.
    }
    onCopyId();
  };

  const handleConfirmCancel = () => {
    setIsConfirmOpen(false);
    onCancelWatch();
  };

  return (
    <section className="account-header-card">
      <div className="account-header-toolbar">
        <h1>当前账号详情</h1>
        <div className="account-header-actions">
          <button className="btn btn-primary" type="button" onClick={onRefetch} disabled={isRefetching}>
            {isRefetching ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            补抓近期发布
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => setIsConfirmOpen(true)}>
            <Slash size={16} />
            取消持续观察
          </button>
          <div className="dropdown-wrap">
            <button
              className="icon-button bordered"
              type="button"
              aria-label="更多操作"
              aria-expanded={isMoreOpen}
              onClick={() => setIsMoreOpen((value) => !value)}
            >
              <MoreHorizontal size={19} />
            </button>
            {isMoreOpen ? (
              <div className="dropdown-menu">
                {["查看账号快照", "导出监控摘要", "暂停提醒"].map((label) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => {
                      setIsMoreOpen(false);
                      onMenuAction(label);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="account-header-main">
        <div className="account-identity">
          <img className="account-detail-avatar" src={account.avatarUrl} alt={`${account.name}头像`} />
          <div>
            <div className="account-name-row">
              <h2>{account.name}</h2>
              <span className="platform-tag larger">
                <Music2 size={14} />
                {account.platform}
              </span>
            </div>
            <div className="account-id-row">
              <span>抖音号：</span>
              <strong>{account.platformAccountId}</strong>
              <button className="copy-button" type="button" onClick={handleCopy} aria-label="复制账号 ID">
                <ClipboardCopy size={15} />
              </button>
            </div>
            <div className="account-scope-row">监控范围：{account.monitorScope.join(" · ")}</div>
          </div>
        </div>

        <div className="account-stats">
          <MetricBlock
            icon={<UsersRound size={16} />}
            label="近期风险产出"
            value={`${account.metrics.recentRiskCount}`}
            suffix="条"
            helper="近 7 天"
          />
          <MetricBlock
            icon={<UsersRound size={16} />}
            label="疑似关联账号"
            value={`${account.metrics.relatedAccountCount}`}
            suffix="个"
            helper="持续归并"
          />
          <MetricBlock
            icon={<Clock3 size={16} />}
            label="最近同步时间"
            value={account.metrics.lastSyncTime}
            helper={account.metrics.lastSyncAgo}
          />
          <div className="metric-block task-metric">
            <div className="metric-label">当前分析任务</div>
            <div className="task-name-row">
              <strong>{account.metrics.currentTaskName}</strong>
              <Badge tone={taskTone[account.metrics.currentTaskStatus]}>{account.metrics.currentTaskStatusLabel}</Badge>
            </div>
            <button className="text-link" type="button" onClick={onTaskView}>
              查看任务
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={isConfirmOpen}
        title="取消持续观察"
        description={`确认停止对「${account.name}」的持续观察？历史产出和关联记录会保留。`}
        confirmText="确认取消"
        onCancel={() => setIsConfirmOpen(false)}
        onConfirm={handleConfirmCancel}
      />
    </section>
  );
}

interface MetricBlockProps {
  icon: JSX.Element;
  label: string;
  value: string;
  suffix?: string;
  helper: string;
}

function MetricBlock({ icon, label, value, suffix, helper }: MetricBlockProps) {
  return (
    <div className="metric-block">
      <div className="metric-label">
        {icon}
        {label}
      </div>
      <div className="metric-value">
        {value}
        {suffix ? <span>{suffix}</span> : null}
      </div>
      <div className="metric-helper">{helper}</div>
    </div>
  );
}
