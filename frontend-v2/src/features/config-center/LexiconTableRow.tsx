import { BookOpen, Trash2 } from "lucide-react";
import { IconButton } from "../../components/common/IconButton";
import { formatCompactDateTime, formatNumber } from "../../services/configCenter";
import type { RiskLexicon } from "../../types/configCenter";

interface LexiconTableRowProps {
  lexicon: RiskLexicon;
  onView: (lexicon: RiskLexicon) => void;
  onEdit: (lexicon: RiskLexicon) => void;
  onDelete: (lexicon: RiskLexicon) => void;
}

export function LexiconTableRow({ lexicon, onView, onEdit, onDelete }: LexiconTableRowProps) {
  return (
    <div className="config-table-row lexicon-table-row" role="row">
      <button className="lexicon-name-cell" type="button" onClick={() => onView(lexicon)}>
        <span className="lexicon-icon" aria-hidden="true">
          <BookOpen size={18} />
        </span>
        <span className="lexicon-name-text">
          <strong>{lexicon.name}</strong>
          <small>{lexicon.keywords.slice(0, 5).join("、") || "暂无词条"}</small>
        </span>
      </button>
      <span className="config-count">{formatNumber(lexicon.entryCount)} 个</span>
      <span className="config-count" title={lexicon.references.map((item) => item.name).join("、")}>
        {lexicon.references.length} 个方案
      </span>
      <span className="config-table-text">{formatCompactDateTime(lexicon.updatedAt)}</span>

      <div className="config-row-actions">
        <button className="config-text-button" type="button" onClick={() => onView(lexicon)}>
          查看
        </button>
        <button className="config-text-button" type="button" onClick={() => onEdit(lexicon)}>
          编辑
        </button>
        <IconButton type="button" aria-label={`删除黑话库${lexicon.name}`} onClick={() => onDelete(lexicon)}>
          <Trash2 size={16} />
        </IconButton>
      </div>
    </div>
  );
}
