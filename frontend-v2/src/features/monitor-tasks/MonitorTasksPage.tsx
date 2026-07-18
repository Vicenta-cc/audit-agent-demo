import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Toast } from "../../components/feedback/Toast";
import { EmptyState } from "../../components/feedback/EmptyState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import { controlJob, deleteJob, fetchJobsSnapshot } from "../../services/jobs";
import type { JobsSnapshot, MonitorTask, TaskSortKey, TaskSourceFilter, TaskStatusFilter } from "../../types/jobs";
import { MonitorPageHeader } from "./MonitorPageHeader";
import { TaskStatsStrip } from "./TaskStatsStrip";
import { TaskToolbar } from "./TaskToolbar";
import { TaskTable } from "./TaskTable";
import { TaskPagination } from "./TaskPagination";
import { TaskDetailDrawer } from "./TaskDetailDrawer";
import { CreateTaskDrawer } from "./CreateTaskDrawer";

const defaultSnapshot: JobsSnapshot = {
  jobs: [],
  auditResults: [],
  tasks: [],
  stats: {
    runningTasks: 0,
    recentRiskCount: 0,
    liveTaskCount: 0,
    focusUserTaskCount: 0,
    platformCrawlTaskCount: 0
  }
};

export function MonitorTasksPage() {
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<JobsSnapshot>(defaultSnapshot);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>("全部");
  const [sourceFilter, setSourceFilter] = useState<TaskSourceFilter>("全部");
  const [sortKey, setSortKey] = useState<TaskSortKey>("default");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<MonitorTask | null>(null);
  const [drawerMode, setDrawerMode] = useState<"detail" | "outputs">("detail");
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MonitorTask | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadData = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "initial") {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    setError("");

    try {
      const next = await fetchJobsSnapshot();
      setSnapshot(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "未知错误");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadData("initial");
  }, [loadData]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    setPage(1);
  }, [query, statusFilter, sourceFilter, sortKey, pageSize]);

  const filteredTasks = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const matched = snapshot.tasks.filter((task) => {
      const matchesKeyword =
        !keyword ||
        [task.name, task.id, task.objectLabel, task.referencePlan, task.platformLabel, task.sourceLabel]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      const matchesStatus = statusFilter === "全部" || task.status === statusFilter;
      const matchesSource = sourceFilter === "全部" || task.source === sourceFilter;
      return matchesKeyword && matchesStatus && matchesSource;
    });

    return matched.slice().sort((a, b) => {
      if (sortKey === "updated") {
        return Date.parse(b.updatedAt || "") - Date.parse(a.updatedAt || "");
      }
      if (sortKey === "waiting") {
        return b.metrics.waiting - a.metrics.waiting;
      }
      if (sortKey === "high") {
        return b.metrics.highRisk - a.metrics.highRisk;
      }
      if (sortKey === "outputs") {
        return b.metrics.outputs - a.metrics.outputs;
      }
      return snapshot.tasks.indexOf(a) - snapshot.tasks.indexOf(b);
    });
  }, [pageSize, query, snapshot.tasks, sortKey, sourceFilter, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filteredTasks.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const visibleTasks = filteredTasks.slice((safePage - 1) * pageSize, safePage * pageSize);

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const handleCreate = () => {
    setCreating(true);
    window.setTimeout(() => {
      setCreating(false);
      setCreateOpen(true);
    }, 450);
  };

  const handleControl = async (task: MonitorTask, action: string, label: string) => {
    setOpenMenuId(null);
    try {
      await controlJob(task.id, action);
      showToast(`${label}已提交`);
      await loadData("refresh");
    } catch (err) {
      showToast(err instanceof Error ? err.message : `${label}失败`, "info");
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) {
      return;
    }
    setDeleting(true);
    try {
      const result = await deleteJob(deleteTarget.id);
      showToast(`任务已删除，移除 ${result.deleted_result_count} 条产出`);
      setDeleteTarget(null);
      await loadData("refresh");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "删除失败", "info");
    } finally {
      setDeleting(false);
    }
  };

  const openDetail = (task: MonitorTask, mode: "detail" | "outputs" = "detail") => {
    if (mode === "outputs") {
      navigate(`/tasks/${encodeURIComponent(task.id)}/outputs`);
      return;
    }
    setSelectedTask(task);
    setDrawerMode(mode);
  };

  return (
    <main className="monitor-tasks-page">
      <MonitorPageHeader onCreate={handleCreate} creating={creating} />
      <TaskStatsStrip stats={snapshot.stats} loading={loading} />

      <section className="task-list-panel">
        <TaskToolbar
          query={query}
          statusFilter={statusFilter}
          sourceFilter={sourceFilter}
          sortKey={sortKey}
          refreshing={refreshing}
          onQueryChange={setQuery}
          onStatusChange={setStatusFilter}
          onSourceChange={setSourceFilter}
          onSortChange={setSortKey}
          onRefresh={() => void loadData("refresh")}
        />

        {loading ? (
          <LoadingState label="正在加载监控任务..." />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void loadData("initial")} />
        ) : visibleTasks.length ? (
          <TaskTable
            tasks={visibleTasks}
            openMenuId={openMenuId}
            onMenuChange={setOpenMenuId}
            onOpenDetail={openDetail}
            onControl={handleControl}
            onDelete={(task) => {
              setOpenMenuId(null);
              setDeleteTarget(task);
            }}
          />
        ) : (
          <EmptyState title="暂无匹配任务" description="调整搜索、筛选或排序条件后再查看任务列表" />
        )}

        <TaskPagination
          total={filteredTasks.length}
          page={safePage}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
        />
      </section>

      <TaskDetailDrawer
        task={selectedTask}
        mode={drawerMode}
        onClose={() => setSelectedTask(null)}
        onControl={handleControl}
      />
      <CreateTaskDrawer open={createOpen} onClose={() => setCreateOpen(false)} />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除任务"
        description={`确认删除「${deleteTarget?.name || ""}」？该任务生成的分析帖子也会从风险研判中删除，此操作不可恢复。`}
        confirmText={deleting ? "删除中..." : "确认删除"}
        onCancel={() => {
          if (!deleting) {
            setDeleteTarget(null);
          }
        }}
        onConfirm={handleConfirmDelete}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}
