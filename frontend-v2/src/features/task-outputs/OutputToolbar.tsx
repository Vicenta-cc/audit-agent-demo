import { RefreshCw, Search } from "lucide-react";
import type { OutputReviewFilter, OutputRiskFilter, OutputSortKey, OutputSourceFilter } from "./taskOutputUtils";

interface OutputToolbarProps {
  query: string;
  riskFilter: OutputRiskFilter;
  reviewFilter: OutputReviewFilter;
  sourceFilter: OutputSourceFilter;
  sortKey: OutputSortKey;
  refreshing?: boolean;
  onQueryChange: (value: string) => void;
  onRiskChange: (value: OutputRiskFilter) => void;
  onReviewChange: (value: OutputReviewFilter) => void;
  onSourceChange: (value: OutputSourceFilter) => void;
  onSortChange: (value: OutputSortKey) => void;
  onRefresh: () => void;
}

export function OutputToolbar({
  query,
  riskFilter,
  reviewFilter,
  sourceFilter,
  sortKey,
  refreshing = false,
  onQueryChange,
  onRiskChange,
  onReviewChange,
  onSourceChange,
  onSortChange,
  onRefresh
}: OutputToolbarProps) {
  return (
    <div className="output-toolbar">
      <label className="task-search output-search">
        <Search size={18} />
        <input
          value={query}
          type="search"
          placeholder="搜索内容 / 作者 / 证据"
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>

      <label className="task-select output-select">
        <span>风险等级：</span>
        <select value={riskFilter} onChange={(event) => onRiskChange(event.target.value as OutputRiskFilter)}>
          <option value="全部">全部</option>
          <option value="高危">高危</option>
          <option value="中危">中危</option>
          <option value="低危">低危</option>
          <option value="无风险">无风险</option>
        </select>
      </label>

      <label className="task-select output-select">
        <span>审核状态：</span>
        <select value={reviewFilter} onChange={(event) => onReviewChange(event.target.value as OutputReviewFilter)}>
          <option value="全部">全部</option>
          <option value="待复核">待复核</option>
          <option value="已复核">已复核</option>
          <option value="无风险">无风险</option>
        </select>
      </label>

      <label className="task-select output-select source-select">
        <span>来源：</span>
        <select value={sourceFilter} onChange={(event) => onSourceChange(event.target.value as OutputSourceFilter)}>
          <option value="全部">全部</option>
          <option value="小红书">小红书</option>
          <option value="抖音">抖音</option>
          <option value="快手">快手</option>
          <option value="本地上传">本地上传</option>
        </select>
      </label>

      <label className="task-select output-select sort-select">
        <select value={sortKey} onChange={(event) => onSortChange(event.target.value as OutputSortKey)}>
          <option value="default">默认排序</option>
          <option value="latest">最新发现</option>
          <option value="risk">风险优先</option>
          <option value="confidence">置信度优先</option>
        </select>
      </label>

      <button className="mt-icon-button" type="button" aria-label="刷新产出" disabled={refreshing} onClick={onRefresh}>
        <RefreshCw size={18} className={refreshing ? "spin" : ""} />
      </button>
    </div>
  );
}
