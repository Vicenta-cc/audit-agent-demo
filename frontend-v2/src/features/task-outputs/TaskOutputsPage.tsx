import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import { controlJob, fetchAuditResults, fetchJobs, getResultTime, mapJobsToMonitorTasks } from "../../services/jobs";
import type { AuditResult, MonitorTask } from "../../types/jobs";
import { OutputGrid } from "./OutputGrid";
import { OutputPagination } from "./OutputPagination";
import { OutputToolbar } from "./OutputToolbar";
import { TaskOutputDrawer } from "./TaskOutputDrawer";
import { TaskOutputHeader } from "./TaskOutputHeader";
import { TaskRuntimeSummary } from "./TaskRuntimeSummary";
import {
  buildOutputSummary,
  getAuditStatusLabel,
  getConfidence,
  getOutputKey,
  getOutputSearchText,
  getPlatformName,
  getRiskLevelLabel,
  type OutputReviewFilter,
  type OutputRiskFilter,
  type OutputSortKey,
  type OutputSourceFilter,
  type TaskDrawerType
} from "./taskOutputUtils";

export function TaskOutputsPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState<MonitorTask | null>(null);
  const [outputs, setOutputs] = useState<AuditResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [riskFilter, setRiskFilter] = useState<OutputRiskFilter>("全部");
  const [reviewFilter, setReviewFilter] = useState<OutputReviewFilter>("全部");
  const [sourceFilter, setSourceFilter] = useState<OutputSourceFilter>("全部");
  const [sortKey, setSortKey] = useState<OutputSortKey>("default");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [activeDrawer, setActiveDrawer] = useState<TaskDrawerType>(null);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadData = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!taskId) {
        setError("缺少任务 ID");
        setLoading(false);
        return;
      }
      if (mode === "initial") {
        setLoading(true);
      } else {
        setRefreshing(true);
      }
      setError("");

      try {
        const [jobs, outputPayload] = await Promise.all([
          fetchJobs(),
          fetchAuditResults({ jobId: taskId, limit: 1000, sort: "latest" })
        ]);
        const targetJob = jobs.find((item) => item.id === taskId);
        if (!targetJob) {
          throw new Error("未找到该监控任务");
        }
        const [mappedTask] = mapJobsToMonitorTasks([targetJob], outputPayload.items || []);
        setTask(mappedTask);
        setOutputs(outputPayload.items || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载任务产出失败");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [taskId]
  );

  useEffect(() => {
    void loadData("initial");
  }, [loadData]);

  useEffect(() => {
    setPage(1);
    setOpenMenuId(null);
  }, [query, riskFilter, reviewFilter, sourceFilter, sortKey, pageSize]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredOutputs = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const matched = outputs.filter((output) => {
      const matchesKeyword = !keyword || getOutputSearchText(output).includes(keyword);
      const matchesRisk = riskFilter === "全部" || getRiskLevelLabel(output) === riskFilter;
      const matchesReview = reviewFilter === "全部" || getAuditStatusLabel(output) === reviewFilter;
      const matchesSource = sourceFilter === "全部" || (task ? getPlatformName(output, task) === sourceFilter : true);
      return matchesKeyword && matchesRisk && matchesReview && matchesSource;
    });

    return matched.slice().sort((a, b) => {
      if (sortKey === "latest") {
        return Date.parse(getResultTime(b) || "") - Date.parse(getResultTime(a) || "");
      }
      if (sortKey === "risk") {
        return getRiskRank(b) - getRiskRank(a) || getConfidence(b) - getConfidence(a);
      }
      if (sortKey === "confidence") {
        return getConfidence(b) - getConfidence(a);
      }
      return outputs.indexOf(a) - outputs.indexOf(b);
    });
  }, [outputs, query, reviewFilter, riskFilter, sortKey, sourceFilter, task]);

  const summary = useMemo(() => buildOutputSummary(outputs), [outputs]);
  const pageCount = Math.max(1, Math.ceil(filteredOutputs.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const visibleOutputs = filteredOutputs.slice((safePage - 1) * pageSize, safePage * pageSize);

  const handleControl = async (action: string, label: string) => {
    if (!task) {
      return;
    }
    setControlBusy(true);
    try {
      await controlJob(task.id, action);
      setToast({ message: `${label}已提交` });
      await loadData("refresh");
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : `${label}失败`, tone: "info" });
    } finally {
      setControlBusy(false);
    }
  };

  if (loading && !task) {
    return (
      <main className="task-outputs-page">
        <LoadingState label="正在加载任务产出..." />
      </main>
    );
  }

  if (error || !task) {
    return (
      <main className="task-outputs-page">
        <ErrorState message={error || "任务不存在"} onRetry={() => void loadData("initial")} />
      </main>
    );
  }

  return (
    <main className="task-outputs-page">
      <TaskOutputHeader
        task={task}
        busy={controlBusy}
        onBack={() => navigate("/tasks")}
        onOpenConfig={() => setActiveDrawer("config")}
        onControl={handleControl}
      />

      <TaskRuntimeSummary task={task} outputs={outputs} onOpenLogs={() => setActiveDrawer("logs")} />

      <section className="task-output-content-panel">
        <header className="task-output-section-header">
          <h2>本任务产出内容</h2>
          <p>
            共 {summary.total} 条 · 高危 {summary.highRisk} · 待复核 {summary.pendingReview} · 无风险 {summary.noRisk} · 可进入询证 {summary.evidenceable}
          </p>
        </header>

        <OutputToolbar
          query={query}
          riskFilter={riskFilter}
          reviewFilter={reviewFilter}
          sourceFilter={sourceFilter}
          sortKey={sortKey}
          refreshing={refreshing}
          onQueryChange={setQuery}
          onRiskChange={setRiskFilter}
          onReviewChange={setReviewFilter}
          onSourceChange={setSourceFilter}
          onSortChange={setSortKey}
          onRefresh={() => void loadData("refresh")}
        />

        {visibleOutputs.length ? (
          <OutputGrid
            task={task}
            outputs={visibleOutputs}
            openMenuId={openMenuId}
            onMenuChange={setOpenMenuId}
            onViewEvidence={(output) => navigate(`/tasks/${encodeURIComponent(task.id)}/outputs/${encodeURIComponent(getOutputKey(output))}`)}
            onAnalyzeUser={() => navigate("/users")}
          />
        ) : (
          <EmptyState title="暂无匹配产出" description="调整搜索、筛选或排序条件后再查看产出内容" />
        )}

        <OutputPagination
          total={filteredOutputs.length}
          page={safePage}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
        />
      </section>

      <TaskOutputDrawer activeDrawer={activeDrawer} task={task} outputs={outputs} onClose={() => setActiveDrawer(null)} />
      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

function getRiskRank(output: AuditResult) {
  const risk = getRiskLevelLabel(output);
  if (risk === "高危") {
    return 4;
  }
  if (risk === "中危") {
    return 3;
  }
  if (risk === "低危") {
    return 2;
  }
  if (risk === "无风险") {
    return 0;
  }
  return 1;
}
