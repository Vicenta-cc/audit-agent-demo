import { Music2, Plus, Search, UserRound } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import type { AccountStatus, MonitoredAccount } from "../../types/focusUsers";

const statusMeta: Record<AccountStatus, { label: string; tone: "blue" | "green" | "orange" }> = {
  continuous: { label: "持续观察中", tone: "blue" },
  watching: { label: "观察中", tone: "orange" },
  completed: { label: "分析完成", tone: "green" }
};

interface AccountSidebarProps {
  accounts: MonitoredAccount[];
  selectedAccountId: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  onSelectAccount: (accountId: string) => void;
}

export function AccountSidebar({
  accounts,
  selectedAccountId,
  searchValue,
  onSearchChange,
  onSelectAccount
}: AccountSidebarProps) {
  return (
    <aside className="account-sidebar">
      <div className="sidebar-title">
        <UserRound size={18} />
        <span>监控对象</span>
      </div>

      <label className="sidebar-search">
        <Search size={18} />
        <input
          type="search"
          value={searchValue}
          placeholder="搜索账号名称 / ID"
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </label>

      <div className="account-list">
        {accounts.length ? (
          accounts.map((account) => {
            const meta = statusMeta[account.status];
            const isSelected = account.id === selectedAccountId;
            return (
              <button
                key={account.id}
                type="button"
                className={`account-list-item${isSelected ? " is-selected" : ""}`}
                onClick={() => onSelectAccount(account.id)}
              >
                <img className="account-list-avatar" src={account.avatarUrl} alt={`${account.name}头像`} />
                <div className="account-list-content">
                  <div className="account-list-topline">
                    <span className="account-list-name">{account.name}</span>
                    <span className="platform-tag">
                      <Music2 size={13} />
                      {account.platform}
                    </span>
                  </div>
                  <div className="account-list-scope">{account.monitorScope.join(" · ")}</div>
                </div>
                <Badge tone={meta.tone}>{meta.label}</Badge>
              </button>
            );
          })
        ) : (
          <div className="sidebar-empty">未找到匹配账号</div>
        )}
      </div>

      <button className="add-account-button" type="button">
        <Plus size={18} />
        新增监控账号
      </button>
    </aside>
  );
}
