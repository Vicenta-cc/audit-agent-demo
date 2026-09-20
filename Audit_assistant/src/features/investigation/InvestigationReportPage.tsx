import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Download,
  FileSearch,
  List,
  Search,
  UserRound,
  X
} from "lucide-react";
import {
  fetchReportAccountDetail,
  fetchReportAccounts,
  fetchReportFindingEvidence,
  fetchReportPostDetail,
  fetchReportPresentation
} from "../../services/reports";
import type {
  AvailableFindingBinding,
  ReportAccountDetail,
  ReportAccountEntry,
  ReportAccountFilter,
  ReportAccountIndexPage,
  ReportAccountMetricStatistics,
  ReportAccountSort,
  ReportEvidencePresentation,
  ReportFindingEvidence,
  ReportPostDetail,
  ReportPostPresentation,
  ReportPresentationProjection,
  ReportPresentationSection
} from "../../types/reports";
import { resolvePublishedReportVersion } from "./reportRoute";
import { ReportPostSourceDetails } from "./ReportPostSourceDetails";

type DrawerState =
  | { type: "account"; ref: string }
  | { type: "account-index"; filter: ReportAccountFilter }
  | { type: "evidence"; ref: string }
  | { type: "post"; ref: string }
  | null;

const decisionLabels: Record<string, string> = {
  pass: "通过",
  review: "复审",
  reject: "拒绝"
};

const riskLabels: Record<string, string> = {
  high: "高风险",
  medium: "中风险",
  low: "低风险",
  none: "无风险"
};

function displayNumber(value: number | null | undefined) {
  return value == null ? "暂不可用" : value.toLocaleString("zh-CN");
}

function displayTime(value: string | null | undefined) {
  if (!value) return "暂不可用";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

function StatBandItem({ label, value }: {
  label: string;
  value: number | null | undefined;
}) {
  return (
    <div className="r31-stat-item">
      <dt>{label}</dt>
      <dd>{displayNumber(value)}</dd>
    </div>
  );
}

function sectionDomId(sectionRef: string) {
  return `report-${sectionRef}`;
}

function AccountMetricGrid({ statistics, includesPublishing, className = "" }: {
  statistics: ReportAccountMetricStatistics;
  includesPublishing: boolean;
  className?: string;
}) {
  const publishing = [
    { key: "published_post_count", label: "发布帖子", risk: false },
    { key: "risk_published_post_count", label: "风险帖子", risk: true }
  ] as const;
  const commenting = [
    { key: "comment_count", label: "评论", risk: false },
    { key: "risk_comment_count", label: "风险评论", risk: true }
  ] as const;
  const metrics = [
    ...(includesPublishing && Number(statistics.published_post_count || 0) > 0
      ? [...publishing, ...commenting]
      : [...commenting, ...(includesPublishing ? publishing : [])]),
    { key: "commented_post_count", label: "评论涉及帖子", risk: false } as const,
    { key: "commented_post_author_count", label: "评论对象", risk: false } as const
  ];
  return (
    <span className={`r31-account-metrics ${includesPublishing ? "is-six" : "is-four"} ${className}`.trim()}>
      {metrics.map(({ key, label, risk }) => (
        <span key={key} className={risk ? `is-risk ${statistics[key] === 0 ? "is-none" : ""}`.trim() : undefined}>
          <small>{label}</small><b>{displayNumber(statistics[key])}</b>
        </span>
      ))}
    </span>
  );
}

type AccountTableMode = "publishing" | "commenting" | "cross" | "risk";

const accountTableHeaders: Record<AccountTableMode, string[]> = {
  publishing: ["账号", "发布帖子", "风险帖子", "评论", "风险评论", "评论涉及帖子", "详情"],
  commenting: ["账号", "评论", "风险评论", "涉及帖子", "评论对象", "详情"],
  cross: ["账号", "出现调查", "本次评论", "本次风险评论", "本次涉及帖子", "详情"],
  risk: ["账号", "风险评论", "评论", "涉及帖子", "评论对象", "详情"]
};

function AccountTable({
  entries,
  mode,
  onOpen
}: {
  entries: ReportAccountEntry[];
  mode: AccountTableMode;
  onOpen: (ref: string) => void;
}) {
  return (
    <div className={`r31-account-table is-${mode}`} role="table">
      <div className="r31-account-table-head" role="row">
        {accountTableHeaders[mode].map((label) => <span key={label} role="columnheader">{label}</span>)}
      </div>
      <div className="r31-account-table-body" role="rowgroup">
        {entries.map((entry) => {
          const statistics = entry.statistics;
          return (
            <button
              key={entry.entry_ref}
              type="button"
              className="r31-account-table-row"
              role="row"
              aria-label={`查看 ${entry.display_name} 的账号活动概览`}
              onClick={() => onOpen(entry.entry_ref)}
            >
              <span className="r31-account-table-name" role="cell" title={entry.display_name}>
                <span className="r31-account-avatar" aria-hidden="true"><UserRound size={15} /></span>
                <strong>{entry.display_name}</strong>
              </span>
              {mode === "publishing" ? (
                <>
                  <span role="cell">{displayNumber(statistics.published_post_count)}</span>
                  <span role="cell" className={Number(statistics.risk_published_post_count || 0) > 0 ? "is-risk" : ""}>{displayNumber(statistics.risk_published_post_count)}</span>
                  <span role="cell">{displayNumber(statistics.comment_count)}</span>
                  <span role="cell" className={Number(statistics.risk_comment_count || 0) > 0 ? "is-risk" : ""}>{displayNumber(statistics.risk_comment_count)}</span>
                  <span role="cell">{displayNumber(statistics.commented_post_count)}</span>
                </>
              ) : null}
              {mode === "cross" ? (
                <>
                  <span role="cell">{displayNumber(entry.comment_investigation_count)}</span>
                  <span role="cell">{displayNumber(statistics.comment_count)}</span>
                  <span role="cell">{displayNumber(statistics.risk_comment_count)}</span>
                  <span role="cell">{displayNumber(statistics.commented_post_count)}</span>
                </>
              ) : null}
              {mode === "commenting" ? (
                <>
                  <span role="cell">{displayNumber(statistics.comment_count)}</span>
                  <span role="cell" className={Number(statistics.risk_comment_count || 0) > 0 ? "is-risk" : ""}>{displayNumber(statistics.risk_comment_count)}</span>
                  <span role="cell">{displayNumber(statistics.commented_post_count)}</span>
                  <span role="cell">{displayNumber(statistics.commented_post_author_count)}</span>
                </>
              ) : null}
              {mode === "risk" ? (
                <>
                  <span role="cell" className="is-risk">{displayNumber(statistics.risk_comment_count)}</span>
                  <span role="cell">{displayNumber(statistics.comment_count)}</span>
                  <span role="cell">{displayNumber(statistics.commented_post_count)}</span>
                  <span role="cell">{displayNumber(statistics.commented_post_author_count)}</span>
                </>
              ) : null}
              <span
                className="r31-account-table-detail"
                role="cell"
                title="查看账号活动概览"
              >
                <ChevronRight size={15} aria-hidden="true" />
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function RiskTag({ risk }: { risk: string }) {
  return <span className={`r31-risk-tag is-${risk || "none"}`}>{riskLabels[risk] || "未分级"}</span>;
}

function PostRow({ post, onOpen }: { post: ReportPostPresentation; onOpen: (ref: string) => void }) {
  const decisionLabel = decisionLabels[post.decision];
  return (
    <div className="r31-post-row">
      <div className="r31-post-row-head">
        <div>
          <strong>{post.title}</strong>
          {post.author_display_name ? <small>作者：{post.author_display_name}</small> : null}
        </div>
        <RiskTag risk={post.risk_level} />
      </div>
      <p>{post.content_summary || post.audit_summary || "当前报告未提供内容摘要。"}</p>
      <div className="r31-post-row-foot">
        {decisionLabel ? <span>{decisionLabel}</span> : null}
        <button type="button" onClick={() => onOpen(post.post_ref)}>
          查看帖子详情 <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

function SectionHeading({ section }: { section: ReportPresentationSection }) {
  return <h2><span>{section.section_number}</span>{section.title}</h2>;
}

function FormalParagraphs({ paragraphs }: { paragraphs: string[] }) {
  return <>{paragraphs.filter(Boolean).map((paragraph, index) => <p key={index}>{paragraph}</p>)}</>;
}

interface SectionProps {
  section: ReportPresentationSection;
  report: ReportPresentationProjection;
  onOpenAccount: (ref: string) => void;
  onOpenAccountIndex: (filter: ReportAccountFilter) => void;
  onOpenEvidence: (ref: string) => void;
  onOpenPost: (ref: string) => void;
  onOpenAppendix: (query?: Record<string, string>) => void;
}

function ReportSection({
  section,
  report,
  onOpenAccount,
  onOpenAccountIndex,
  onOpenEvidence,
  onOpenPost,
  onOpenAppendix
}: SectionProps) {
  const statistics = report.statistics;
  const metadata = report.report_metadata;

  if (section.presentation_kind === "overview") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <dl className="r31-metadata-table">
          <div><dt>调查名称</dt><dd>{metadata.source_name || "暂不可用"}</dd></div>
          <div><dt>平台</dt><dd>{metadata.platform.status === "available" ? metadata.platform.label : "暂不可用"}</dd></div>
          <div><dt>报告状态</dt><dd>{metadata.status === "published" ? "已发布" : metadata.status}</dd></div>
          <div><dt>发布时间</dt><dd>{displayTime(metadata.published_at)}</dd></div>
        </dl>
        <h3>调查摘要</h3>
        <FormalParagraphs paragraphs={report.investigation_summary.paragraphs} />
        {metadata.scope.status === "available" ? (
          <>
            <h3>资料范围</h3>
            <p>{metadata.scope.text}</p>
          </>
        ) : null}
      </section>
    );
  }

  if (section.presentation_kind === "data_overview") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <FormalParagraphs paragraphs={section.presentation_paragraphs || section.paragraphs} />
      </section>
    );
  }

  if (section.presentation_kind === "content_scale") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        {statistics.independently_reviewed_comments == null ? (
          <FormalParagraphs paragraphs={section.paragraphs} />
        ) : (
          <dl className="r31-stat-band">
            <StatBandItem label="纳入报告帖子" value={statistics.canonical_posts} />
            <StatBandItem label="已完成独立审核评论" value={statistics.independently_reviewed_comments} />
          </dl>
        )}
      </section>
    );
  }

  if (section.presentation_kind === "audit_risk_statistics") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <div className="r31-table-wrap">
          <table className="r31-stat-table">
            <tbody>
              <tr><th>审核决定</th><td>通过 {displayNumber(statistics.decision.pass)}</td><td>复审 {displayNumber(statistics.decision.review)}</td><td>拒绝 {displayNumber(statistics.decision.reject)}</td></tr>
              <tr><th>风险等级</th><td>高风险 {displayNumber(statistics.risk_level.high)}</td><td>中风险 {displayNumber(statistics.risk_level.medium)} · 低风险 {displayNumber(statistics.risk_level.low)}</td><td>无风险 {displayNumber(statistics.risk_level.none)}</td></tr>
              <tr><th>评论审核</th><td colSpan={2}>已完成独立审核评论 {displayNumber(statistics.independently_reviewed_comments)}</td><td>评论自身风险 {displayNumber(statistics.comment_own_risk)}</td></tr>
              <tr><th>审核材料</th><td>直接研判依据 {displayNumber(statistics.evidence.direct)}</td><td>辅助材料 {displayNumber(statistics.evidence.indirect)}</td><td>边界材料 {displayNumber(statistics.evidence.counter)}</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    );
  }

  if (section.presentation_kind === "account_activity_overview") {
    const accounts = report.accounts;
    const isUnifiedAudit = metadata.template_kind === "unified_audit";
    if (accounts.snapshot_summary) {
      return (
        <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
          <SectionHeading section={section} />
          <FormalParagraphs paragraphs={section.paragraphs} />
          <div className="r31-table-wrap">
            <table className="r31-stat-table">
              <thead><tr><th>发布账号</th><th>本次已审核帖子</th></tr></thead>
              <tbody>{accounts.snapshot_summary.entries.map((entry, index) => (
                <tr key={index}><td>{entry.display_name}</td><td>{displayNumber(entry.published_post_count)}</td></tr>
              ))}</tbody>
            </table>
          </div>
          <p className="r31-availability-note">{accounts.snapshot_summary.scope}</p>
        </section>
      );
    }
    if (accounts.status === "unavailable") {
      return (
        <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
          <SectionHeading section={section} />
          <FormalParagraphs paragraphs={section.paragraphs} />
          <p className="r31-availability-note">当前发布报告未提供结构化账号活动。</p>
        </section>
      );
    }
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <dl className="r31-account-coverage">
          <StatBandItem label={accounts.target_entries?.length ? "调查目标" : "发布帖子"} value={accounts.target_entries?.length ? accounts.coverage?.target_account_count : statistics.canonical_posts} />
          <StatBandItem label="发布账号" value={accounts.coverage?.post_author_account_count} />
          <StatBandItem label="评论账号" value={accounts.coverage?.comment_author_account_count} />
          <StatBandItem label="去重账号" value={accounts.coverage?.distinct_account_count} />
        </dl>
        <div className="r31-publishing-account-groups">
          {accounts.target_entries?.length ? <div className="r31-account-group is-first">
            <h3>调查目标</h3>
            {accounts.target_entries?.length
              ? <AccountTable entries={accounts.target_entries} mode="publishing" onOpen={onOpenAccount} />
              : <p className="r31-empty-line">本次调查没有结构化调查目标账号。</p>}
          </div> : null}
          <div className={`r31-account-group ${accounts.target_entries?.length ? "" : "is-first"}`}>
            <h3>{accounts.target_entries?.length ? "其他发布账号" : "本次发布账号"}</h3>
            {accounts.post_author_entries?.length
              ? <AccountTable entries={accounts.post_author_entries.slice(0, 5)} mode="publishing" onOpen={onOpenAccount} />
              : <p className="r31-empty-line">本次调查没有可展示的发布账号。</p>}
            {accounts.post_author_entries?.length ? (
              <button type="button" className="r31-text-action" onClick={() => onOpenAccountIndex("post_author")}>
                查看全部发布账号（{displayNumber(accounts.coverage?.post_author_account_count)}） <ChevronRight size={15} />
              </button>
            ) : null}
          </div>
        </div>
        {isUnifiedAudit ? (
          <div className="r31-account-group r31-comment-account-panel r31-current-commenters">
            <div className="r31-group-heading">
              <h3>本次评论账号</h3>
              <span>基于本次发布报告</span>
            </div>
            {accounts.comment_author_entries?.length
              ? <AccountTable entries={accounts.comment_author_entries} mode="commenting" onOpen={onOpenAccount} />
              : <p className="r31-empty-line">本次调查没有具有稳定标识的评论账号。</p>}
          </div>
        ) : <>
          <div className="r31-account-group r31-comment-account-panel r31-dynamic-commenters">
          <div className="r31-group-heading">
            <h3>跨调查评论账号</h3>
            <span>{accounts.cross_investigation_commenters?.basis_label}</span>
          </div>
          {accounts.cross_investigation_commenters?.status === "available" ? (
            <>
              {accounts.cross_investigation_commenters.entries.length
                ? <AccountTable entries={accounts.cross_investigation_commenters.entries} mode="cross" onOpen={onOpenAccount} />
                : <p className="r31-empty-line">当前没有符合条件的跨调查评论账号。</p>}
              <button
                type="button"
                className="r31-text-action"
                onClick={() => onOpenAccountIndex("cross_investigation_commenter")}
              >
                {accounts.cross_investigation_commenters.action_label}（{displayNumber(accounts.cross_investigation_commenters.total_count)}） <ChevronRight size={15} />
              </button>
            </>
          ) : (
            <p className="r31-availability-note">
              {accounts.cross_investigation_commenters?.unavailable_message || "当前可访问调查的账号活动暂时不可用。"}
            </p>
          )}
        </div>
        <div className="r31-account-group r31-comment-account-panel r31-risk-commenters">
          <div className="r31-group-heading"><h3>风险评论账号</h3><span>{accounts.risk_commenters?.basis_label}</span></div>
          {accounts.risk_commenters?.entries.length
            ? <AccountTable entries={accounts.risk_commenters.entries} mode="risk" onOpen={onOpenAccount} />
            : <p className="r31-empty-line">本次调查没有风险评论账号。</p>}
          {accounts.risk_commenters?.status === "available" ? (
            <button
              type="button"
              className="r31-text-action"
              onClick={() => onOpenAccountIndex("risk_commenter")}
            >
              {accounts.risk_commenters.action_label}（{displayNumber(accounts.risk_commenters.total_count)}） <ChevronRight size={15} />
            </button>
          ) : null}
        </div>
        </>}
      </section>
    );
  }

  if (["risk_post_analysis", "safe_post_analysis", "pending_post_analysis"].includes(section.presentation_kind)) {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        {section.group_summary ? <p>{section.group_summary}</p> : null}
        <div className="r31-post-list">
          {(section.group_posts || []).map((post) => (
            <article className="r31-standalone-row" key={post.post_ref}>
              <PostRow post={post} onOpen={onOpenPost} />
              <p className="r31-disposition-note"><strong>原审核说明：</strong>{post.audit_summary || "原审核结果未提供文字说明。"}</p>
            </article>
          ))}
        </div>
      </section>
    );
  }

  if (section.presentation_kind === "appendix") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <FormalParagraphs paragraphs={section.paragraphs} />
        <button type="button" className="r31-text-action" onClick={() => onOpenAppendix({ view: "posts" })}>
          查看全部帖子与审核结论（{displayNumber(report.appendix.post_count)}） <ChevronRight size={15} />
        </button>
        {report.appendix.direct_evidence_count > 0 ? (
          <button type="button" className="r31-text-action" onClick={() => onOpenAppendix({ view: "evidence" })}>
            查看全部研判依据（{displayNumber(report.appendix.direct_evidence_count)}） <ChevronRight size={15} />
          </button>
        ) : null}
      </section>
    );
  }

  if (section.presentation_kind === "audit_samples") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <FormalParagraphs paragraphs={section.paragraphs} />
        <div className="r31-post-list">
          {(section.sample_posts || []).map((post) => (
            <article className="r31-standalone-row" key={post.post_ref}>
              <PostRow post={post} onOpen={onOpenPost} />
              <p className="r31-disposition-note"><strong>原审核说明：</strong>{post.audit_summary || "原审核结果未提供文字说明。"}</p>
            </article>
          ))}
        </div>
        <button type="button" className="r31-text-action" onClick={() => onOpenAppendix({ view: "posts" })}>
          查看全部已审核帖子（{displayNumber(section.sample_total)}） <ChevronRight size={15} />
        </button>
      </section>
    );
  }

  if (section.presentation_kind === "investigation_findings") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <p className="r31-finding-count">本报告形成 {displayNumber(statistics.investigation_finding_count)} 项主要调查发现。</p>
        <FormalParagraphs paragraphs={section.paragraphs} />
      </section>
    );
  }

  if (section.presentation_kind === "investigation_finding") {
    const binding = section.finding_binding;
    if (!binding || binding.status === "unavailable") {
      return (
        <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section r31-finding-section">
          <SectionHeading section={section} />
          <FormalParagraphs paragraphs={section.paragraphs} />
          <p className="r31-availability-note">本节关联资料暂不可用。</p>
        </section>
      );
    }
    return (
      <FindingSection
        section={section}
        binding={binding}
        onOpenEvidence={onOpenEvidence}
        onOpenPost={onOpenPost}
        onOpenAppendix={onOpenAppendix}
      />
    );
  }

  if (section.presentation_kind === "standalone_risk_posts") {
    const items = section.standalone_items || [];
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
        <SectionHeading section={section} />
        <p className="r31-section-intro">未形成共性调查发现、但仍需单独关注的内容</p>
        {Number(section.standalone_count || 0) > 0 ? (
          <>
            <div className="r31-post-list">
              {items.map((item) => (
                <article className="r31-standalone-row" key={item.post_ref}>
                  <PostRow post={item} onOpen={onOpenPost} />
                  <p className="r31-disposition-note">{item.disposition_note || item.audit_summary}</p>
                </article>
              ))}
            </div>
            <button type="button" className="r31-text-action" onClick={() => onOpenAppendix({ view: "standalone" })}>
              查看全部独立风险帖子（{displayNumber(section.standalone_count)}） <ChevronRight size={15} />
            </button>
          </>
        ) : <p className="r31-empty-state">当前报告没有其他独立风险事项。</p>}
      </section>
    );
  }

  if (section.presentation_kind === "conclusion") {
    return (
      <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section ethnic-report-conclusion">
        <SectionHeading section={section} />
        <h3>调查结论</h3>
        <FormalParagraphs paragraphs={section.paragraphs} />
      </section>
    );
  }

  return (
    <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section">
      <SectionHeading section={section} />
      <FormalParagraphs paragraphs={section.paragraphs} />
    </section>
  );
}

function FindingSection({
  section,
  binding,
  onOpenEvidence,
  onOpenPost,
  onOpenAppendix
}: {
  section: ReportPresentationSection;
  binding: AvailableFindingBinding;
  onOpenEvidence: (ref: string) => void;
  onOpenPost: (ref: string) => void;
  onOpenAppendix: (query?: Record<string, string>) => void;
}) {
  return (
    <section id={sectionDomId(section.section_ref)} className="ethnic-report-section r31-report-section r31-finding-section">
      <SectionHeading section={section} />
      <p>{binding.statement}</p>
      <dl className="r31-finding-facts">
        <StatBandItem label="相关帖子" value={binding.related_post_count} />
        <StatBandItem label="代表帖子" value={binding.representative_post_count} />
        <StatBandItem label="研判依据" value={binding.direct_evidence_count} />
      </dl>
      {binding.representative_posts.length ? (
        <div className="r31-post-list">
          {binding.representative_posts.map((post) => <PostRow key={post.post_ref} post={post} onOpen={onOpenPost} />)}
        </div>
      ) : null}
      {binding.boundary_notes.length ? (
        <aside className="r31-boundary-note">
          <strong>研判边界</strong>
          {binding.boundary_notes.map((note, index) => <p key={index}>{note}</p>)}
        </aside>
      ) : null}
      <div className="r31-link-row">
        <button type="button" onClick={() => onOpenAppendix({ finding_ref: binding.investigation_finding_ref })}>
          查看本项全部相关帖子（{binding.related_post_count}）
        </button>
        {binding.direct_evidence_count > 0 ? (
          <button type="button" onClick={() => onOpenEvidence(binding.investigation_finding_ref)}>
            查看研判依据（{binding.direct_evidence_count}）
          </button>
        ) : null}
      </div>
    </section>
  );
}

function DrawerShell({ title, onClose, children, className = "" }: { title: string; onClose: () => void; children: ReactNode; className?: string }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const previousPaddingRight = document.body.style.paddingRight;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    if (scrollbarWidth > 0) {
      const currentPadding = Number.parseFloat(window.getComputedStyle(document.body).paddingRight) || 0;
      document.body.style.paddingRight = `${currentPadding + scrollbarWidth}px`;
    }
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      document.body.style.paddingRight = previousPaddingRight;
      previousFocus?.focus();
    };
  }, [onClose]);

  return createPortal(
    <div className="ethnic-evidence-layer r31-drawer-layer" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <aside className={`ethnic-evidence-drawer r31-drawer ${className}`.trim()} role="dialog" aria-modal="true" aria-label={title}>
        <header className="r31-drawer-header">
          <div><span>调查报告</span><h2>{title}</h2></div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭" title="关闭"><X size={18} /></button>
        </header>
        <div className="r31-drawer-body">{children}</div>
      </aside>
    </div>,
    document.body
  );
}

function AsyncState({ loading, error }: { loading: boolean; error: string }) {
  if (loading) return <p className="r31-drawer-status" role="status">正在加载...</p>;
  if (error) return <p className="r31-drawer-status is-error" role="alert">{error}</p>;
  return null;
}

function DistributionMetrics({ statistics }: { statistics: ReportAccountMetricStatistics }) {
  return (
    <div className="r31-distribution-metrics">
      <p><span>评论 <b>{displayNumber(statistics.comment_count)}</b></span><i>·</i><span>风险评论 <b className={Number(statistics.risk_comment_count || 0) > 0 ? "is-risk" : ""}>{displayNumber(statistics.risk_comment_count)}</b></span><i>·</i><span>发布 <b>{displayNumber(statistics.published_post_count)}</b></span></p>
      <p><span>风险帖子 <b className={Number(statistics.risk_published_post_count || 0) > 0 ? "is-risk" : ""}>{displayNumber(statistics.risk_published_post_count)}</b></span><i>·</i><span>评论涉及帖子 <b>{displayNumber(statistics.commented_post_count)}</b></span><i>·</i><span>评论对象 <b>{displayNumber(statistics.commented_post_author_count)}</b></span></p>
    </div>
  );
}

function AccountOverviewContent({ reportVersionId, entryRef, onBack, backLabel = "返回评论账号索引" }: {
  reportVersionId: string;
  entryRef: string;
  onBack?: () => void;
  backLabel?: string;
}) {
  const [detail, setDetail] = useState<ReportAccountDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setDetail(null);
    setError("");
    void fetchReportAccountDetail(reportVersionId, entryRef)
      .then((value) => { if (active) setDetail(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "账号信息加载失败"); });
    return () => { active = false; };
  }, [entryRef, reportVersionId]);
  return (
    <>
      {onBack ? (
        <button type="button" className="r31-drawer-back" onClick={onBack}>
          <ArrowLeft size={15} /> {backLabel}
        </button>
      ) : null}
      <AsyncState loading={!detail && !error} error={error} />
      {detail ? (
        <div className="r31-account-overview-drawer">
          <div className="r31-account-identity"><UserRound size={20} /><div><strong>{detail.entry.display_name}</strong></div></div>
          <section className="r31-drawer-section">
            <div className="r31-drawer-section-title"><h3>本次调查</h3><span>当前发布报告</span></div>
            <AccountMetricGrid statistics={detail.current_report.statistics} includesPublishing className="is-drawer" />
          </section>
          <section className="r31-drawer-section">
            <div className="r31-drawer-section-title"><h3>全部已授权调查</h3>{detail.authorized_investigations.status === "available" ? <span>出现于 {displayNumber(detail.authorized_investigations.investigation_count)} 项调查</span> : null}</div>
            {detail.authorized_investigations.status === "available" ? (
              <>
                <AccountMetricGrid statistics={detail.authorized_investigations.statistics} includesPublishing className="is-drawer" />
                {detail.authorized_investigations.single_investigation_message
                  ? <p className="r31-single-investigation-note">{detail.authorized_investigations.single_investigation_message}</p>
                  : null}
              </>
            ) : <p className="r31-availability-note">{detail.authorized_investigations.unavailable_message}</p>}
          </section>
          <section className="r31-drawer-section">
            <div className="r31-drawer-section-title"><h3>活跃情况</h3></div>
            {detail.activity.status === "available" ? (
              <dl className="r31-account-time-range is-activity">
                {detail.activity.earliest_activity_at ? <div><dt>最早活动</dt><dd>{displayTime(detail.activity.earliest_activity_at)}</dd></div> : null}
                {detail.activity.latest_activity_at ? <div><dt>最近活动</dt><dd>{displayTime(detail.activity.latest_activity_at)}</dd></div> : null}
                {detail.activity.latest_comment_at ? <div><dt>最近评论时间</dt><dd>{displayTime(detail.activity.latest_comment_at)}</dd></div> : null}
                {detail.activity.latest_published_at ? <div><dt>最近发布时间</dt><dd>{displayTime(detail.activity.latest_published_at)}</dd></div> : null}
              </dl>
            ) : <p className="r31-availability-note">{detail.activity.unavailable_message}</p>}
          </section>
          <section className="r31-drawer-section">
            <div className="r31-drawer-section-title"><h3>相关调查分布</h3></div>
            {detail.authorized_investigations.status === "available" ? (
              <div className="r31-investigation-distribution">
                {detail.investigation_distribution.map((item) => (
                  <article key={`${item.investigation_name}-${item.is_current_report ? "current" : "authorized"}`}>
                    <header><strong>{item.investigation_name}</strong>{item.is_current_report ? <span>当前报告</span> : null}</header>
                    <DistributionMetrics statistics={item.statistics} />
                  </article>
                ))}
              </div>
            ) : <p className="r31-availability-note">当前授权调查活动暂时不可用。</p>}
          </section>
          <section className="r31-drawer-section">
            <div className="r31-drawer-section-title"><h3>主要评论对象</h3></div>
            {detail.authorized_investigations.status !== "available" ? (
              <p className="r31-availability-note">当前授权调查活动暂时不可用。</p>
            ) : detail.primary_comment_targets.length ? (
              <div className="r31-comment-targets" role="table" aria-label="主要评论对象">
                <div className="r31-comment-targets-head" role="row">
                  <span role="columnheader">评论对象</span>
                  <span role="columnheader">评论数</span>
                  <span role="columnheader">涉及帖子数</span>
                </div>
                {detail.primary_comment_targets.map((item) => (
                  <div key={item.target_ref} role="row">
                    <strong role="cell" title={item.display_name}>{item.display_name}</strong>
                    <span role="cell">{displayNumber(item.comment_count)}</span>
                    <span role="cell">{displayNumber(item.commented_post_count)}</span>
                  </div>
                ))}
              </div>
            ) : <p className="r31-empty-line">当前授权范围内没有评论对象记录。</p>}
          </section>
        </div>
      ) : null}
    </>
  );
}

function AccountDetailDrawer({ reportVersionId, entryRef, onClose }: { reportVersionId: string; entryRef: string; onClose: () => void }) {
  return (
    <DrawerShell title="账号活动概览" onClose={onClose} className="r31-account-detail-drawer">
      <AccountOverviewContent reportVersionId={reportVersionId} entryRef={entryRef} />
    </DrawerShell>
  );
}

const accountSortOptions: Record<ReportAccountFilter, Array<{ value: ReportAccountSort; label: string }>> = {
  post_author: [
    { value: "published_post_count", label: "发布帖子数量优先" },
    { value: "risk_published_post_count", label: "风险帖子数量优先" },
    { value: "latest_activity", label: "最近活动优先" }
  ],
  cross_investigation_commenter: [
    { value: "investigation_count", label: "调查数量优先" },
    { value: "comment_count", label: "本次评论数量优先" },
    { value: "risk_comment_count", label: "本次风险评论优先" },
    { value: "latest_activity", label: "最近活动优先" }
  ],
  risk_commenter: [
    { value: "risk_comment_count", label: "风险评论数量优先" },
    { value: "comment_count", label: "评论数量优先" },
    { value: "commented_post_count", label: "涉及帖子数量优先" },
    { value: "latest_activity", label: "最近活动优先" }
  ]
};

function defaultAccountSort(filter: ReportAccountFilter): ReportAccountSort {
  return accountSortOptions[filter][0].value;
}

function AccountIndexDrawer({ reportVersionId, filter, onClose }: {
  reportVersionId: string;
  filter: ReportAccountFilter;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<ReportAccountFilter>(filter);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ReportAccountSort>(defaultAccountSort(filter));
  const [page, setPage] = useState<ReportAccountIndexPage | null>(null);
  const [filterCounts, setFilterCounts] = useState<ReportAccountIndexPage["filter_counts"] | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [selectedEntryRef, setSelectedEntryRef] = useState("");
  const [error, setError] = useState("");
  const listScrollRef = useRef<HTMLDivElement>(null);
  const savedListScrollTop = useRef(0);
  const shouldRestoreListScroll = useRef(false);
  const requestCursor = cursorHistory[pageIndex];

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      const normalized = searchInput.trim();
      if (normalized !== search) {
        setSearch(normalized);
        setCursorHistory([null]);
        setPageIndex(0);
      }
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [search, searchInput]);

  useEffect(() => {
    let active = true;
    setPage(null);
    setError("");
    void fetchReportAccounts(reportVersionId, {
      filter: mode,
      search: search || undefined,
      sort,
      limit: 20,
      cursor: requestCursor || undefined
    })
      .then((value) => {
        if (active) {
          setPage(value);
          setFilterCounts(value.filter_counts);
        }
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "账号索引加载失败"); });
    return () => { active = false; };
  }, [mode, reportVersionId, requestCursor, search, sort]);

  useLayoutEffect(() => {
    if (!selectedEntryRef && shouldRestoreListScroll.current && listScrollRef.current) {
      listScrollRef.current.scrollTop = savedListScrollTop.current;
      shouldRestoreListScroll.current = false;
    }
  }, [selectedEntryRef]);

  const changeMode = (nextMode: ReportAccountFilter) => {
    if (nextMode === mode) return;
    setMode(nextMode);
    setSort(defaultAccountSort(nextMode));
    setCursorHistory([null]);
    setPageIndex(0);
  };
  const changeSort = (nextSort: ReportAccountSort) => {
    setSort(nextSort);
    setCursorHistory([null]);
    setPageIndex(0);
  };
  const openOverview = (entryRef: string) => {
    savedListScrollTop.current = listScrollRef.current?.scrollTop || 0;
    setSelectedEntryRef(entryRef);
  };
  const returnToIndex = () => {
    shouldRestoreListScroll.current = true;
    setSelectedEntryRef("");
  };
  const nextPage = () => {
    if (!page?.next_cursor) return;
    setCursorHistory((current) => [
      ...current.slice(0, pageIndex + 1),
      page.next_cursor
    ]);
    setPageIndex((current) => current + 1);
  };
  const previousPage = () => setPageIndex((current) => Math.max(0, current - 1));
  const tableMode: AccountTableMode = mode === "post_author" ? "publishing" : mode === "risk_commenter" ? "risk" : "cross";
  const modeCount = (value: ReportAccountFilter) => {
    const count = filterCounts?.[value];
    return count?.status === "available" ? ` ${displayNumber(count.total_count)}` : "";
  };

  return (
    <DrawerShell
      title={selectedEntryRef ? "账号活动概览" : mode === "post_author" ? "发布账号索引" : "评论账号索引"}
      onClose={onClose}
      className={`r31-account-index-drawer ${selectedEntryRef ? "is-overview" : ""}`}
    >
      {selectedEntryRef ? (
        <AccountOverviewContent
          reportVersionId={reportVersionId}
          entryRef={selectedEntryRef}
          onBack={returnToIndex}
          backLabel={mode === "post_author" ? "返回发布账号索引" : "返回评论账号索引"}
        />
      ) : (
        <div className="r31-account-index-shell">
          <div className="r31-account-index-controls">
            {mode !== "post_author" ? <div className="r31-account-segments" role="group" aria-label="评论账号筛选">
              <button type="button" className={mode === "cross_investigation_commenter" ? "is-active" : ""} onClick={() => changeMode("cross_investigation_commenter")}>跨调查评论账号{modeCount("cross_investigation_commenter")}</button>
              <button type="button" className={mode === "risk_commenter" ? "is-active" : ""} onClick={() => changeMode("risk_commenter")}>风险评论账号{modeCount("risk_commenter")}</button>
            </div> : null}
            <p className="r31-account-index-basis">{mode === "cross_investigation_commenter" ? "基于当前可访问调查" : "基于本次发布报告"}</p>
            <div className="r31-account-index-toolbar">
              <label className="r31-account-search">
                <Search size={15} aria-hidden="true" />
                <input type="search" aria-label="搜索账号显示名" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索账号显示名" />
              </label>
              <label className="r31-account-sort">
                <select aria-label="账号排序方式" value={sort} onChange={(event) => changeSort(event.target.value as ReportAccountSort)}>
                  {accountSortOptions[mode].map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
              <span className="r31-index-count">当前结果 {displayNumber(page?.total_count)}</span>
            </div>
          </div>
          <div ref={listScrollRef} className="r31-account-index-scroll">
            <AsyncState loading={!page && !error} error={error} />
            {page?.status === "unavailable" ? <p className="r31-availability-note">{page.unavailable_message}</p> : null}
            {page?.status === "available" && page.entries.length ? (
              <AccountTable entries={page.entries} mode={tableMode} onOpen={openOverview} />
            ) : null}
            {page?.status === "available" && page.entries.length === 0 ? <p className="r31-empty-line">当前筛选没有符合条件的账号。</p> : null}
          </div>
          <div className="r31-account-pagination" aria-label="账号索引分页">
            <button type="button" disabled={pageIndex === 0 || !page} onClick={previousPage}><ChevronLeft size={14} />上一页</button>
            <span>第 {displayNumber(pageIndex + 1)} 页</span>
            <button type="button" disabled={!page?.has_more || !page.next_cursor} onClick={nextPage}>下一页<ChevronRight size={14} /></button>
          </div>
        </div>
      )}
    </DrawerShell>
  );
}

function EvidenceDrawer({ reportVersionId, findingRef, onClose, onViewAll }: { reportVersionId: string; findingRef: string; onClose: () => void; onViewAll: () => void }) {
  const [detail, setDetail] = useState<ReportFindingEvidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void fetchReportFindingEvidence(reportVersionId, findingRef)
      .then((value) => { if (active) setDetail(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "研判依据加载失败"); });
    return () => { active = false; };
  }, [findingRef, reportVersionId]);
  return (
    <DrawerShell title="研判依据" onClose={onClose}>
      <AsyncState loading={!detail && !error} error={error} />
      {detail ? (
        <>
          <p className="r31-evidence-heading">{detail.title}</p>
          <p className="r31-evidence-count">研判依据 {detail.direct_evidence_count} 条</p>
          <EvidenceList items={detail.items} />
          <button type="button" className="r31-load-more" onClick={onViewAll}>在附录中查看全部研判依据</button>
        </>
      ) : null}
    </DrawerShell>
  );
}

function EvidenceList({ items }: { items: ReportEvidencePresentation[] }) {
  return <div className="r31-evidence-list">{items.map((item) => (
    <article key={item.evidence_ref}>
      <div><span>直接依据</span><small>{item.evidence_type || "审核材料"}</small></div>
      <p>{item.summary}</p>
      {item.original_text ? <details><summary>查看原文</summary><pre>{item.original_text}</pre></details> : null}
      {item.translated_text ? <details><summary>查看译文</summary><pre>{item.translated_text}</pre></details> : null}
    </article>
  ))}</div>;
}

function PostDetailDrawer({ reportVersionId, postRef, onClose }: { reportVersionId: string; postRef: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ReportPostDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void fetchReportPostDetail(reportVersionId, postRef)
      .then((value) => { if (active) setDetail(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "帖子详情加载失败"); });
    return () => { active = false; };
  }, [postRef, reportVersionId]);
  return (
    <DrawerShell title="帖子详情" onClose={onClose}>
      <AsyncState loading={!detail && !error} error={error} />
      {detail ? (
        <>
          <div className="r31-post-detail-head"><div><h3>{detail.title}</h3>{detail.author_display_name ? <span>作者：{detail.author_display_name}</span> : null}</div><RiskTag risk={detail.risk_level} /></div>
          <ReportPostSourceDetails detail={detail} reportVersionId={reportVersionId} />
          {detail.content_summary ? <section className="r31-drawer-section"><h3>内容摘要</h3><p>{detail.content_summary}</p></section> : null}
          <section className="r31-drawer-section"><h3>审核结论</h3><p>{decisionLabels[detail.decision] ? `${decisionLabels[detail.decision]} · ` : ""}{riskLabels[detail.risk_level] || "未分级"}</p><p>{detail.audit_summary || "原审核结果未提供文字说明。"}</p></section>
          {detail.direct_evidence.length ? <section className="r31-drawer-section"><h3>直接研判依据</h3><EvidenceList items={detail.direct_evidence} /></section> : null}
        </>
      ) : null}
    </DrawerShell>
  );
}

export function InvestigationReportPage() {
  const { investigationId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [reportVersionId, setReportVersionId] = useState("");
  const [report, setReport] = useState<ReportPresentationProjection | null>(null);
  const [error, setError] = useState("");
  const [drawer, setDrawer] = useState<DrawerState>(null);
  const [tocOpen, setTocOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const requestedSection = searchParams.get("section") || "";
  const explicitReportVersion = searchParams.get("report") || "";

  useEffect(() => {
    let active = true;
    setReport(null);
    setError("");
    void resolvePublishedReportVersion(investigationId, explicitReportVersion)
      .then(async (version) => ({ version, projection: await fetchReportPresentation(version) }))
      .then(({ version, projection }) => {
        if (!active) return;
        setReportVersionId(version);
        setReport(projection);
        setActiveSection(projection.ordered_sections[0]?.section_ref || "");
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "调查报告加载失败");
      });
    return () => { active = false; };
  }, [explicitReportVersion, investigationId]);

  useEffect(() => {
    if (!report) return;
    const previousTitle = document.title;
    document.title = report.report_metadata.title;
    return () => { document.title = previousTitle; };
  }, [report]);

  useEffect(() => {
    if (!report) return;
    const targets = report.ordered_sections
      .map((section) => document.getElementById(sectionDomId(section.section_ref)))
      .filter((item): item is HTMLElement => Boolean(item));
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top)[0];
      if (visible) setActiveSection(visible.target.id.replace(/^report-/, ""));
    }, { rootMargin: "-18% 0px -68% 0px", threshold: 0 });
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, [report]);

  useEffect(() => {
    if (!report || !requestedSection) return;
    const target = document.getElementById(sectionDomId(requestedSection));
    if (target) window.requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
  }, [report, requestedSection]);

  const rootSections = useMemo(() => report?.ordered_sections || [], [report]);
  const scrollToSection = (sectionRef: string) => {
    document.getElementById(sectionDomId(sectionRef))?.scrollIntoView({ behavior: "smooth", block: "start" });
    setTocOpen(false);
  };
  const openAppendix = (query: Record<string, string> = {}) => {
    const params = new URLSearchParams({ report: reportVersionId, ...query });
    navigate(`/investigation/${encodeURIComponent(investigationId)}/report/evidence?${params.toString()}`);
  };

  if (error) {
    return <main className="ethnic-report-page"><div className="r31-report-load-state is-error" role="alert"><FileSearch size={22} /><strong>调查报告暂不可用</strong><p>{error}</p><button type="button" onClick={() => navigate(`/investigation/${encodeURIComponent(investigationId)}`)}>返回调查会话</button></div></main>;
  }
  if (!report) {
    return <main className="ethnic-report-page"><div className="r31-report-load-state" role="status">正在加载已发布报告...</div></main>;
  }

  return (
    <main className="ethnic-report-page r31-report-page">
      <header className="ethnic-report-toolbar">
        <button type="button" className="ethnic-report-back" aria-label="返回调查会话" title="返回调查会话" onClick={() => navigate(`/investigation/${encodeURIComponent(investigationId)}`)}><ArrowLeft size={17} /><span>返回调查会话</span></button>
        <div className="ethnic-report-toolbar-title"><span>{report.report_metadata.source_name}</span><strong>{report.report_metadata.title}</strong></div>
        <div className="r31-toolbar-actions">
          <button type="button" className="r31-toc-toggle" aria-label="打开报告目录" title="打开报告目录" onClick={() => setTocOpen(true)}><List size={16} /><span>目录</span></button>
          <button type="button" className="ethnic-report-export" aria-label="导出报告" title="导出报告" onClick={() => window.print()}><Download size={16} /><span>导出报告</span></button>
        </div>
      </header>

      <div className="ethnic-report-layout">
        {tocOpen ? <button type="button" className="r31-toc-backdrop" aria-label="关闭目录" onClick={() => setTocOpen(false)} /> : null}
        <nav className={`ethnic-report-toc r31-report-toc ${tocOpen ? "is-open" : ""}`} aria-label="报告目录">
          <div className="r31-toc-heading"><span>报告目录</span><button type="button" onClick={() => setTocOpen(false)} aria-label="关闭目录"><X size={17} /></button></div>
          {report.ordered_sections.map((section) => (
            <button
              key={section.section_ref}
              type="button"
              className={`${activeSection === section.section_ref ? "is-active" : ""} ${section.parent_section_ref ? "is-child" : ""}`}
              aria-current={activeSection === section.section_ref ? "location" : undefined}
              onClick={() => scrollToSection(section.section_ref)}
            >
              <span>{section.section_number}</span>{section.title}
            </button>
          ))}
          <button type="button" className="r31-toc-appendix" onClick={() => openAppendix()}><span>附录</span>报告附录</button>
        </nav>

        <article className="ethnic-report-paper r31-report-paper">
          <header className="ethnic-report-cover r31-report-cover">
            <p>调查报告</p>
            <h1>{report.report_metadata.title}</h1>
            <div className="ethnic-report-title-rule" />
            <dl>
              <div><dt>来源调查</dt><dd>{report.report_metadata.source_name}</dd></div>
              <div><dt>报告状态</dt><dd>{report.report_metadata.status === "published" ? "已发布" : report.report_metadata.status}</dd></div>
              <div><dt>发布时间</dt><dd>{displayTime(report.report_metadata.published_at)}</dd></div>
              {report.report_metadata.platform.status === "available" ? <div><dt>调查平台</dt><dd>{report.report_metadata.platform.label}</dd></div> : null}
            </dl>
          </header>

          {rootSections.map((section) => (
            <ReportSection
              key={section.section_ref}
              section={section}
              report={report}
              onOpenAccount={(ref) => setDrawer({ type: "account", ref })}
              onOpenAccountIndex={(filter) => setDrawer({ type: "account-index", filter })}
              onOpenEvidence={(ref) => setDrawer({ type: "evidence", ref })}
              onOpenPost={(ref) => setDrawer({ type: "post", ref })}
              onOpenAppendix={openAppendix}
            />
          ))}

          <footer className="ethnic-report-footer"><span>{report.report_metadata.title}</span><span>已发布调查报告</span></footer>
        </article>
      </div>

      {drawer?.type === "account" ? <AccountDetailDrawer reportVersionId={reportVersionId} entryRef={drawer.ref} onClose={() => setDrawer(null)} /> : null}
      {drawer?.type === "account-index" ? <AccountIndexDrawer reportVersionId={reportVersionId} filter={drawer.filter} onClose={() => setDrawer(null)} /> : null}
      {drawer?.type === "evidence" ? <EvidenceDrawer reportVersionId={reportVersionId} findingRef={drawer.ref} onClose={() => setDrawer(null)} onViewAll={() => { setDrawer(null); openAppendix({ view: "evidence", finding_ref: drawer.ref }); }} /> : null}
      {drawer?.type === "post" ? <PostDetailDrawer reportVersionId={reportVersionId} postRef={drawer.ref} onClose={() => setDrawer(null)} /> : null}
    </main>
  );
}
