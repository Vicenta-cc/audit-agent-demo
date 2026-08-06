import type { MouseEvent } from "react";
import { FileText, Trash2 } from "lucide-react";
import { IconButton } from "../../components/common/IconButton";
import { formatCompactDateTime } from "../../services/configCenter";
import type { ResearchPolicy } from "../../types/configCenter";

interface PolicyTableRowProps {
  policy: ResearchPolicy;
  onOpenDetail: (policy: ResearchPolicy) => void;
  onEdit: (policy: ResearchPolicy) => void;
  onDelete: (policy: ResearchPolicy) => void;
}

export function PolicyTableRow({ policy, onOpenDetail, onEdit, onDelete }: PolicyTableRowProps) {
  const enabledDetections = policy.detectionConfigs.filter((item) => item.enabled);
  const stop = (event: MouseEvent) => event.stopPropagation();

  return (
    <div className="config-table-row policy-table-row" role="row" tabIndex={0} onClick={() => onOpenDetail(policy)}>
      <button className="policy-name-cell" type="button" onClick={() => onOpenDetail(policy)}>
        <span className="policy-icon" aria-hidden="true">
          <FileText size={18} />
        </span>
        <span className="policy-name-text">
          <strong>{policy.name}</strong>
          <small>{policy.description || "暂无方案说明"}</small>
        </span>
      </button>

      <span className="config-count" title={policy.lexiconNames.join("、")}>
        {policy.lexiconNames.length ? policy.lexiconNames.join("、") : "未绑定"}
      </span>
      <span className="config-count" title={enabledDetections.map((item) => item.location).join("、")}>
        {enabledDetections.length} 项
      </span>
      <span className="config-count" title={policy.references.map((item) => item.name).join("、")}>
        {policy.references.length} 个
      </span>
      <span className="config-table-text">{formatCompactDateTime(policy.updatedAt)}</span>

      <div className="config-row-actions" onClick={stop}>
        <button className="config-text-button" type="button" onClick={() => onOpenDetail(policy)}>
          查看
        </button>
        <button className="config-text-button" type="button" onClick={() => onEdit(policy)}>
          编辑
        </button>
        <IconButton type="button" aria-label={`删除方案${policy.name}`} onClick={() => onDelete(policy)}>
          <Trash2 size={16} />
        </IconButton>
      </div>
    </div>
  );
}
