import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  getListViewUrl,
  getReturnNavigationState,
  readEnumParam,
  readPageSizeParam,
  readPositiveIntParam,
  rememberListScroll,
  useRestoreListScroll,
  writeDefaultedParam
} from "../../app/listNavigation";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import { fetchAuditResults, fetchJob, mapJobsToMonitorTasks } from "../../services/jobs";
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

const outputRiskLevels: OutputFiltersValue["riskLevel"][] = ["全部", "高危", "中危", "待复核", "无风险"];
const outputEvidenceTypes: OutputFiltersValue["evidenceType"][] = ["全部", "文本", "OCR", "ASR", "视觉", "评论"];
const outputSortKeys: OutputFiltersValue["sort"][] = ["latest", "risk"];

interface OutputViewUpdate {
  filters: OutputFiltersValue;
  page: number;
  pageSize: number;
}

export function TaskOutputsPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [task, setTask] = useState<MonitorTask | null>(null);
  const [rawOutputs, setRawOutputs] = useState<AuditResult[]>([]);
  const [apiTotal, setApiTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeDrawer, setActiveDrawer] = useState<TaskDrawerType>(null);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);
  const filters = useMemo<OutputFiltersValue>(() => ({
    query: searchParams.get("q") || initialFilters.query,
    riskLevel: readEnumParam(searchParams, "level", outputRiskLevels, initialFilters.riskLevel),
    riskLibraryId: searchParams.get("library") || initialFilters.riskLibraryId,
    evidenceType: readEnumParam(searchParams, "evidence", outputEvidenceTypes, initialFilters.evidenceType),
    sort: readEnumParam(searchParams, "sort", outputSortKeys, initialFilters.sort)
  }), [searchParams]);
  const page = readPositiveIntParam(searchParams, "page", 1);
  const pageSize = readPageSizeParam(searchParams, 10);
  const viewUrl = getListViewUrl(location);
  const parentReturn = getReturnNavigationState(location.state);

  const updateView = useCallback((updates: Partial<OutputViewUpdate>) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (updates.filters) {
        writeDefaultedParam(next, "q", updates.filters.query, initialFilters.query);
        writeDefaultedParam(next, "level", updates.filters.riskLevel, initialFilters.riskLevel);
        writeDefaultedParam(next, "library", updates.filters.riskLibraryId, initialFilters.riskLibraryId);
        writeDefaultedParam(next, "evidence", updates.filters.evidenceType, initialFilters.evidenceType);
        writeDefaultedParam(next, "sort", updates.filters.sort, initialFilters.sort);
      }
      if (updates.page !== undefined) writeDefaultedParam(next, "page", updates.page, 1);
      if (updates.pageSize !== undefined) writeDefaultedParam(next, "size", updates.pageSize, 10);
      return next;
    }, { replace: true, state: location.state });
  }, [location.state, setSearchParams]);

  const loadData = useCallback(async () => {
    if (!taskId) {
      setError("缺少任务 ID");
      setLoading(false);
      return;
    }
    setError("");
    try {
      const [targetJob, outputPayload] = await Promise.all([
        fetchJob(taskId),
        fetchAuditResults({ jobId: taskId, limit: 1000, sort: "latest", compact: true })
      ]);
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

  useEffect(() => {
    if (!loading && page !== safePage) updateView({ page: safePage });
  }, [loading, page, safePage, updateView]);

  useRestoreListScroll(viewUrl, !loading && Boolean(task));

  const handleBack = () => {
    if (parentReturn) navigate(-1);
    else navigate("/tasks");
  };

  const handleViewEvidence = (item: TaskOutputItem) => {
    if (!task) return;
    rememberListScroll(viewUrl);
    navigate(`/tasks/${encodeURIComponent(task.id)}/outputs/${encodeURIComponent(item.id)}`, {
      state: {
        returnTo: viewUrl,
        returnLabel: "监控任务",
        returnTitle: task.name
      }
    });
  };

  if (loading && !task) return <main className="task-outputs-page"><LoadingState label="正在加载任务产出..." /></main>;
  if (error || !task || !taskConfig) {
    return <main className="task-outputs-page"><ErrorState message={error || "任务不存在"} onRetry={() => void loadData()} /></main>;
  }

  return (
    <main className="task-outputs-page">
      <TaskOutputHeader
        task={task}
        onBack={handleBack}
        onOpenConfig={() => setActiveDrawer((current) => current === "config" ? null : "config")}
        onOpenLogs={() => setActiveDrawer((current) => current === "logs" ? null : "logs")}
        onContinueCollection={() => setToast({ message: "当前后端未提供恢复持续采集接口", tone: "info" })}
      />
      <RiskSummary
        summary={summary}
        activeRiskLevel={filters.riskLevel}
        onRiskLevelChange={(riskLevel) => updateView({ filters: { ...filters, riskLevel }, page: 1 })}
      />
      <section className="task-output-content-panel" aria-label="内容分析结果">
        <OutputFilters
          value={filters}
          riskLibraries={riskLibraries}
          onApply={(value) => updateView({ filters: value, page: 1 })}
        />
        {visibleOutputs.length ? (
          <OutputList
            outputs={visibleOutputs}
            onViewEvidence={handleViewEvidence}
          />
        ) : <EmptyState title="暂无匹配结果" description="调整筛选条件后再查看内容分析结果" />}
        <OutputPagination
          total={filteredOutputs.length}
          page={safePage}
          pageSize={pageSize}
          onPageChange={(value) => updateView({ page: value })}
          onPageSizeChange={(value) => updateView({ pageSize: value, page: 1 })}
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
