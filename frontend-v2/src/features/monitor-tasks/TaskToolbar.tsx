import { RefreshCw, Search } from "lucide-react";
import type { TaskSortKey, TaskSourceFilter, TaskStatusFilter } from "../../types/jobs";
import { IconButton } from "../../components/common/IconButton";

interface TaskToolbarProps {
  query: string;
  statusFilter: TaskStatusFilter;
  sourceFilter: TaskSourceFilter;
  sortKey: TaskSortKey;
  refreshing: boolean;
  onQueryChange: (value: string) => void;
  onStatusChange: (value: TaskStatusFilter) => void;
  onSourceChange: (value: TaskSourceFilter) => void;
  onSortChange: (value: TaskSortKey) => void;
  onRefresh: () => void;
}

const statusOptions: TaskStatusFilter[] = ["全部", "运行中", "已暂停", "已完成", "失败"];
const sourceOptions: TaskSourceFilter[] = ["全部", "平台抓取", "直播接入", "重点用户", "本地视频"];
const sortOptions: Array<{ label: string; value: TaskSortKey }> = [
  { label: "默认排序", value: "default" },
  { label: "最近更新", value: "updated" },
  { label: "待分析最多", value: "waiting" },
  { label: "高危最多", value: "high" },
  { label: "产出最多", value: "outputs" }
];

export function TaskToolbar({
  query,
  statusFilter,
  sourceFilter,
  sortKey,
  refreshing,
  onQueryChange,
  onStatusChange,
  onSourceChange,
  onSortChange,
  onRefresh
}: TaskToolbarProps) {
  return (
    <div className="task-toolbar">
      <label className="task-search">
        <Search size={18} />
        <input
          value={query}
          type="search"
          placeholder="搜索任务名称 / 账号 / 方案"
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>

      <label className="task-select">
        <span>状态：</span>
        <select value={statusFilter} onChange={(event) => onStatusChange(event.target.value as TaskStatusFilter)}>
          {statusOptions.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      </label>

      <label className="task-select">
        <span>来源：</span>
        <select value={sourceFilter} onChange={(event) => onSourceChange(event.target.value as TaskSourceFilter)}>
          {sourceOptions.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      </label>

      <label className="task-select sort-select">
        <select value={sortKey} onChange={(event) => onSortChange(event.target.value as TaskSortKey)}>
          {sortOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <IconButton type="button" aria-label="刷新任务列表" onClick={onRefresh} disabled={refreshing}>
        <RefreshCw className={refreshing ? "spin" : ""} size={18} />
      </IconButton>
    </div>
  );
}
