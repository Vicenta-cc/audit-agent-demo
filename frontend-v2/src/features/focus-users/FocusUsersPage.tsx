import { useEffect, useMemo, useState } from "react";
import { AccountHeader } from "./AccountHeader";
import { AccountSidebar } from "./AccountSidebar";
import { AccountTabs, type AccountTabKey } from "./AccountTabs";
import { OverviewTab } from "./OverviewTab";
import { Toast } from "../../components/feedback/Toast";
import { fetchMonitoredAccounts } from "../../services/focusUsers";
import type { MonitoredAccount } from "../../types/focusUsers";
import { FileSearch, ListChecks, Settings2, UsersRound } from "lucide-react";

const tabPlaceholders: Record<Exclude<AccountTabKey, "overview">, { title: string; text: string; icon: typeof FileSearch }> = {
  risks: {
    title: "风险产出",
    text: "风险产出完整列表将在下一阶段实现",
    icon: FileSearch
  },
  relations: {
    title: "关联账号",
    text: "关联账号管理将在下一阶段实现",
    icon: UsersRound
  },
  timeline: {
    title: "监控时间线",
    text: "监控事件时间线将在下一阶段实现",
    icon: ListChecks
  },
  plan: {
    title: "监控计划",
    text: "监控计划编辑将在下一阶段实现",
    icon: Settings2
  }
};

export function FocusUsersPage() {
  const [accounts, setAccounts] = useState<MonitoredAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [activeTab, setActiveTab] = useState<AccountTabKey>("overview");
  const [isRefetching, setIsRefetching] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    let mounted = true;
    fetchMonitoredAccounts().then((items) => {
      if (!mounted) {
        return;
      }
      setAccounts(items);
      setSelectedAccountId(items[0]?.id ?? "");
    });

    return () => {
      mounted = false;
    };
  }, []);

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

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === selectedAccountId) ?? accounts[0],
    [accounts, selectedAccountId]
  );

  const notify = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const handleRefetch = () => {
    if (isRefetching) {
      return;
    }
    setIsRefetching(true);
    window.setTimeout(() => {
      setIsRefetching(false);
      notify("近期发布补抓已加入队列");
    }, 850);
  };

  const handleSelectAccount = (accountId: string) => {
    setSelectedAccountId(accountId);
    setActiveTab("overview");
  };

  if (!selectedAccount) {
    return (
      <main className="focus-users-shell">
        <section className="loading-panel">正在加载重点用户数据...</section>
      </main>
    );
  }

  return (
    <main className="focus-users-shell">
      <div className="focus-users-layout">
        <AccountSidebar
          accounts={filteredAccounts}
          selectedAccountId={selectedAccount.id}
          searchValue={query}
          onSearchChange={setQuery}
          onSelectAccount={handleSelectAccount}
        />

        <section className="account-workspace" aria-label="账号详情工作区">
          <AccountHeader
            account={selectedAccount}
            isRefetching={isRefetching}
            onCopyId={() => notify("平台账号 ID 已复制")}
            onRefetch={handleRefetch}
            onCancelWatch={() => notify("已提交取消持续观察申请")}
            onTaskView={() => notify("任务详情抽屉将在下一阶段接入", "info")}
            onMenuAction={(label) => notify(`${label} 已触发`, "info")}
          />

          <AccountTabs activeTab={activeTab} onChange={setActiveTab} />

          {activeTab === "overview" ? (
            <OverviewTab
              account={selectedAccount}
              isRefetching={isRefetching}
              onRefetch={handleRefetch}
              onSwitchTab={setActiveTab}
              onOpenDetail={() => notify("风险内容详情页将在下一阶段接入", "info")}
              onOpenRelation={() => notify("关联账号分析任务将在下一阶段接入", "info")}
            />
          ) : (
            <StructuredTabPlaceholder {...tabPlaceholders[activeTab]} />
          )}
        </section>
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

interface StructuredTabPlaceholderProps {
  title: string;
  text: string;
  icon: typeof FileSearch;
}

function StructuredTabPlaceholder({ title, text, icon: Icon }: StructuredTabPlaceholderProps) {
  return (
    <section className="tab-placeholder-card">
      <div className="tab-placeholder-icon" aria-hidden="true">
        <Icon size={30} />
      </div>
      <div>
        <h2>{title}</h2>
        <p>{text}</p>
      </div>
    </section>
  );
}
