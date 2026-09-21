import { FormEvent, useEffect, useState } from "react";
import { UserPlus, UserRound, UsersRound, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import type {
  CrawlerAccount,
  CrawlerAccountAccessScope,
  CrawlerAccountInput,
  CrawlerAccountPlatform
} from "../../types/crawlerAccounts";

const platformOptions: Array<{ value: CrawlerAccountPlatform; label: string }> = [
  { value: "xhs", label: "小红书" },
  { value: "dy", label: "抖音" },
  { value: "ks", label: "快手" }
];

interface CrawlerAccountDrawerProps {
  open: boolean;
  account: CrawlerAccount | null;
  submitting: boolean;
  allowPublicAccounts?: boolean;
  onClose: () => void;
  onSubmit: (input: CrawlerAccountInput) => Promise<void>;
}

export function CrawlerAccountDrawer({
  open,
  account,
  submitting,
  allowPublicAccounts = false,
  onClose,
  onSubmit
}: CrawlerAccountDrawerProps) {
  const [platform, setPlatform] = useState<CrawlerAccountPlatform>("xhs");
  const [displayName, setDisplayName] = useState("");
  const [platformAccountId, setPlatformAccountId] = useState("");
  const [accessScope, setAccessScope] = useState<CrawlerAccountAccessScope>("private");
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (!open) return;
    setPlatform(account?.platform || "xhs");
    setDisplayName(account?.displayName || "");
    setPlatformAccountId(account?.platformAccountId || "");
    setAccessScope(account?.accessScope || "private");
    setFormError("");
  }, [account, open]);

  if (!open) return null;

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!displayName.trim()) {
      setFormError("请输入账号名称");
      return;
    }
    setFormError("");
    try {
      await onSubmit({
        platform,
        displayName: displayName.trim(),
        platformAccountId: platformAccountId.trim(),
        accessScope
      });
    } catch (error) {
      setFormError(readApiError(error));
    }
  };

  return (
    <div className="crawler-account-drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside
        className="crawler-account-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="crawler-account-drawer-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="crawler-account-drawer-header">
          <div className="crawler-account-drawer-heading">
            <span className="crawler-account-drawer-icon" aria-hidden="true">
              <UserPlus size={20} />
            </span>
            <div>
              <span>{account ? "账号信息" : "账号登记"}</span>
              <h2 id="crawler-account-drawer-title">{account ? "编辑采集账号" : "添加采集账号"}</h2>
            </div>
          </div>
          <IconButton type="button" aria-label="关闭账号编辑" onClick={onClose}>
            <X size={19} />
          </IconButton>
        </header>

        <form className="crawler-account-form" onSubmit={handleSubmit}>
          <div className="crawler-account-form-body">
            <label className="crawler-account-field">
              <span>所属平台</span>
              <select
                value={platform}
                disabled={Boolean(account)}
                onChange={(event) => setPlatform(event.target.value as CrawlerAccountPlatform)}
              >
                {platformOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            {!account && allowPublicAccounts ? (
              <fieldset className="crawler-account-scope-picker">
                <legend>账号池归属</legend>
                <label className={accessScope === "public" ? "is-selected" : ""}>
                  <input
                    type="radio"
                    name="crawler-account-access-scope"
                    value="public"
                    checked={accessScope === "public"}
                    onChange={(event) => setAccessScope(event.target.value as CrawlerAccountAccessScope)}
                  />
                  <span className="crawler-account-scope-picker-icon" aria-hidden="true">
                    <UsersRound size={18} />
                  </span>
                  <span>
                    <strong>公共账号池</strong>
                    <small>所有用户任务可调度，系统优先使用</small>
                  </span>
                </label>
                <label className={accessScope === "private" ? "is-selected" : ""}>
                  <input
                    type="radio"
                    name="crawler-account-access-scope"
                    value="private"
                    checked={accessScope === "private"}
                    onChange={(event) => setAccessScope(event.target.value as CrawlerAccountAccessScope)}
                  />
                  <span className="crawler-account-scope-picker-icon" aria-hidden="true">
                    <UserRound size={18} />
                  </span>
                  <span>
                    <strong>我的私有账号池</strong>
                    <small>仅当前管理员自己的任务可使用</small>
                  </span>
                </label>
              </fieldset>
            ) : null}

            <label className="crawler-account-field">
              <span>账号名称</span>
              <input
                value={displayName}
                maxLength={80}
                placeholder="例如：小红书采集号 A"
                autoFocus
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>

            <label className="crawler-account-field">
              <span>平台账号 ID <small>选填</small></span>
              <input
                value={platformAccountId}
                maxLength={120}
                placeholder="登录后可自动回填"
                onChange={(event) => setPlatformAccountId(event.target.value)}
              />
            </label>

            <div className="crawler-account-status-preview">
              <span>保存后的状态</span>
              <strong>{account ? "保持当前状态" : "待登录"}</strong>
            </div>

            {formError ? <div className="crawler-account-form-error">{formError}</div> : null}
          </div>

          <footer className="crawler-account-drawer-footer">
            <Button type="button" onClick={onClose} disabled={submitting}>
              取消
            </Button>
            <Button type="submit" variant="primary" loading={submitting}>
              {account ? "保存修改" : "保存账号"}
            </Button>
          </footer>
        </form>
      </aside>
    </div>
  );
}

function readApiError(error: unknown): string {
  const message = error instanceof Error ? error.message : "保存失败";
  try {
    const parsed = JSON.parse(message) as { detail?: string };
    return parsed.detail || message;
  } catch {
    return message;
  }
}
