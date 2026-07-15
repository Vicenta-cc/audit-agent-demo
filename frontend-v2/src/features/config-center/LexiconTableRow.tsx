import { BookOpen, ChevronDown, Copy, History, Tags, Trash2 } from "lucide-react";
import { Button } from "../../components/common/Button";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import { formatCompactDateTime, formatNumber } from "../../services/configCenter";
import type { RiskLexicon } from "../../types/configCenter";

interface LexiconTableRowProps {
  lexicon: RiskLexicon;
  menuOpen: boolean;
  onMenuToggle: () => void;
  onMenuClose: () => void;
  onView: (lexicon: RiskLexicon) => void;
  onEdit: (lexicon: RiskLexicon) => void;
  onCopy: (lexicon: RiskLexicon) => void;
  onViewHistory: (lexicon: RiskLexicon) => void;
  onDelete: (lexicon: RiskLexicon) => void;
}

export function LexiconTableRow({
  lexicon,
  menuOpen,
  onMenuToggle,
  onMenuClose,
  onView,
  onEdit,
  onCopy,
  onViewHistory,
  onDelete
}: LexiconTableRowProps) {
  return (
    <div className="config-table-row lexicon-table-row" role="row">
      <span className="category-pill">{lexicon.category}</span>
      <button className="lexicon-name-cell" type="button" onClick={() => onView(lexicon)}>
        <span className="lexicon-icon" aria-hidden="true">
          <BookOpen size={18} />
        </span>
        <span className="lexicon-name-text">
          <strong>{lexicon.name}</strong>
          <span>ID：{lexicon.id}</span>
          <small>{lexicon.keywords.slice(0, 4).join("、") || "暂无词条预览"}</small>
        </span>
      </button>
      <span className="config-count">{formatNumber(lexicon.entryCount)} 个</span>
      <span className="config-count" title={lexicon.platformSearchWords.join("、")}>
        {formatNumber(lexicon.platformSearchWordCount)} 个
      </span>
      <span className="config-count" title={lexicon.platformTags.join("、")}>
        {formatNumber(lexicon.platformTagCount)} 个
      </span>
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
          <button type="button" onClick={() => onCopy(lexicon)}>
            <Copy size={15} />
            复制黑话库
          </button>
          <button type="button" onClick={() => onViewHistory(lexicon)}>
            <History size={15} />
            查看版本历史
          </button>
          <button type="button" onClick={() => onEdit(lexicon)}>
            <Tags size={15} />
            管理词条
          </button>
          <button className="is-danger" type="button" onClick={() => onDelete(lexicon)}>
            <Trash2 size={15} />
            删除黑话库
          </button>
        </DropdownMenu>
      </div>
    </div>
  );
}
