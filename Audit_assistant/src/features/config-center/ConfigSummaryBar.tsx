import type { LexiconSummary, PolicySummary } from "../../types/configCenter";
import { formatNumber } from "../../services/configCenter";

interface ConfigSummaryBarProps {
  type: "policies" | "lexicons";
  policySummary?: PolicySummary;
  lexiconSummary?: LexiconSummary;
}

export function ConfigSummaryBar({ type, policySummary, lexiconSummary }: ConfigSummaryBarProps) {
  const text = type === "policies"
    ? `共 ${formatNumber(policySummary?.total || 0)} 个研判方案 · 绑定 ${formatNumber(policySummary?.lexiconReferenceCount || 0)} 个黑话库 · 被 ${formatNumber(policySummary?.referencedTaskCount || 0)} 个监控任务引用`
    : `共 ${formatNumber(lexiconSummary?.total || 0)} 个黑话库 · ${formatNumber(lexiconSummary?.entryCount || 0)} 个词条 · 被方案引用 ${formatNumber(lexiconSummary?.policyReferenceCount || 0)} 次`;

  return <div className="config-summary-bar" role="status">{text}</div>;
}
