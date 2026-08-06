import {
  Image as ImageIcon,
  UserRound
} from "lucide-react";
import { useState } from "react";
import { Button } from "../../components/common/Button";
import type { TaskOutputItem } from "../../types/taskOutputs";

interface OutputListItemProps {
  item: TaskOutputItem;
  onViewEvidence: (item: TaskOutputItem) => void;
}

export function OutputListItem({
  item,
  onViewEvidence
}: OutputListItemProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const evidence = item.evidenceCounts;

  return (
    <article className={`output-list-item risk-${riskTone(item.riskLevel)}`}>
      <div className="output-card-top">
        <div className="output-title-group">
          <div className={`risk-level-tag is-${riskTone(item.riskLevel)}`}>{item.riskLevel}</div>
          <h3 title={item.title}>{item.title}</h3>
        </div>
        <strong className="output-item-number">{item.number}</strong>
      </div>

      <div className="output-card-body">
        <div className="output-card-copy">
          {item.summary ? <p title={item.summary}>{item.summary}</p> : null}
          {item.riskLibrary ? (
            <div className="output-library-line">
              <span>命中风险库：</span>
              <strong>{item.riskLibrary.label}</strong>
            </div>
          ) : null}
        </div>
        <div className="output-thumbnail">
          {item.thumbnailUrl && !imageFailed ? (
            <img src={item.thumbnailUrl} alt="内容缩略图" loading="lazy" onError={() => setImageFailed(true)} />
          ) : (
            <div className="output-thumbnail-placeholder" aria-label="暂无内容缩略图">
              <ImageIcon size={34} strokeWidth={1.7} />
              <span>内容缩略图</span>
            </div>
          )}
        </div>
      </div>

      <div className="output-card-bottom">
        <div className="output-evidence-counts" aria-label="命中证据数量">
          <span>文本 {evidence.text}</span>
          <span>OCR {evidence.ocr}</span>
          <span>ASR {evidence.asr}</span>
          <span>视觉 {evidence.visual}</span>
          <span>评论 {evidence.comments}</span>
        </div>
        <div className="output-card-footer">
          <div className="output-author-time">
            <UserRound size={18} strokeWidth={2} aria-hidden="true" />
            <span>作者：{item.author}</span>
            <span aria-hidden="true">·</span>
            <span>{item.time}</span>
          </div>
          <Button type="button" variant="primary" size="small" onClick={() => onViewEvidence(item)}>
            查看证据
          </Button>
        </div>
      </div>
    </article>
  );
}

function riskTone(level: TaskOutputItem["riskLevel"]) {
  if (level === "高危") return "high";
  if (level === "中危") return "medium";
  if (level === "待复核") return "review";
  return "safe";
}
