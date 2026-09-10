import { RefreshCw, Search } from "lucide-react";
import { IconButton } from "../../components/common/IconButton";
import type { PolicySortKey, ReferenceFilter } from "../../types/configCenter";

interface PolicyToolbarProps {
  query: string;
  referenceFilter: ReferenceFilter;
  sortKey: PolicySortKey;
  refreshing: boolean;
  onQueryChange: (value: string) => void;
  onReferenceChange: (value: ReferenceFilter) => void;
  onSortChange: (value: PolicySortKey) => void;
  onRefresh: () => void;
}

const referenceOptions: ReferenceFilter[] = ["全部", "已被引用", "未被引用"];
const sortOptions: Array<{ value: PolicySortKey; label: string }> = [
  { value: "updated", label: "最近更新" },
  { value: "references", label: "引用任务最多" },
  { value: "name", label: "名称排序" },
  { value: "created", label: "创建时间" }
];

export function PolicyToolbar({
  query,
  referenceFilter,
  sortKey,
  refreshing,
  onQueryChange,
  onReferenceChange,
  onSortChange,
  onRefresh
}: PolicyToolbarProps) {
  return (
    <div className="config-toolbar">
      <label className="config-search">
        <Search size={18} />
        <input
          value={query}
          type="search"
          placeholder="搜索方案名称或知识库"
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>
      <label className="config-select">
        <span>引用状态：</span>
        <select value={referenceFilter} onChange={(event) => onReferenceChange(event.target.value as ReferenceFilter)}>
          {referenceOptions.map((option) => <option key={option}>{option}</option>)}
        </select>
      </label>
      <label className="config-select config-sort-select">
        <select value={sortKey} onChange={(event) => onSortChange(event.target.value as PolicySortKey)}>
          {sortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
      <IconButton type="button" aria-label="刷新研判方案列表" onClick={onRefresh} disabled={refreshing}>
        <RefreshCw className={refreshing ? "spin" : ""} size={18} />
      </IconButton>
    </div>
  );
}
