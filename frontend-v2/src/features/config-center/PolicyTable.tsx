import type { ResearchPolicy } from "../../types/configCenter";
import { PolicyTableRow } from "./PolicyTableRow";

interface PolicyTableProps {
  policies: ResearchPolicy[];
  openMenuId: string | null;
  onMenuChange: (policyId: string | null) => void;
  onOpenDetail: (policy: ResearchPolicy) => void;
  onEdit: (policy: ResearchPolicy) => void;
  onCopy: (policy: ResearchPolicy) => void;
  onToggleStatus: (policy: ResearchPolicy) => void;
  onViewHistory: (policy: ResearchPolicy) => void;
  onDelete: (policy: ResearchPolicy) => void;
}

export function PolicyTable({
  policies,
  openMenuId,
  onMenuChange,
  onOpenDetail,
  onEdit,
  onCopy,
  onToggleStatus,
  onViewHistory,
  onDelete
}: PolicyTableProps) {
  return (
    <div className="config-table policy-table" role="table" aria-label="研判方案列表">
      <div className="config-table-head policy-table-head" role="row">
        <span>方案名称</span>
        <span>风险分类</span>
        <span>状态</span>
        <span>知识库</span>
        <span>内容范围</span>
        <span>识别能力</span>
        <span>引用任务</span>
        <span>最近更新</span>
        <span>操作</span>
      </div>
      {policies.map((policy) => (
        <PolicyTableRow
          key={policy.id}
          policy={policy}
          menuOpen={openMenuId === policy.id}
          onMenuToggle={() => onMenuChange(openMenuId === policy.id ? null : policy.id)}
          onMenuClose={() => onMenuChange(null)}
          onOpenDetail={onOpenDetail}
          onEdit={onEdit}
          onCopy={onCopy}
          onToggleStatus={onToggleStatus}
          onViewHistory={onViewHistory}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
