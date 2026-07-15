import { EllipsisVertical, ExternalLink } from "lucide-react";
import { Button } from "../../components/common/Button";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import type { AuditResult, MonitorTask } from "../../types/jobs";
import {
  formatOutputDate,
  getAuditStatusLabel,
  getAuthorName,
  getConfidence,
  getEvidenceCounts,
  getOutputNumber,
  getOutputTitle,
  getPlatformName,
  getRiskLevelLabel,
  getRiskTypeLabel
} from "./taskOutputUtils";

interface OutputCardProps {
  task: MonitorTask;
  output: AuditResult;
  menuOpen: boolean;
  onMenuToggle: () => void;
  onMenuClose: () => void;
  onViewEvidence: (output: AuditResult) => void;
  onAnalyzeUser: (output: AuditResult) => void;
}

export function OutputCard({
  task,
  output,
  menuOpen,
  onMenuToggle,
  onMenuClose,
  onViewEvidence,
  onAnalyzeUser
}: OutputCardProps) {
  const evidence = getEvidenceCounts(output);
  const status = getAuditStatusLabel(output);
  const riskLevel = getRiskLevelLabel(output);
  const number = getOutputNumber(output);
  const sourceUrl = output.url || "";

  return (
    <article className="output-card">
      <div className="output-card-meta-row">
        <span className="output-card-tag tag-platform">{getPlatformName(output, task)}</span>
        <span className={`output-card-tag tag-risk ${getRiskToneClass(riskLevel)}`}>{getRiskTypeLabel(output)}</span>
        <span className={`output-card-tag tag-review ${getReviewToneClass(status)}`}>{status}</span>
        <span className="output-card-number">{number}</span>
      </div>

      <div className="output-card-main">
        <h3 title={getOutputTitle(output)}>{getOutputTitle(output)}</h3>
        <p title={output.summary || output.primary_risk || ""}>{output.summary || output.primary_risk || "暂无风险摘要"}</p>
      </div>

      <div className="output-card-evidence">
        证据：
        <span>文本 {evidence.text}</span>
        <span>OCR {evidence.ocr}</span>
        <span>ASR {evidence.asr}</span>
        <span>视觉 {evidence.visual}</span>
        <span>评论 {evidence.comments}</span>
      </div>

      <div className="output-card-footer">
        <div className="output-card-footnote">
          <span title={getAuthorName(output)}>作者：{getAuthorName(output)}</span>
          <span>{formatOutputDate(output)}</span>
          <span>置信度 {getConfidence(output)}%</span>
        </div>
        <div className="output-card-actions">
          <Button type="button" variant="secondary" size="small" onClick={() => onViewEvidence(output)}>
            查看证据
          </Button>
          <Button type="button" variant="secondary" size="small" onClick={() => onAnalyzeUser(output)}>
            分析用户
          </Button>
          <DropdownMenu
            open={menuOpen}
            onClose={onMenuClose}
            trigger={
              <button className="output-more-button" type="button" aria-label={`${number} 更多操作`} onClick={onMenuToggle}>
                <EllipsisVertical size={18} />
              </button>
            }
          >
            <button
              type="button"
              onClick={() => {
                onMenuClose();
                void navigator.clipboard?.writeText(number);
              }}
            >
              复制编号
            </button>
            <button
              type="button"
              disabled={!sourceUrl}
              onClick={() => {
                onMenuClose();
                if (sourceUrl) {
                  window.open(sourceUrl, "_blank", "noopener,noreferrer");
                }
              }}
            >
              <ExternalLink size={15} />
              打开原文
            </button>
            <button
              type="button"
              onClick={() => {
                onMenuClose();
                downloadOutput(output, number);
              }}
            >
              下载证据摘要
            </button>
          </DropdownMenu>
        </div>
      </div>
    </article>
  );
}

function getRiskToneClass(riskLevel: string) {
  if (riskLevel === "高危") {
    return "is-danger";
  }
  if (riskLevel === "中危" || riskLevel === "低危") {
    return "is-warning";
  }
  return "is-safe";
}

function getReviewToneClass(status: string) {
  if (status === "待复核") {
    return "is-warning";
  }
  if (status === "已复核") {
    return "is-info";
  }
  return "is-safe";
}

function downloadOutput(output: AuditResult, number: string) {
  const payload = {
    id: number,
    title: getOutputTitle(output),
    summary: output.summary || output.primary_risk || "",
    author: getAuthorName(output),
    evidence: getEvidenceCounts(output)
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${number}-evidence-summary.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
