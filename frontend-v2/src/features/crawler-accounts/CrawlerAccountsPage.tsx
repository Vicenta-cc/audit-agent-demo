import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Ban,
  CheckCircle2,
  Pencil,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
  UsersRound
} from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import {
  createCrawlerAccount,
  deleteCrawlerAccount,
  fetchCrawlerAccounts,
  updateCrawlerAccount
} from "../../services/crawlerAccounts";
import type {
  CrawlerAccount,
  CrawlerAccountInput,
  CrawlerAccountPlatform,
  CrawlerAccountStatus
} from "../../types/crawlerAccounts";
import { CrawlerAccountDrawer } from "./CrawlerAccountDrawer";
import { CrawlerAccountLoginDialog } from "./CrawlerAccountLoginDialog";

type PlatformFilter = "all" | CrawlerAccountPlatform;
type StatusFilter = "all" | CrawlerAccountStatus;

const platformMeta: Record<CrawlerAccountPlatform, { label: string; icon: string }> = {
  xhs: {
    label: "小红书",
    icon: new URL("../monitor-tasks/assets/platform-xiaohongshu.svg", import.meta.url).href
  },
  dy: {
    label: "抖音",
    icon: new URL("../monitor-tasks/assets/platform-douyin.svg", import.meta.url).href
  },
  ks: {
    label: "快手",
    icon: new URL("../monitor-tasks/assets/platform-kuaishou.svg", import.meta.url).href
  }
};

const statusMeta: Record<CrawlerAccountStatus, { label: string; className: string }> = {
  active: { label: "可用", className: "is-active" },
  login_required: { label: "待登录", className: "is-login-required" },
  expired: { label: "已失效", className: "is-expired" },
  disabled: { label: "已停用", className: "is-disabled" }
};

export function CrawlerAccountsPage() {
  const [accounts, setAccounts] = useState<CrawlerAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<CrawlerAccount | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionId, setActionId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<CrawlerAccount | null>(null);
  const [loginTarget, setLoginTarget] = useState<CrawlerAccount | null>(null);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadData = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError("");
    try {
      setAccounts(await fetchCrawlerAccounts());
    } catch (loadError) {
      setError(readApiError(loadError));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredAccounts = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return accounts.filter((account) => {
      const matchesQuery =
        !keyword ||
        [account.displayName, account.platformAccountId, platformMeta[account.platform].label]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      const matchesPlatform = platformFilter === "all" || account.platform === platformFilter;
      const matchesStatus = statusFilter === "all" || account.status === statusFilter;
      return matchesQuery && matchesPlatform && matchesStatus;
    });
  }, [accounts, platformFilter, query, statusFilter]);

  const stats = useMemo(
    () => ({
      total: accounts.length,
      active: accounts.filter((account) => account.status === "active").length,
      attention: accounts.filter((account) => ["login_required", "expired"].includes(account.status)).length,
      disabled: accounts.filter((account) => account.status === "disabled").length
    }),
    [accounts]
  );

  const openCreate = () => {
    setEditingAccount(null);
    setDrawerOpen(true);
  };

  const openEdit = (account: CrawlerAccount) => {
    setEditingAccount(account);
    setDrawerOpen(true);
  };

  const handleSubmit = async (input: CrawlerAccountInput) => {
    setSubmitting(true);
    try {
      if (editingAccount) {
        const updated = await updateCrawlerAccount(editingAccount.id, {
          displayName: input.displayName,
          platformAccountId: input.platformAccountId
        });
        setAccounts((current) => current.map((account) => (account.id === updated.id ? updated : account)));
        setToast({ message: "账号信息已更新" });
      } else {
        const created = await createCrawlerAccount(input);
        setAccounts((current) => [created, ...current]);
        setLoginTarget(created);
        setToast({ message: "账号已添加，请扫码登录" });
      }
      setDrawerOpen(false);
      setEditingAccount(null);
    } finally {
      setSubmitting(false);
    }
  };

  const handleLoginSuccess = useCallback(async () => {
    setLoginTarget(null);
    await loadData("refresh");
    setToast({ message: "账号登录成功", tone: "success" });
  }, [loadData]);

  const handleToggleDisabled = async (account: CrawlerAccount) => {
    setActionId(account.id);
    try {
      const nextStatus: CrawlerAccountStatus = account.status === "disabled" ? "login_required" : "disabled";
      const updated = await updateCrawlerAccount(account.id, { status: nextStatus });
      setAccounts((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setToast({ message: nextStatus === "disabled" ? "账号已停用" : "账号已启用，请重新登录" });
    } catch (actionError) {
      setToast({ message: readApiError(actionError), tone: "info" });
    } finally {
      setActionId("");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setActionId(deleteTarget.id);
    try {
      await deleteCrawlerAccount(deleteTarget.id);
      setAccounts((current) => current.filter((account) => account.id !== deleteTarget.id));
      setToast({ message: "采集账号已删除" });
      setDeleteTarget(null);
    } catch (deleteError) {
      setToast({ message: readApiError(deleteError), tone: "info" });
    } finally {
      setActionId("");
    }
  };

  return (
    <main className="crawler-accounts-page">
      <header className="crawler-accounts-header">
        <div>
          <h1>采集账号</h1>
          <p>平台登录账号与当前可用状态</p>
        </div>
        <Button type="button" variant="primary" onClick={openCreate}>
          <Plus size={18} />
          添加账号
        </Button>
      </header>

      <section className="crawler-account-summary" aria-label="账号概况">
        <SummaryItem icon={<UsersRound />} label="账号总数" value={stats.total} tone="blue" />
        <SummaryItem icon={<CheckCircle2 />} label="当前可用" value={stats.active} tone="green" />
        <SummaryItem icon={<ShieldAlert />} label="需要处理" value={stats.attention} tone="orange" />
        <SummaryItem icon={<Ban />} label="已停用" value={stats.disabled} tone="gray" />
      </section>

      <section className="crawler-account-panel">
        <div className="crawler-account-toolbar">
          <label className="crawler-account-search">
            <Search size={18} aria-hidden="true" />
            <input
              value={query}
              placeholder="搜索账号名称或账号 ID"
              aria-label="搜索采集账号"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label className="crawler-account-filter">
            <span>平台</span>
            <select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value as PlatformFilter)}>
              <option value="all">全部平台</option>
              <option value="xhs">小红书</option>
              <option value="dy">抖音</option>
              <option value="ks">快手</option>
            </select>
          </label>
          <label className="crawler-account-filter">
            <span>状态</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}>
              <option value="all">全部状态</option>
              <option value="active">可用</option>
              <option value="login_required">待登录</option>
              <option value="expired">已失效</option>
              <option value="disabled">已停用</option>
            </select>
          </label>
          <IconButton
            type="button"
            className="crawler-account-refresh"
            aria-label="刷新账号列表"
            title="刷新"
            disabled={refreshing}
            onClick={() => void loadData("refresh")}
          >
            <RefreshCw className={refreshing ? "spin" : ""} size={17} />
          </IconButton>
          <span className="crawler-account-result-count">共 {filteredAccounts.length} 个账号</span>
        </div>

        {loading ? (
          <LoadingState label="正在加载采集账号..." />
        ) : error && !accounts.length ? (
          <ErrorState message={error} onRetry={() => void loadData()} />
        ) : filteredAccounts.length ? (
          <div className="crawler-account-table" role="table" aria-label="采集账号列表">
            <div className="crawler-account-table-head" role="row">
              <span>账号</span>
              <span>平台账号 ID</span>
              <span>登录状态</span>
              <span>最近校验</span>
              <span>最近使用</span>
              <span>操作</span>
            </div>
            {filteredAccounts.map((account) => {
              const platform = platformMeta[account.platform];
              const status = statusMeta[account.status];
              const isActing = actionId === account.id;
              return (
                <div className="crawler-account-table-row" role="row" key={account.id}>
                  <div className="crawler-account-identity">
                    <img src={platform.icon} alt="" aria-hidden="true" />
                    <span>
                      <strong>{account.displayName}</strong>
                      <small>{platform.label}</small>
                    </span>
                  </div>
                  <span className="crawler-account-cell" data-label="平台账号 ID">
                    {account.platformAccountId || "待登录后识别"}
                  </span>
                  <span className="crawler-account-cell" data-label="登录状态">
                    <span className={`crawler-account-status ${status.className}`}>{status.label}</span>
                  </span>
                  <span className="crawler-account-cell" data-label="最近校验">
                    {formatDateTime(account.lastValidatedAt)}
                  </span>
                  <span className="crawler-account-cell" data-label="最近使用">
                    {formatDateTime(account.lastUsedAt)}
                  </span>
                  <div className="crawler-account-row-actions">
                    <IconButton
                      type="button"
                      aria-label={`${account.status === "active" ? "重新登录" : "扫码登录"}${account.displayName}`}
                      title={account.status === "active" ? "重新登录" : "扫码登录"}
                      disabled={account.status === "disabled" || isActing}
                      onClick={() => setLoginTarget(account)}
                    >
                      <QrCode size={16} />
                    </IconButton>
                    <IconButton type="button" aria-label={`编辑${account.displayName}`} title="编辑" onClick={() => openEdit(account)}>
                      <Pencil size={16} />
                    </IconButton>
                    <IconButton
                      type="button"
                      aria-label={account.status === "disabled" ? `启用${account.displayName}` : `停用${account.displayName}`}
                      title={account.status === "disabled" ? "启用" : "停用"}
                      disabled={isActing}
                      onClick={() => void handleToggleDisabled(account)}
                    >
                      {account.status === "disabled" ? <CheckCircle2 size={16} /> : <Ban size={16} />}
                    </IconButton>
                    <IconButton
                      type="button"
                      aria-label={`删除${account.displayName}`}
                      title="删除"
                      disabled={isActing}
                      onClick={() => setDeleteTarget(account)}
                    >
                      <Trash2 size={16} />
                    </IconButton>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState
            title={accounts.length ? "暂无匹配账号" : "暂无采集账号"}
            description={accounts.length ? "调整搜索或筛选条件" : "添加平台账号后在这里维护登录状态"}
          />
        )}
      </section>

      <CrawlerAccountDrawer
        open={drawerOpen}
        account={editingAccount}
        submitting={submitting}
        onClose={() => {
          if (!submitting) setDrawerOpen(false);
        }}
        onSubmit={handleSubmit}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除采集账号"
        description={`确认删除「${deleteTarget?.displayName || ""}」？账号记录删除后不可恢复。`}
        confirmText={actionId ? "删除中..." : "确认删除"}
        onCancel={() => {
          if (!actionId) setDeleteTarget(null);
        }}
        onConfirm={() => void handleDelete()}
      />

      <CrawlerAccountLoginDialog
        account={loginTarget}
        onClose={() => setLoginTarget(null)}
        onSuccess={handleLoginSuccess}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

function SummaryItem({
  icon,
  label,
  value,
  tone
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  tone: "blue" | "green" | "orange" | "gray";
}) {
  return (
    <div className="crawler-account-summary-item">
      <span className={`crawler-account-summary-icon is-${tone}`} aria-hidden="true">
        {icon}
      </span>
      <span>
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

function formatDateTime(value: string): string {
  if (!value) return "尚无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date);
}

function readApiError(error: unknown): string {
  const message = error instanceof Error ? error.message : "请求失败";
  try {
    const parsed = JSON.parse(message) as { detail?: string };
    return parsed.detail || message;
  } catch {
    return message;
  }
}
