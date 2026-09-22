import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Pencil,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
  UserRound,
  UsersRound
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
  fetchCrawlerAccountOverview,
  updateCrawlerAccount
} from "../../services/crawlerAccounts";
import type {
  CrawlerAccount,
  CrawlerAccountAccessScope,
  CrawlerAccountInput,
  CrawlerAccountPlatform,
  CrawlerAccountStatus,
  SharedCrawlerPoolSummary
} from "../../types/crawlerAccounts";
import { useApplicationAuth } from "../auth/AuthBoundary";
import { CrawlerAccountDrawer } from "./CrawlerAccountDrawer";
import { CrawlerAccountLoginDialog } from "./CrawlerAccountLoginDialog";

type PlatformFilter = "all" | CrawlerAccountPlatform;
type StatusFilter = "all" | CrawlerAccountStatus | "cooling";

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
  const { user } = useApplicationAuth();
  const isAdmin = !user || user.role === "admin";
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedReturnTo = searchParams.get("return_to") || "";
  const returnTo = requestedReturnTo.startsWith("/investigation") ? requestedReturnTo : "";
  const [accounts, setAccounts] = useState<CrawlerAccount[]>([]);
  const [sharedPool, setSharedPool] = useState<SharedCrawlerPoolSummary>({
    total: 0,
    ready: 0,
    busy: 0,
    unavailable: 0,
    byPlatform: {}
  });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [scopeFilter, setScopeFilter] = useState<CrawlerAccountAccessScope>("private");
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<CrawlerAccount | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionId, setActionId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<CrawlerAccount | null>(null);
  const [loginTarget, setLoginTarget] = useState<CrawlerAccount | null>(null);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const loadGenerationRef = useRef(0);
  const [expandedPlatforms, setExpandedPlatforms] = useState<Record<string, boolean>>({
    xhs: true,
    dy: true,
    ks: true
  });

  const loadData = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    const generation = ++loadGenerationRef.current;
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError("");
    try {
      const overview = await fetchCrawlerAccountOverview();
      if (generation !== loadGenerationRef.current) return;
      setAccounts(overview.accounts);
      setSharedPool(overview.sharedPool);
      const pendingAccountId = window.sessionStorage.getItem("crawler-account-login-target") || "";
      if (pendingAccountId) {
        const pendingAccount = overview.accounts.find((account) => account.id === pendingAccountId);
        if (pendingAccount) setLoginTarget(pendingAccount);
        else window.sessionStorage.removeItem("crawler-account-login-target");
      }
    } catch (loadError) {
      if (generation !== loadGenerationRef.current) return;
      // A failed authoritative read must never leave stale accounts actionable.
      setAccounts([]);
      setError(readApiError(loadError));
    } finally {
      if (generation === loadGenerationRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
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

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const scopedAccounts = useMemo(
    () => isAdmin ? accounts.filter((account) => account.accessScope === scopeFilter) : accounts,
    [accounts, isAdmin, scopeFilter]
  );

  const scopeCounts = useMemo(
    () => ({
      public: accounts.filter((account) => account.accessScope === "public").length,
      private: accounts.filter((account) => account.accessScope === "private").length
    }),
    [accounts]
  );

  const filteredAccounts = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return scopedAccounts.filter((account) => {
      const matchesQuery =
        !keyword ||
        [account.displayName, account.platformAccountId, platformMeta[account.platform].label]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      const matchesPlatform = platformFilter === "all" || account.platform === platformFilter;
      const cooling = isCooling(account, clock);
      const matchesStatus =
        statusFilter === "all" ||
        (statusFilter === "cooling" ? cooling : account.status === statusFilter && !cooling);
      return matchesQuery && matchesPlatform && matchesStatus;
    });
  }, [clock, platformFilter, query, scopedAccounts, statusFilter]);

  const stats = useMemo(
    () => ({
      total: scopedAccounts.length,
      active: scopedAccounts.filter((account) => account.status === "active" && !isCooling(account, clock)).length,
      attention: scopedAccounts.filter(
        (account) => isCooling(account, clock) || ["login_required", "expired"].includes(account.status)
      ).length,
      disabled: scopedAccounts.filter((account) => account.status === "disabled").length
    }),
    [clock, scopedAccounts]
  );

  const openCreate = () => {
    setEditingAccount(null);
    setDrawerOpen(true);
  };

  const openEdit = (account: CrawlerAccount) => {
    setEditingAccount(account);
    setDrawerOpen(true);
  };

  const openLogin = (account: CrawlerAccount) => {
    window.sessionStorage.setItem("crawler-account-login-target", account.id);
    setLoginTarget(account);
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
        if (isAdmin) setScopeFilter(created.accessScope);
        openLogin(created);
        setToast({ message: "账号已添加，请扫码登录" });
      }
      setDrawerOpen(false);
      setEditingAccount(null);
    } finally {
      setSubmitting(false);
    }
  };

  const handleLoginSuccess = useCallback(async () => {
    window.sessionStorage.removeItem("crawler-account-login-target");
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
          {returnTo ? (
            <button type="button" className="crawler-account-return" onClick={() => navigate(returnTo)}>
              <ArrowLeft size={16} aria-hidden="true" />
              返回调查
            </button>
          ) : null}
          <h1>采集账号</h1>
          <p>{isAdmin ? "维护公共账号池和管理员私有账号" : "维护你的私有账号，并查看公共池可用情况"}</p>
        </div>
        <Button type="button" variant="primary" onClick={openCreate}>
          <Plus size={18} />
          {isAdmin ? "添加采集账号" : "添加私有账号"}
        </Button>
      </header>

      <section className="crawler-account-pool-strip" aria-label="公共账号池概况">
        <div className="crawler-account-pool-intro">
          <span className="crawler-account-pool-icon" aria-hidden="true">
            <UsersRound size={19} />
          </span>
          <div>
            <strong>公共账号池</strong>
            <p>创建任务时由系统优先调度，无需手动选择</p>
          </div>
          <span className="crawler-account-pool-badge">自动调度</span>
        </div>
        <div className="crawler-account-pool-metrics">
          <PoolMetric label="公共账号总数" value={sharedPool.total} tone="neutral" />
          <PoolMetric label="当前空闲" value={sharedPool.ready} tone="ready" />
          <PoolMetric label="正在使用" value={sharedPool.busy} tone="busy" />
          <PoolMetric label="不可用" value={sharedPool.unavailable} tone="unavailable" />
        </div>
      </section>

      <section className="crawler-account-panel">
        <div className="crawler-account-panel-heading">
          <div>
            <h2>{isAdmin ? (scopeFilter === "public" ? "公共账号池明细" : "管理员私有账号") : "我的私有账号"}</h2>
            <p>{isAdmin
              ? (scopeFilter === "public" ? "所有用户任务均可调度，由系统优先使用" : "仅用于当前管理员自己的任务")
              : "公共池繁忙时，系统会自动使用你的可用私有账号"}</p>
          </div>
          <div className="crawler-account-compact-summary" aria-label="账号概况">
            <span><strong>{stats.total}</strong> 个账号</span>
            <span className="is-ready"><strong>{stats.active}</strong> 可用</span>
            {stats.attention ? <span className="is-attention"><ShieldAlert size={14} /><strong>{stats.attention}</strong> 待处理</span> : null}
            {stats.disabled ? <span><strong>{stats.disabled}</strong> 已停用</span> : null}
          </div>
        </div>
        {isAdmin ? (
          <div className="crawler-account-scope-tabs" role="tablist" aria-label="账号池分类">
            <button
              type="button"
              role="tab"
              aria-selected={scopeFilter === "public"}
              className={scopeFilter === "public" ? "is-active" : ""}
              onClick={() => setScopeFilter("public")}
            >
              <UsersRound size={16} />
              公共账号池
              <span>{scopeCounts.public}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={scopeFilter === "private"}
              className={scopeFilter === "private" ? "is-active" : ""}
              onClick={() => setScopeFilter("private")}
            >
              <UserRound size={16} />
              我的私有账号
              <span>{scopeCounts.private}</span>
            </button>
          </div>
        ) : null}
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
              <option value="cooling">冷却中</option>
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
          <div className="crawler-account-groups">
            {(Object.keys(platformMeta) as CrawlerAccountPlatform[]).map((platformKey) => {
              const groupAccounts = filteredAccounts.filter((a) => a.platform === platformKey);
              if (groupAccounts.length === 0) return null;
              const meta = platformMeta[platformKey];
              const activeCount = groupAccounts.filter(
                (account) => account.status === "active" && !isCooling(account, clock)
              ).length;
              const exceptionCount = groupAccounts.length - activeCount;

              const isExpanded = expandedPlatforms[platformKey] ?? true;

              return (
                <div className="crawler-account-group" key={platformKey}>
                  <div
                    className="crawler-account-group-header"
                    onClick={() => setExpandedPlatforms((prev) => ({ ...prev, [platformKey]: !isExpanded }))}
                  >
                    <div className="crawler-account-group-title">
                      <img src={meta.icon} alt="" style={{ width: 16, height: 16, borderRadius: 4 }} aria-hidden="true" />
                      <h3>{meta.label}</h3>
                      <span className="crawler-account-group-count">{groupAccounts.length} 个账号</span>
                      <span className="crawler-account-group-stats">
                        可用 {activeCount} / 异常 {exceptionCount}
                      </span>
                    </div>
                    <ChevronDown
                      size={16}
                      style={{
                        transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                        transition: "transform 0.2s"
                      }}
                    />
                  </div>
                  {isExpanded && (
                    <div className="crawler-account-table" role="table" aria-label={`${meta.label}账号列表`}>
                      <div className="crawler-account-table-head" role="row">
                        <span>账号</span>
                        <span>平台账号 ID</span>
                        <span>登录状态</span>
                        <span>最近校验</span>
                        <span>最近使用</span>
                        <span>操作</span>
                      </div>
                      {groupAccounts.map((account) => {
                        const status = accountStatus(account, clock);
                        const isActing = actionId === account.id;
                        return (
                          <div className="crawler-account-table-row" role="row" key={account.id}>
                            <div className="crawler-account-identity">
                              <strong>{account.displayName}</strong>
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
                              {account.canManage ? <>
                              <IconButton
                                type="button"
                                aria-label={`${account.status === "active" ? "重新登录" : "扫码登录"}${account.displayName}`}
                                title={account.status === "active" ? "重新登录" : "扫码登录"}
                                disabled={account.status === "disabled" || isActing}
                                onClick={() => openLogin(account)}
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
                                className="action-delete"
                                aria-label={`删除${account.displayName}`}
                                title="删除"
                                disabled={isActing}
                                onClick={() => setDeleteTarget(account)}
                              >
                                <Trash2 size={16} />
                              </IconButton>
                              </> : <span>由管理员维护</span>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
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
        allowPublicAccounts={isAdmin}
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
        onClose={() => {
          window.sessionStorage.removeItem("crawler-account-login-target");
          setLoginTarget(null);
        }}
        onSuccess={handleLoginSuccess}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

function PoolMetric({
  label,
  value,
  tone
}: {
  label: string;
  value: number;
  tone: "neutral" | "ready" | "busy" | "unavailable";
}) {
  return (
    <div className={`crawler-account-pool-metric is-${tone}`}>
      <span aria-hidden="true" />
      <small>{label}</small>
      <strong>{value}</strong>
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

function isCooling(account: CrawlerAccount, now: number): boolean {
  if (account.status !== "active" || !account.cooldownUntil) return false;
  const until = new Date(account.cooldownUntil).getTime();
  return Number.isFinite(until) && until > now;
}

function accountStatus(account: CrawlerAccount, now: number): { label: string; className: string } {
  if (isCooling(account, now)) {
    return {
      label: `冷却中 · 至 ${formatDateTime(account.cooldownUntil)}`,
      className: "is-cooling"
    };
  }
  return statusMeta[account.status];
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
