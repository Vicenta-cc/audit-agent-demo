import type { AuditResult, MonitorTask } from "../../types/jobs";
import { getOutputKey } from "./taskOutputUtils";
import { OutputCard } from "./OutputCard";

interface OutputGridProps {
  task: MonitorTask;
  outputs: AuditResult[];
  openMenuId: string | null;
  onMenuChange: (value: string | null) => void;
  onViewEvidence: (output: AuditResult) => void;
  onAnalyzeUser: (output: AuditResult) => void;
}

export function OutputGrid({
  task,
  outputs,
  openMenuId,
  onMenuChange,
  onViewEvidence,
  onAnalyzeUser
}: OutputGridProps) {
  return (
    <div className="output-grid" aria-label="本任务产出内容列表">
      {outputs.map((output) => {
        const key = getOutputKey(output);
        return (
          <OutputCard
            key={key}
            task={task}
            output={output}
            menuOpen={openMenuId === key}
            onMenuToggle={() => onMenuChange(openMenuId === key ? null : key)}
            onMenuClose={() => onMenuChange(null)}
            onViewEvidence={onViewEvidence}
            onAnalyzeUser={onAnalyzeUser}
          />
        );
      })}
    </div>
  );
}
