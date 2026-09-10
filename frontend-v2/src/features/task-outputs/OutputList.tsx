import type { TaskOutputItem } from "../../types/taskOutputs";
import { OutputListItem } from "./OutputListItem";

interface OutputListProps {
  outputs: TaskOutputItem[];
  onViewEvidence: (item: TaskOutputItem) => void;
}

export function OutputList({ outputs, onViewEvidence }: OutputListProps) {
  return (
    <div className="output-list" aria-label="本任务内容分析结果列表">
      {outputs.map((item) => (
        <OutputListItem
          key={item.id}
          item={item}
          onViewEvidence={onViewEvidence}
        />
      ))}
    </div>
  );
}
