import type { RiskLexicon } from "../../types/configCenter";
import { LexiconTableRow } from "./LexiconTableRow";

interface LexiconTableProps {
  lexicons: RiskLexicon[];
  onView: (lexicon: RiskLexicon) => void;
  onEdit: (lexicon: RiskLexicon) => void;
  onDelete: (lexicon: RiskLexicon) => void;
}

export function LexiconTable({ lexicons, onView, onEdit, onDelete }: LexiconTableProps) {
  return (
    <div className="config-table lexicon-table" role="table" aria-label="黑话库列表">
      <div className="config-table-head lexicon-table-head" role="row">
        <span>词库名称</span>
        <span>词条数</span>
        <span>被方案引用</span>
        <span>最近更新</span>
        <span>操作</span>
      </div>
      {lexicons.map((lexicon) => (
        <LexiconTableRow
          key={lexicon.id}
          lexicon={lexicon}
          onView={onView}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
