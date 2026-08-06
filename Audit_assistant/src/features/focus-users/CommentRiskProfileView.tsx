import { Clock3, MessageSquareText, ShieldAlert } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { PlatformIcon } from "../../components/common/PlatformIcon";
import { formatCommentRiskTime } from "../../mocks/userPenetration";
import type { CommentRiskLevel, CommentRiskProfile } from "../../types/focusUsers";
import { AccountAvatar } from "./AccountAvatar";
import { DistributionPie } from "./DistributionPie";

const riskMeta: Record<CommentRiskLevel, { label: string; tone: "red" | "orange" | "blue" }> = {
  high: { label: "高危", tone: "red" },
  medium: { label: "中危", tone: "orange" },
  review: { label: "待复核", tone: "blue" }
};

interface CommentRiskProfileViewProps {
  profile: CommentRiskProfile;
}

export function CommentRiskProfileView({ profile }: CommentRiskProfileViewProps) {
  return (
    <section className="penetration-workspace" aria-label={`${profile.name}用户穿透详情`}>
      <article className="dashboard-card penetration-evidence-card">
        <header className="penetration-detail-heading">
          <div className="penetration-person">
            <AccountAvatar name={profile.name} src={profile.avatarUrl} variant="detail" />
            <div>
              <div className="account-name-row">
                <h1>{profile.name}</h1>
                <span className="platform-tag larger" style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
                  <PlatformIcon platform={profile.platform} size={15} />
                  <span>{profile.platform}</span>
                </span>
              </div>
              <p>近 30 天评论风险汇总</p>
            </div>
          </div>
          <Badge tone="blue">用户穿透</Badge>
        </header>

        <div className="penetration-summary-grid" aria-label="近30天违规汇总">
          <SummaryMetric label="违规评论" value={profile.violationCount} helper="去重后" icon={<MessageSquareText size={16} />} />
          <SummaryMetric label="高危" value={profile.highRiskCount} helper="高危评论" icon={<ShieldAlert size={16} />} />
          <SummaryMetric label="中危 / 待复核" value={`${profile.mediumRiskCount} / ${profile.pendingReviewCount}`} helper="按风险等级" icon={<ShieldAlert size={16} />} compact />
          <SummaryMetric label="最近命中" value={formatCommentRiskTime(profile.latestAt)} helper={`${profile.commentAreaCount} 个内容来源`} icon={<Clock3 size={16} />} compact />
        </div>

        <div className="penetration-section-heading">
          <div>
            <h2>违规评论证据</h2>
            <p>近 30 天内按平台和评论去重，共 {profile.comments.length} 条。</p>
          </div>
          <span>{profile.comments.length} 条</span>
        </div>

        <div className="penetration-evidence-table">
          <div className="penetration-evidence-row penetration-evidence-head" aria-hidden="true">
            <span>风险等级</span>
            <span>评论内容</span>
            <span>主命中黑话库</span>
            <span>来源内容</span>
            <span>时间</span>
          </div>
          {profile.comments.map((comment) => {
            const meta = riskMeta[comment.riskLevel];
            return (
              <div className="penetration-evidence-row" key={`${comment.platform}-${comment.id}`}>
                <div className="penetration-risk-cell">
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                  <span>{comment.riskScore} 分</span>
                </div>
                <div className="penetration-comment-cell">
                  <MessageSquareText size={16} aria-hidden="true" />
                  <strong>{comment.content}</strong>
                </div>
                <span>{comment.primaryLibrary?.label || "未归类"}</span>
                <span>{comment.sourceTitle}</span>
                <time dateTime={comment.occurredAt}>{formatCommentRiskTime(comment.occurredAt)}</time>
              </div>
            );
          })}
        </div>
      </article>

      <article className="dashboard-card penetration-distribution-card">
        <div className="penetration-section-heading">
          <div>
            <h2>违规分布</h2>
            <p>图表总数与去重后的违规评论数一致。</p>
          </div>
          <span>{profile.violationCount} 条</span>
        </div>
        <div className="penetration-chart-grid">
          <DistributionPie
            title="风险程度"
            description="高危、中危、待复核评论"
            items={profile.riskDistribution}
          />
          <DistributionPie
            title="主命中黑话库"
            description="每条评论只统计主命中库"
            items={profile.libraryDistribution}
          />
        </div>
      </article>
    </section>
  );
}

interface SummaryMetricProps {
  label: string;
  value: number | string;
  helper: string;
  icon: JSX.Element;
  compact?: boolean;
}

function SummaryMetric({ label, value, helper, icon, compact = false }: SummaryMetricProps) {
  return (
    <div className="penetration-summary-item">
      <div className="metric-label">{icon}{label}</div>
      <strong className={compact ? "is-compact" : ""}>{value}</strong>
      <span>{helper}</span>
    </div>
  );
}
