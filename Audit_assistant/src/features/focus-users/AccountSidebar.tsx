import { Plus, Search, UserRound } from "lucide-react";
import type { MonitoredAccount } from "../../types/focusUsers";
import { AccountAvatar } from "./AccountAvatar";

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
        <span>重点用户</span>
      </div>

      <label className="sidebar-search">
        <Search size={18} />
        <input
          type="search"
          value={searchValue}
          placeholder="搜索账号名称 / 抖音号"
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </label>

      <div className="account-list">
        {accounts.length ? accounts.map((account) => (
          <MonitoredAccountItem
            key={account.id}
            account={account}
            selected={account.id === selectedAccountId}
            onSelect={() => onSelectAccount(account.id)}
          />
        )) : <div className="sidebar-empty">未找到匹配账号</div>}
      </div>

      <button className="add-account-button" type="button">
        <Plus size={18} />
        新增监控账号
      </button>
    </aside>
  );
}

interface MonitoredAccountItemProps {
  account: MonitoredAccount;
  selected: boolean;
  onSelect: () => void;
}

function MonitoredAccountItem({ account, selected, onSelect }: MonitoredAccountItemProps) {
  return (
    <button
      type="button"
      className={`account-list-item${selected ? " is-selected" : ""}`}
      onClick={onSelect}
    >
      <AccountAvatar name={account.name} src={account.avatarUrl} variant="list" />
      <div className="account-list-content">
        <span className="account-list-name">{account.name}</span>
        {account.platform === "抖音" && account.publicDouyinId ? <span className="account-list-scope">抖音号：{account.publicDouyinId}</span> : null}
      </div>
    </button>
  );
}
