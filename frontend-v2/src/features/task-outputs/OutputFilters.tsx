import { ChevronDown, Search } from "lucide-react";
import type { OutputFiltersValue, RiskLibrary } from "../../types/taskOutputs";

interface OutputFiltersProps {
  value: OutputFiltersValue;
  riskLibraries: RiskLibrary[];
  onApply: (value: OutputFiltersValue) => void;
}

export function OutputFilters({ value, riskLibraries, onApply }: OutputFiltersProps) {
  void riskLibraries;

  const update = <Key extends keyof OutputFiltersValue>(key: Key, nextValue: OutputFiltersValue[Key]) => {
    onApply({ ...value, [key]: nextValue });
  };

  return (
    <div className="output-filters">
      <h2>内容分析结果</h2>
      <div className="output-filter-controls">
      <label className="output-filter-search">
        <Search size={17} />
        <input
          type="search"
          value={value.query}
          placeholder="搜索标题 / 作者"
          onChange={(event) => update("query", event.target.value)}
        />
      </label>
      <label className="output-filter-select output-sort-select">
        <select value={value.sort} onChange={(event) => update("sort", event.target.value as OutputFiltersValue["sort"])} aria-label="排序">
          <option value="latest">最新优先</option>
          <option value="risk">风险优先</option>
        </select>
        <ChevronDown size={17} aria-hidden="true" />
      </label>
      </div>
    </div>
  );
}
