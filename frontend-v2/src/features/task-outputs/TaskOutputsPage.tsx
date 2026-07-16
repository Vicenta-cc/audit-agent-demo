import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import { fetchAuditResults, fetchJobs, mapJobsToMonitorTasks } from "../../services/jobs";
import type { AuditResult, MonitorTask } from "../../types/jobs";
import type { OutputFiltersValue, TaskDrawerType, TaskOutputItem } from "../../types/taskOutputs";
import { OutputFilters } from "./OutputFilters";
import { OutputList } from "./OutputList";
import { OutputPagination } from "./OutputPagination";
import { RiskSummary } from "./RiskSummary";
import { TaskOutputDrawer } from "./TaskOutputDrawer";
import { TaskOutputHeader } from "./TaskOutputHeader";
import {
  buildConfigHistory,
  buildOutputSummary,
  buildTaskConfig,
  buildTaskLogs,
  getOutputSearchText,
  getRiskLibraries,
  hasEvidenceType,
  mapAuditResultToTaskOutput
} from "./taskOutputUtils";

const initialFilters: OutputFiltersValue = {
  riskLevel: "全部",
  riskLibraryId: "全部",
  evidenceType: "全部",
  query: "",
  sort: "latest"
};

export function TaskOutputsPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState<MonitorTask | null>(null);
  const [rawOutputs, setRawOutputs] = useState<AuditResult[]>([]);
  const [apiTotal, setApiTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState<OutputFiltersValue>(initialFilters);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [activeDrawer, setActiveDrawer] = useState<TaskDrawerType>(null);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadData = useCallback(async () => {
    if (!taskId) {
      setError("缺少任务 ID");
      setLoading(false);
      return;
    }
    setError("");
    try {
      const [jobs, outputPayload] = await Promise.all([
        fetchJobs(),
        fetchAuditResults({ jobId: taskId, limit: 1000, sort: "latest" })
      ]);
      const targetJob = jobs.find((item) => item.id === taskId);
      if (!targetJob) throw new Error("未找到该监控任务");
      const [mappedTask] = mapJobsToMonitorTasks([targetJob], outputPayload.items || []);
      setTask(mappedTask);
      setRawOutputs(outputPayload.items || []);
      setApiTotal(outputPayload.total ?? outputPayload.items?.length ?? 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载任务产出失败");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => { void loadData(); }, [loadData]);
  useEffect(() => {
    setPage(1);
  }, [filters, pageSize]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const outputs = useMemo(() => rawOutputs.map(mapAuditResultToTaskOutput), [rawOutputs]);
  const riskLibraries = useMemo(() => getRiskLibraries(outputs), [outputs]);
  const summary = useMemo(() => buildOutputSummary(outputs, apiTotal || outputs.length), [apiTotal, outputs]);
  const taskConfig = useMemo(() => task ? buildTaskConfig(task, riskLibraries) : null, [riskLibraries, task]);
  const configHistory = useMemo(() => task ? buildConfigHistory(task) : [], [task]);
  const logs = useMemo(() => task ? buildTaskLogs(task) : [], [task]);

  const filteredOutputs = useMemo(() => {
    const keyword = filters.query.trim().toLowerCase();
    const matched = outputs.filter((item) => {
      const matchesKeyword = !keyword || getOutputSearchText(item).includes(keyword);
      const matchesRisk = filters.riskLevel === "全部" || item.riskLevel === filters.riskLevel;
      const matchesLibrary = filters.riskLibraryId === "全部" || item.riskLibrary?.id === filters.riskLibraryId;
      return matchesKeyword && matchesRisk && matchesLibrary && hasEvidenceType(item, filters.evidenceType);
    });
    return matched.slice().sort((a, b) => {
      if (filters.sort === "risk") return riskRank(b) - riskRank(a) || parseTime(b.timestamp) - parseTime(a.timestamp);
      return parseTime(b.timestamp) - parseTime(a.timestamp);
    });
  }, [filters, outputs]);

  const pageCount = Math.max(1, Math.ceil(filteredOutputs.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const visibleOutputs = filteredOutputs.slice((safePage - 1) * pageSize, safePage * pageSize);

  if (loading && !task) return <main className="task-outputs-page"><LoadingState label="正在加载任务产出..." /></main>;
  if (error || !task || !taskConfig) {
    return <main className="task-outputs-page"><ErrorState message={error || "任务不存在"} onRetry={() => void loadData()} /></main>;
  }

  return (
    <main className="task-outputs-page">
      <TaskOutputHeader
        task={task}
        onBack={() => navigate("/tasks")}
        onOpenConfig={() => setActiveDrawer((current) => current === "config" ? null : "config")}
        onOpenLogs={() => setActiveDrawer((current) => current === "logs" ? null : "logs")}
        onContinueCollection={() => setToast({ message: "当前后端未提供恢复持续采集接口", tone: "info" })}
      />
      <RiskSummary summary={summary} />
      <section className="task-output-content-panel" aria-label="内容分析结果">
        <OutputFilters value={filters} riskLibraries={riskLibraries} onApply={setFilters} />
        {visibleOutputs.length ? (
          <OutputList
            outputs={visibleOutputs}
            onViewEvidence={(item) => navigate(`/tasks/${encodeURIComponent(task.id)}/outputs/${encodeURIComponent(item.id)}`)}
          />
        ) : <EmptyState title="暂无匹配结果" description="调整筛选条件后再查看内容分析结果" />}
        <OutputPagination
          total={filteredOutputs.length}
          page={safePage}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
        />
      </section>
      <TaskOutputDrawer
        activeDrawer={activeDrawer}
        config={taskConfig}
        configHistory={configHistory}
        logs={logs}
        taskId={task.id}
        onClose={() => setActiveDrawer(null)}
      />
      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

function riskRank(item: TaskOutputItem) {
  return { 高危: 4, 中危: 3, 待复核: 2, 无风险: 1 }[item.riskLevel];
}

function parseTime(value: string) {
  const timestamp = Date.parse(value || "");
  return Number.isFinite(timestamp) ? timestamp : 0;
}
