import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AccountHeader } from "./AccountHeader";
import { AccountSidebar, type FocusSidebarMode } from "./AccountSidebar";
import { AccountTabs, type AccountTabKey } from "./AccountTabs";
import { OverviewTab } from "./OverviewTab";
import { RelatedAccountsCard } from "./RelatedAccountsCard";
import { RiskHistoryPreviewCard } from "./RiskHistoryPreviewCard";
import { MonitoringTimeline } from "./MonitoringTimeline";
import { MonitoringPlanCard } from "./MonitoringPlanCard";
import { CommentRiskProfileView } from "./CommentRiskProfileView";
import { Toast } from "../../components/feedback/Toast";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { fetchMonitoredAccountDetail, fetchMonitoredAccounts } from "../../services/focusUsers";
import { fetchMockCommentRiskProfiles } from "../../mocks/userPenetration";
import type { CommentRiskProfile, MonitoredAccount, RiskOutput } from "../../types/focusUsers";

export function FocusUsersPage() {
  const navigate = useNavigate();
  const [sidebarMode, setSidebarMode] = useState<FocusSidebarMode>("monitored");
  const [accounts, setAccounts] = useState<MonitoredAccount[]>([]);
  const [penetrationUsers, setPenetrationUsers] = useState<CommentRiskProfile[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [selectedPenetrationUserId, setSelectedPenetrationUserId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [activeTab, setActiveTab] = useState<AccountTabKey>("overview");
  const [isRefetching, setIsRefetching] = useState(false);
  const [detailLoadingAccountId, setDetailLoadingAccountId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadAccounts = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const items = await fetchMonitoredAccounts();
      setAccounts(items);
      setSelectedAccountId((current) => (
        current && items.some((item) => item.id === current) ? current : items[0]?.id ?? ""
      ));
    } catch (err) {
      setError(err instanceof Error ? err.message : "重点用户数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
    void fetchMockCommentRiskProfiles().then((profiles) => {
      setPenetrationUsers(profiles);
      setSelectedPenetrationUserId(profiles[0]?.id ?? "");
    });
  }, [loadAccounts]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredAccounts = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) {
      return accounts;
    }
    return accounts.filter((account) => {
      return (
        account.name.toLowerCase().includes(keyword) ||
        account.platformAccountId.toLowerCase().includes(keyword) ||
        account.id.toLowerCase().includes(keyword)
      );
    });
  }, [accounts, query]);

  const filteredPenetrationUsers = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return penetrationUsers;
    return penetrationUsers.filter((profile) => profile.name.toLowerCase().includes(keyword));
  }, [penetrationUsers, query]);

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === selectedAccountId) ?? accounts[0],
    [accounts, selectedAccountId]
  );

  const selectedPenetrationUser = useMemo(
    () => penetrationUsers.find((profile) => profile.id === selectedPenetrationUserId) ?? penetrationUsers[0],
    [penetrationUsers, selectedPenetrationUserId]
  );

  const notify = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const handleRefetch = async () => {
    if (isRefetching) {
      return;
    }
    setIsRefetching(true);
    try {
      let items = await fetchMonitoredAccounts();
      const currentId = selectedAccountId || items[0]?.id || "";
      const current = items.find((item) => item.id === currentId);
      if (current && !current.riskDataLoaded) {
        const hydrated = await fetchMonitoredAccountDetail(current);
        items = items.map((item) => item.id === hydrated.id ? hydrated : item);
      }
      setAccounts(items);
      setSelectedAccountId((current) => (
        current && items.some((item) => item.id === current) ? current : items[0]?.id ?? ""
      ));
      notify("已刷新真实监控数据");
    } catch (err) {
      notify(err instanceof Error ? err.message : "数据刷新失败", "info");
    } finally {
      setIsRefetching(false);
    }
  };

  const handleSelectAccount = (accountId: string) => {
    setSelectedAccountId(accountId);
    setActiveTab("overview");
    const account = accounts.find((item) => item.id === accountId);
    if (!account || account.riskDataLoaded || detailLoadingAccountId === accountId) return;
    setDetailLoadingAccountId(accountId);
    void fetchMonitoredAccountDetail(account)
      .then((hydrated) => {
        setAccounts((items) => items.map((item) => item.id === hydrated.id ? hydrated : item));
      })
      .catch((err) => {
        notify(err instanceof Error ? err.message : "账号风险数据加载失败", "info");
      })
      .finally(() => setDetailLoadingAccountId((current) => current === accountId ? "" : current));
  };

  const handleModeChange = (mode: FocusSidebarMode) => {
    setSidebarMode(mode);
    setQuery("");
  };

  const handleOpenAnalysisTask = (taskId: string) => {
    if (!taskId) {
      notify("该关联账号尚未创建分析任务", "info");
      return;
    }
    navigate(`/tasks/${encodeURIComponent(taskId)}/outputs`);
  };

  const handleOpenRisk = (risk: RiskOutput) => {
    if (!risk.jobId || !risk.id) {
      notify("该产出缺少来源任务信息", "info");
      return;
    }
    navigate(`/tasks/${encodeURIComponent(risk.jobId)}/outputs/${encodeURIComponent(risk.id)}`);
  };

  if (loading) {
    return (
      <main className="focus-users-shell">
        <LoadingState label="正在加载真实重点用户与评论关系..." />
      </main>
    );
  }

  if (error) {
    return (
      <main className="focus-users-shell">
        <ErrorState message={error} onRetry={() => void loadAccounts()} />
      </main>
    );
  }

  return (
    <main className="focus-users-shell">
      <div className="focus-users-layout">
        <AccountSidebar
          mode={sidebarMode}
          accounts={filteredAccounts}
          penetrationUsers={filteredPenetrationUsers}
          selectedAccountId={selectedAccount?.id ?? ""}
          selectedPenetrationUserId={selectedPenetrationUser?.id ?? ""}
          searchValue={query}
          onModeChange={handleModeChange}
          onSearchChange={setQuery}
          onSelectAccount={handleSelectAccount}
          onSelectPenetrationUser={setSelectedPenetrationUserId}
        />

        {sidebarMode === "penetration" ? (
          selectedPenetrationUser ? (
            <CommentRiskProfileView profile={selectedPenetrationUser} />
          ) : (
            <section className="loading-panel">
              <EmptyState title="暂无穿透用户" description="当前近 30 天数据中没有满足入选规则的评论用户。" />
            </section>
          )
        ) : selectedAccount ? (
          <section className="account-workspace" aria-label="账号详情工作区">
            {detailLoadingAccountId === selectedAccount.id ? (
              <div className="loading-panel account-detail-loading">
                <LoadingState label={`正在加载${selectedAccount.name}的真实审核结果...`} />
              </div>
            ) : (
              <>
                <AccountHeader
                  account={selectedAccount}
                  isRefetching={isRefetching}
                  onCopyId={() => notify("平台账号 ID 已复制")}
                  onRefetch={handleRefetch}
                  onCancelWatch={() => notify("已提交取消持续观察申请")}
                  onTaskView={() => handleOpenAnalysisTask(selectedAccount.sourceJobId)}
                  onMenuAction={(label) => notify(`${label} 已触发`, "info")}
                />

                <AccountTabs activeTab={activeTab} onChange={setActiveTab} />

                {activeTab === "overview" ? (
                  <OverviewTab
                    account={selectedAccount}
                    isRefetching={isRefetching}
                    onRefetch={handleRefetch}
                    onSwitchTab={setActiveTab}
                    onOpenDetail={handleOpenRisk}
                    onOpenAnalysisTask={handleOpenAnalysisTask}
                  />
                ) : activeTab === "relations" ? (
                  <RelatedAccountsCard
                    accounts={selectedAccount.relatedAccounts}
                    variant="full"
                    onOpenAnalysisTask={handleOpenAnalysisTask}
                  />
                ) : activeTab === "risks" ? (
                  <RiskHistoryPreviewCard
                    risks={selectedAccount.riskOutputs}
                    variant="full"
                    onOpenRisk={handleOpenRisk}
                  />
                ) : activeTab === "timeline" ? (
                  <MonitoringTimeline events={selectedAccount.timeline} />
                ) : (
                  <MonitoringPlanCard plan={selectedAccount.monitoringPlan} variant="full" />
                )}
              </>
            )}
          </section>
        ) : (
          <section className="loading-panel">
            <EmptyState
              title="暂无重点用户"
              description="从审核结果关联评论用户后，账号和评论关系会显示在这里。"
            />
          </section>
        )}
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}
