import type { InvestigationTurnStage } from "../../types/investigations";

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
  let formatted = value
    .replace(/\bInvestigationFinding\b/g, "调查发现")
    .replace(/\bdirect Evidence\b/gi, "直接证据")
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
