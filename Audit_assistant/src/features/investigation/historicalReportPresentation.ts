import type { InvestigationTurnStage } from "../../types/investigations";

// Normalize known adapter vocabulary in assistant prose without exposing query
// implementation names. Report evidence and data sources remain unchanged.
const REPORT_VOCABULARY: Record<string, string> = {
  membership_evidence_subset: "本项调查发现的归类证据子集",
  deterministic_statistics: "统计结果",
  report_statement: "报告综合结论",
  author_caption: "作者配文",
  directory_complete_for_post: "该帖目录已完整返回",
  post_in_current_report_risk_summary: "是否列入当前报告风险汇总",
  text_complete: "完整原文",
  text_preview: "原文预览",
  read_posts: "帖子详情查询",
  list_finding_posts: "关联帖子查询",
  list_post_risk_comments: "风险评论查询",
  list_post_comments: "评论查询",
  read_account_occurrence: "账号活动详情查询"
};
const REPORT_VOCABULARY_PATTERN = new RegExp(`\\b(${Object.keys(REPORT_VOCABULARY).join("|")})\\b`, "g");

export function formatReportVocabulary(value: string) {
  return value.replace(REPORT_VOCABULARY_PATTERN, term => REPORT_VOCABULARY[term]);
}

const DISPLAY_TERMS = {
  none: "无风险",
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  pass: "通过",
  review: "需复核"
} as const;

const ENUM_VALUE_PATTERN = /^(\s*)(none|low|medium|high|pass|review)(\s*[，。；、,.;:：()（）\[\]【】]*)$/i;
const LABELED_ENUM_PATTERN = /((?:风险(?:等级)?|审核(?:结论|状态)?|risk[_ ]?level|decision)\s*(?:[：:=]\s*|\s+))(none|low|medium|high|pass|review)\b/gi;
const ENUM_WITH_CHINESE_PATTERN = /\b(none|low|medium|high|pass|review)\b\s*[（(](?:无风险|低风险|中风险|高风险|通过|需复核)[）)]/gi;
const RISK_ENUM_WITH_SUFFIX_PATTERN = /\b(none|low|medium|high)\b\s*风险/gi;
const ENUM_TOKEN_PATTERN = /\b(none|low|medium|high|pass|review)\b/gi;

export function formatHistoricalReportText(
  value: string,
  options: { tableCell?: boolean; enumContext?: boolean } = {}
) {
  let formatted = formatReportVocabulary(value)
    .replace(/\bInvestigationFinding statement\b/g, "报告综合结论")
    .replace(/\bInvestigationFinding\b/g, "调查发现")
    .replace(/\bdirect Evidence\b/gi, "直接证据")
    .replace(/\bEvidence\b/g, "证据")
    .replace(/\bFinding\b/g, "调查发现")
    .replace(LABELED_ENUM_PATTERN, (_, prefix: string, term: string) => (
      `${prefix}${DISPLAY_TERMS[term.toLowerCase() as keyof typeof DISPLAY_TERMS]}`
    ));

  if (options.tableCell || ENUM_VALUE_PATTERN.test(formatted)) {
    formatted = formatted.replace(
      ENUM_VALUE_PATTERN,
      (_, leading: string, term: string, trailing: string) => (
        `${leading}${DISPLAY_TERMS[term.toLowerCase() as keyof typeof DISPLAY_TERMS]}${trailing}`
      )
    );
  }

  if (options.enumContext) {
    formatted = formatted
      .replace(ENUM_WITH_CHINESE_PATTERN, (_, term: string) => (
        DISPLAY_TERMS[term.toLowerCase() as keyof typeof DISPLAY_TERMS]
      ))
      .replace(RISK_ENUM_WITH_SUFFIX_PATTERN, (_, term: string) => (
        DISPLAY_TERMS[term.toLowerCase() as keyof typeof DISPLAY_TERMS]
      ))
      .replace(ENUM_TOKEN_PATTERN, (term) => (
        DISPLAY_TERMS[term.toLowerCase() as keyof typeof DISPLAY_TERMS]
      ));
  }

  return formatted;
}

export function historicalPendingStatus(
  stage: InvestigationTurnStage | undefined,
  recovering = false
) {
  if (recovering) return "正在恢复上次回答……";
  switch (stage) {
    case "preparing_sources":
      return "正在准备所需资料……";
    case "acquiring_source":
      return "正在查询报告资料……";
    case "answering":
    case "completed":
      return "正在整理回答……";
    case "accepted":
      return "已接收，等待开始回答……";
    case "planning":
    case "interrupted":
    case "failed":
    default:
      return "正在理解你的问题……";
  }
}

export function shouldShowHistoricalPending(
  isPublishedReportSession: boolean,
  isSendingMessage: boolean,
  hasPendingTurn: boolean
) {
  return isPublishedReportSession && (isSendingMessage || hasPendingTurn);
}
