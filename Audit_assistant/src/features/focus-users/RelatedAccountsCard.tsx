import { ChevronRight, ExternalLink, UsersRound } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { AccountAvatar } from "./AccountAvatar";
import type { RelatedAccount } from "../../types/focusUsers";

interface RelatedAccountsCardProps {
  accounts: RelatedAccount[];
  totalCount?: number;
  variant?: "preview" | "full";
  onViewAll?: () => void;
  onOpenAnalysisTask: (taskId: string) => void;
}

export function RelatedAccountsCard({
  accounts,
  totalCount = accounts.length,
  variant = "full",
  onViewAll,
  onOpenAnalysisTask
}: RelatedAccountsCardProps) {
  return (
    <article className={`dashboard-card related-card is-${variant}`}>
      <div className="card-heading related-card-heading">
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <h2>
            疑似关联账号
          </h2>
          <span className="related-count">共 {totalCount} 条</span>
        </div>
        {variant === "preview" && onViewAll && totalCount > 0 ? (
          <button className="text-link" type="button" onClick={onViewAll}>
            查看全部关联账号
          </button>
        ) : null}
      </div>

      {accounts.length ? (
        <div className="related-list-compact">
          {accounts.map((account) => {
            const status = getAnalysisStatus(account);

            return (
              <div className="related-comment-item-compact" key={account.id}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <AccountAvatar name={account.name} src={account.avatarUrl} variant="list" />
                </div>
                <div className="related-comment-main-compact">
                  <div className="related-comment-name-row">
                    <strong>{account.name}</strong>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                  <p className="related-comment-desc">{account.sourceCommentText || "（无文本内容，可能为表情或空评论）"}</p>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="related-empty-state">
          <UsersRound size={24} aria-hidden="true" />
          <div>
            <strong>暂无疑似关联账号</strong>
            <p>从审核结果关联评论用户后，真实评论内容会显示在这里。</p>
          </div>
        </div>
      )}
    </article>
  );
}

function getAnalysisStatus(account: RelatedAccount): {
  label: string;
  tone: "blue" | "green" | "orange" | "red" | "gray";
} {
  const status = account.analysisStatus.toLowerCase();
  if (["completed", "done", "success"].includes(status)) return { label: "分析完成", tone: "green" };
  if (["running", "analyzing", "crawling"].includes(status)) return { label: "分析中", tone: "blue" };
  if (["queued", "pending"].includes(status)) return { label: "排队中", tone: "orange" };
  if (["failed", "error"].includes(status)) return { label: "任务异常", tone: "red" };
  if (status === "stopped") return { label: "已停止", tone: "gray" };
  return { label: "已关联", tone: "gray" };
}
