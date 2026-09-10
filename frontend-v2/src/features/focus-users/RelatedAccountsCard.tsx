import { ChevronRight, ExternalLink, MessageSquareText, UsersRound } from "lucide-react";
import { Badge } from "../../components/common/Badge";
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
        <h2>
          <UsersRound size={18} aria-hidden="true" />
          疑似关联账号
        </h2>
        <span className="related-count">共 {totalCount} 条</span>
      </div>

      {accounts.length ? (
        <div className="related-list">
          {accounts.map((account) => {
            const status = getAnalysisStatus(account);
            const sourceMeta = [
              `${account.platform}评论`,
              account.sourceAuditResultId ? `审核结果 #${account.sourceAuditResultId}` : "",
              account.updatedAtLabel !== "--" ? `更新于 ${account.updatedAtLabel}` : ""
            ].filter(Boolean);

            return (
              <div className="related-comment-item" key={account.id}>
                <div className="related-comment-icon" aria-hidden="true">
                  <MessageSquareText size={18} />
                </div>
                <div className="related-comment-main">
                  <div className="related-comment-name-row">
                    <strong>{account.name}</strong>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                  <p>{account.sourceCommentText || "（无文本内容，可能为表情或空评论）"}</p>
                  <span>{sourceMeta.join(" · ")}</span>
                </div>
                <div className="related-comment-actions">
                  {account.profileUrl ? (
                    <a
                      className="icon-button small bordered"
                      href={account.profileUrl}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`打开${account.name}主页`}
                      title="打开账号主页"
                    >
                      <ExternalLink size={16} />
                    </a>
                  ) : null}
                  <button
                    className="btn btn-secondary compact"
                    type="button"
                    disabled={!account.analysisJobId}
                    title={account.analysisJobId ? account.analysisJobName || "查看分析任务" : "尚未创建分析任务"}
                    onClick={() => onOpenAnalysisTask(account.analysisJobId)}
                  >
                    {account.analysisJobId ? "查看分析任务" : "尚未创建任务"}
                  </button>
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

      {onViewAll && totalCount > 0 ? (
        <button className="card-bottom-link" type="button" onClick={onViewAll}>
          查看全部关联账号
          <ChevronRight size={15} />
        </button>
      ) : null}
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
