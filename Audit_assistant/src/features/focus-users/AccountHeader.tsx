import { useState } from "react";
import {
  ClipboardCopy,
  Clock3,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  Slash,
  UsersRound,
  AlertTriangle
} from "lucide-react";
import { PlatformIcon } from "../../components/common/PlatformIcon";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import type { MonitoredAccount } from "../../types/focusUsers";
import { AccountAvatar } from "./AccountAvatar";

interface AccountHeaderProps {
  account: MonitoredAccount;
  isRefetching: boolean;
  onCopyId: () => void;
  onRefetch: () => void;
  onCancelWatch: () => void;
  onTaskView: () => void;
  onMenuAction: (label: string) => void;
}

export function AccountHeader({
  account,
  isRefetching,
  onCopyId,
  onRefetch,
  onCancelWatch,
  onMenuAction
}: AccountHeaderProps) {
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [isMoreOpen, setIsMoreOpen] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(account.platformAccountId);
    } catch {
      // Fallback in demo mode
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
        <div className="account-header-info">
          <AccountAvatar name={account.name} src={account.avatarUrl} variant="detail" />
          <div className="account-header-titles">
            <div className="account-name-row">
              <h2>{account.name}</h2>
              <PlatformIcon platform={account.platform} size={15} />
              <div className="account-id-row">
                <span>ID: {account.platformAccountId}</span>
                <button className="copy-button" type="button" onClick={handleCopy} title="复制账号 ID">
                  <ClipboardCopy size={13} />
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="account-header-actions">
          <button
            className="mt-button mt-button-secondary mt-button-small btn-compressed"
            type="button"
            onClick={onRefetch}
            disabled={isRefetching}
          >
            {isRefetching ? <Loader2 className="spin" size={14} /> : <RefreshCw size={14} />}
            <span>刷新数据</span>
          </button>

          <div className="dropdown-wrap" style={{ position: "relative" }}>
            <button
              className="mt-button mt-button-secondary mt-button-small btn-compressed"
              type="button"
              aria-label="更多操作"
              aria-expanded={isMoreOpen}
              onClick={() => setIsMoreOpen((value) => !value)}
            >
              <MoreHorizontal size={15} />
              <span>更多操作</span>
            </button>

            {isMoreOpen ? (
              <div
                className="dropdown-menu"
                style={{
                  position: "absolute",
                  right: 0,
                  top: "100%",
                  marginTop: "4px",
                  background: "#ffffff",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-medium)",
                  boxShadow: "var(--shadow-card)",
                  zIndex: 50,
                  padding: "4px",
                  minWidth: "130px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "2px"
                }}
              >
                <button
                  type="button"
                  style={{
                    padding: "6px 10px",
                    fontSize: "13px",
                    border: "none",
                    background: "transparent",
                    textAlign: "left",
                    cursor: "pointer",
                    color: "var(--color-danger)",
                    borderRadius: "4px",
                    display: "flex",
                    alignItems: "center",
                    gap: "6px"
                  }}
                  onClick={() => {
                    setIsMoreOpen(false);
                    setIsConfirmOpen(true);
                  }}
                >
                  <Slash size={13} />
                  <span>取消持续观察</span>
                </button>
                {["查看账号快照", "导出监控摘要", "暂停提醒"].map((label) => (
                  <button
                    key={label}
                    type="button"
                    style={{
                      padding: "6px 10px",
                      fontSize: "13px",
                      border: "none",
                      background: "transparent",
                      textAlign: "left",
                      cursor: "pointer",
                      color: "#334155",
                      borderRadius: "4px"
                    }}
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

      <div className="account-summary-metrics">
        <div className="acc-metric-item">
          <AlertTriangle size={15} style={{ color: "var(--color-warning)" }} />
          <span className="acc-metric-label">近期风险产出：</span>
          <strong className="acc-metric-val">{account.metrics.recentRiskCount} 条</strong>
          <span className="acc-metric-sub">({account.metrics.riskRangeLabel})</span>
        </div>

        <div className="acc-metric-item">
          <UsersRound size={15} style={{ color: "var(--color-primary)" }} />
          <span className="acc-metric-label">疑似关联账号：</span>
          <strong className="acc-metric-val">{account.metrics.relatedAccountCount} 个</strong>
        </div>

        <div className="acc-metric-item">
          <Clock3 size={15} style={{ color: "#64748b" }} />
          <span className="acc-metric-label">最近同步：</span>
          <strong className="acc-metric-val">{account.metrics.lastSyncTime}</strong>
          <span className="acc-metric-sub">({account.metrics.lastSyncAgo})</span>
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
