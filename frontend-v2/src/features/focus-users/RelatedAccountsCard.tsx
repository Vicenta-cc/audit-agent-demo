import { useState } from "react";
import { ChevronRight, MoreHorizontal } from "lucide-react";
import type { RelatedAccount } from "../../types/focusUsers";

interface RelatedAccountsCardProps {
  accounts: RelatedAccount[];
  onViewAll: () => void;
  onOpenRelation: () => void;
}

export function RelatedAccountsCard({ accounts, onViewAll, onOpenRelation }: RelatedAccountsCardProps) {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  return (
    <article className="dashboard-card related-card">
      <div className="card-heading">
        <h2>疑似关联账号 Top 3</h2>
      </div>

      <div className="related-list">
        {accounts.map((account) => (
          <div className="related-item" key={account.id}>
            <img src={account.avatarUrl} alt={`${account.name}头像`} />
            <div className="related-main">
              <strong>{account.name}</strong>
              <span>关联原因：{account.reason}</span>
              <span>最近出现：{account.lastSeenAt}</span>
            </div>
            <div className="related-tags">
              {account.tags.map((tag) => (
                <span key={tag}>{tag}</span>
              ))}
            </div>
            <button className="btn btn-secondary compact" type="button" onClick={onOpenRelation}>
              查看
            </button>
            <div className="dropdown-wrap">
              <button
                className="icon-button small bordered"
                type="button"
                aria-label={`${account.name}更多操作`}
                aria-expanded={openMenuId === account.id}
                onClick={() => setOpenMenuId((current) => (current === account.id ? null : account.id))}
              >
                <MoreHorizontal size={17} />
              </button>
              {openMenuId === account.id ? (
                <div className="dropdown-menu related-menu">
                  <button type="button" onClick={() => setOpenMenuId(null)}>
                    标记已确认
                  </button>
                  <button type="button" onClick={() => setOpenMenuId(null)}>
                    排除关联
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      <button className="card-bottom-link" type="button" onClick={onViewAll}>
        查看全部关联账号
        <ChevronRight size={15} />
      </button>
    </article>
  );
}
