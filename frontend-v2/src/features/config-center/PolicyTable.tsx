import type { ResearchPolicy } from "../../types/configCenter";
import { PolicyTableRow } from "./PolicyTableRow";

interface PolicyTableProps {
  policies: ResearchPolicy[];
  onOpenDetail: (policy: ResearchPolicy) => void;
  onEdit: (policy: ResearchPolicy) => void;
  onDelete: (policy: ResearchPolicy) => void;
}

export function PolicyTable({ policies, onOpenDetail, onEdit, onDelete }: PolicyTableProps) {
  return (
    <div className="config-table policy-table" role="table" aria-label="研判方案列表">
      <div className="config-table-head policy-table-head" role="row">
        <span>方案名称</span>
        <span>知识库</span>
        <span>检测配置</span>
        <span>引用任务</span>
        <span>最近更新</span>
        <span>操作</span>
      </div>
      {policies.map((policy) => (
        <PolicyTableRow
          key={policy.id}
          policy={policy}
          onOpenDetail={onOpenDetail}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
