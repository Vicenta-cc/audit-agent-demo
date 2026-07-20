import { RefreshCw, Search } from "lucide-react";
import { IconButton } from "../../components/common/IconButton";
import type { LexiconSortKey, ReferenceFilter } from "../../types/configCenter";

interface LexiconToolbarProps {
  query: string;
  referenceFilter: ReferenceFilter;
  sortKey: LexiconSortKey;
  refreshing: boolean;
  onQueryChange: (value: string) => void;
  onReferenceChange: (value: ReferenceFilter) => void;
  onSortChange: (value: LexiconSortKey) => void;
  onRefresh: () => void;
}

const referenceOptions: ReferenceFilter[] = ["全部", "已被引用", "未被引用"];

const sortOptions: Array<{ value: LexiconSortKey; label: string }> = [
  { value: "updated", label: "最近更新" },
  { value: "entries", label: "词条最多" },
  { value: "references", label: "引用最多" },
  { value: "name", label: "名称排序" }
];

export function LexiconToolbar({
  query,
  referenceFilter,
  sortKey,
  refreshing,
  onQueryChange,
  onReferenceChange,
  onSortChange,
  onRefresh
}: LexiconToolbarProps) {
  return (
    <div className="config-toolbar">
      <label className="config-search">
        <Search size={18} />
        <input
          value={query}
          type="search"
          placeholder="搜索词库名称或词条"
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>

      <label className="config-select">
        <span>引用状态：</span>
        <select value={referenceFilter} onChange={(event) => onReferenceChange(event.target.value as ReferenceFilter)}>
          {referenceOptions.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      </label>

      <label className="config-select config-sort-select">
        <select value={sortKey} onChange={(event) => onSortChange(event.target.value as LexiconSortKey)}>
          {sortOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <IconButton type="button" aria-label="刷新黑话库列表" onClick={onRefresh} disabled={refreshing}>
        <RefreshCw className={refreshing ? "spin" : ""} size={18} />
      </IconButton>
    </div>
  );
}
