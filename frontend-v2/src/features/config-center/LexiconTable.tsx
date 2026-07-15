import type { RiskLexicon } from "../../types/configCenter";
import { LexiconTableRow } from "./LexiconTableRow";

interface LexiconTableProps {
  lexicons: RiskLexicon[];
  openMenuId: string | null;
  onMenuChange: (lexiconId: string | null) => void;
  onView: (lexicon: RiskLexicon) => void;
  onEdit: (lexicon: RiskLexicon) => void;
  onCopy: (lexicon: RiskLexicon) => void;
  onViewHistory: (lexicon: RiskLexicon) => void;
  onDelete: (lexicon: RiskLexicon) => void;
}

export function LexiconTable({
  lexicons,
  openMenuId,
  onMenuChange,
  onView,
  onEdit,
  onCopy,
  onViewHistory,
  onDelete
}: LexiconTableProps) {
  return (
    <div className="config-table lexicon-table" role="table" aria-label="黑话库列表">
      <div className="config-table-head lexicon-table-head" role="row">
        <span>风险分类</span>
        <span>词库名称</span>
        <span>词条数</span>
        <span>平台搜索词</span>
        <span>平台标签</span>
        <span>被方案引用</span>
        <span>最近更新</span>
        <span>操作</span>
      </div>
      {lexicons.map((lexicon) => (
        <LexiconTableRow
          key={lexicon.id}
          lexicon={lexicon}
          menuOpen={openMenuId === lexicon.id}
          onMenuToggle={() => onMenuChange(openMenuId === lexicon.id ? null : lexicon.id)}
          onMenuClose={() => onMenuChange(null)}
          onView={onView}
          onEdit={onEdit}
          onCopy={onCopy}
          onViewHistory={onViewHistory}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
