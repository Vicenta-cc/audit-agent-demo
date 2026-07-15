import type { MouseEvent } from "react";
import { ChevronDown, Copy, FileText, History, Power, Trash2 } from "lucide-react";
import { Button } from "../../components/common/Button";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import { StatusTag } from "../../components/common/StatusTag";
import { formatCompactDateTime, getPolicyStatusLabel, getPolicyStatusTone } from "../../services/configCenter";
import type { ResearchPolicy } from "../../types/configCenter";

interface PolicyTableRowProps {
  policy: ResearchPolicy;
  menuOpen: boolean;
  onMenuToggle: () => void;
  onMenuClose: () => void;
  onOpenDetail: (policy: ResearchPolicy) => void;
  onEdit: (policy: ResearchPolicy) => void;
  onCopy: (policy: ResearchPolicy) => void;
  onToggleStatus: (policy: ResearchPolicy) => void;
  onViewHistory: (policy: ResearchPolicy) => void;
  onDelete: (policy: ResearchPolicy) => void;
}

export function PolicyTableRow({
  policy,
  menuOpen,
  onMenuToggle,
  onMenuClose,
  onOpenDetail,
  onEdit,
  onCopy,
  onToggleStatus,
  onViewHistory,
  onDelete
}: PolicyTableRowProps) {
  const stop = (event: MouseEvent) => event.stopPropagation();

  return (
    <div className="config-table-row policy-table-row" role="row" tabIndex={0} onClick={() => onOpenDetail(policy)}>
      <button className="policy-name-cell" type="button" onClick={() => onOpenDetail(policy)}>
        <span className="policy-icon" aria-hidden="true">
          <FileText size={18} />
        </span>
        <span className="policy-name-text">
          <strong>{policy.name}</strong>
          <span>ID：{policy.id}</span>
          <small>{policy.scenarioTags.join("、")}</small>
        </span>
      </button>

      <span className="config-table-text">{policy.category}</span>
      <StatusTag tone={getPolicyStatusTone(policy.status)}>{getPolicyStatusLabel(policy.status)}</StatusTag>
      <span className="config-count" title={policy.lexiconNames.join("、")}>
        {policy.lexiconNames.length} 个
      </span>
      <span className="config-count" title={policy.contentScopes.join("、")}>
        {policy.contentScopes.length} 项
      </span>
      <span className="config-count" title={policy.recognitionCapabilities.join("、")}>
        {policy.recognitionCapabilities.length} 项
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
        <DropdownMenu
          open={menuOpen}
          onClose={onMenuClose}
          trigger={
            <Button type="button" variant="ghost" size="small" onClick={onMenuToggle}>
              更多
              <ChevronDown size={15} />
            </Button>
          }
        >
          <button type="button" onClick={() => onCopy(policy)}>
            <Copy size={15} />
            复制方案
          </button>
          <button type="button" onClick={() => onToggleStatus(policy)}>
            <Power size={15} />
            {policy.status === "published" ? "停用方案" : "发布方案"}
          </button>
          <button type="button" onClick={() => onViewHistory(policy)}>
            <History size={15} />
            查看版本历史
          </button>
          <button className="is-danger" type="button" onClick={() => onDelete(policy)}>
            <Trash2 size={15} />
            删除方案
          </button>
        </DropdownMenu>
      </div>
    </div>
  );
}
