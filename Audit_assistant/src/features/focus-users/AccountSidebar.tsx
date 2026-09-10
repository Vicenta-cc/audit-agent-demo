import { Plus, Search, UserRound } from "lucide-react";
import type { CommentRiskProfile, MonitoredAccount } from "../../types/focusUsers";
import { AccountAvatar } from "./AccountAvatar";

export type FocusSidebarMode = "monitored" | "penetration";

interface AccountSidebarProps {
  mode: FocusSidebarMode;
  accounts: MonitoredAccount[];
  penetrationUsers: CommentRiskProfile[];
  selectedAccountId: string;
  selectedPenetrationUserId: string;
  searchValue: string;
  onModeChange: (mode: FocusSidebarMode) => void;
  onSearchChange: (value: string) => void;
  onSelectAccount: (accountId: string) => void;
  onSelectPenetrationUser: (userId: string) => void;
}

export function AccountSidebar({
  mode,
  accounts,
  penetrationUsers,
  selectedAccountId,
  selectedPenetrationUserId,
  searchValue,
  onModeChange,
  onSearchChange,
  onSelectAccount,
  onSelectPenetrationUser
}: AccountSidebarProps) {
  return (
    <aside className="account-sidebar">
      <div className="sidebar-title">
        <UserRound size={18} />
        <span>重点用户</span>
      </div>

      <div className="focus-sidebar-tabs" role="tablist" aria-label="重点用户类型">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "monitored"}
          className={mode === "monitored" ? "is-active" : ""}
          onClick={() => onModeChange("monitored")}
        >
          监控对象
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "penetration"}
          className={mode === "penetration" ? "is-active" : ""}
          onClick={() => onModeChange("penetration")}
        >
          用户穿透
          <span className="focus-tab-count">{penetrationUsers.length}</span>
        </button>
      </div>

      <label className="sidebar-search">
        <Search size={18} />
        <input
          type="search"
          value={searchValue}
          placeholder={mode === "monitored" ? "搜索账号名称 / ID" : "搜索用户名称"}
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </label>

      <div className="account-list">
        {mode === "monitored" ? (
          accounts.length ? accounts.map((account) => (
            <MonitoredAccountItem
              key={account.id}
              account={account}
              selected={account.id === selectedAccountId}
              onSelect={() => onSelectAccount(account.id)}
            />
          )) : <div className="sidebar-empty">未找到匹配账号</div>
        ) : penetrationUsers.length ? (
          penetrationUsers.map((profile) => (
            <button
              key={profile.id}
              type="button"
              className={`account-list-item penetration-user-item${profile.id === selectedPenetrationUserId ? " is-selected" : ""}`}
              onClick={() => onSelectPenetrationUser(profile.id)}
            >
              <AccountAvatar name={profile.name} src={profile.avatarUrl} variant="list" />
              <div className="account-list-content">
                <span className="account-list-name">{profile.name}</span>
              </div>
            </button>
          ))
        ) : (
          <div className="sidebar-empty">未找到穿透用户</div>
        )}
      </div>

      {mode === "monitored" ? (
        <button className="add-account-button" type="button">
          <Plus size={18} />
          新增监控账号
        </button>
      ) : null}
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
      </div>
    </button>
  );
}
