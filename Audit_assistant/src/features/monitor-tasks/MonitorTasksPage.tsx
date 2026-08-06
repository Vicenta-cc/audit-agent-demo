import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  getListViewUrl,
  readEnumParam,
  readPageSizeParam,
  readPositiveIntParam,
  rememberListScroll,
  useRestoreListScroll,
  writeDefaultedParam
} from "../../app/listNavigation";
import { Toast } from "../../components/feedback/Toast";
import { EmptyState } from "../../components/feedback/EmptyState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import {
  controlJob,
  deleteJob,
  fetchJobsSnapshot,
  readCachedJobsSnapshot,
  writeCachedJobsSnapshot
} from "../../services/jobs";
import type { JobsSnapshot, MonitorTask, TaskSortKey, TaskSourceFilter, TaskStatusFilter } from "../../types/jobs";
import { MonitorPageHeader } from "./MonitorPageHeader";
import { TaskStatsStrip } from "./TaskStatsStrip";
import { TaskToolbar } from "./TaskToolbar";
import { TaskTable } from "./TaskTable";
import { TaskPagination } from "./TaskPagination";
import { TaskDetailDrawer } from "./TaskDetailDrawer";

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

const taskStatusFilters: TaskStatusFilter[] = ["全部", "运行中", "已暂停", "已完成", "失败"];
const taskSourceFilters: TaskSourceFilter[] = ["全部", "平台抓取", "直播接入", "重点用户", "本地视频"];
const taskSortKeys: TaskSortKey[] = ["default", "updated", "waiting", "high", "outputs"];

interface MonitorViewUpdate {
  query: string;
  statusFilter: TaskStatusFilter;
  sourceFilter: TaskSourceFilter;
  sortKey: TaskSortKey;
  page: number;
  pageSize: number;
}

export function MonitorTasksPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [initialCache] = useState(() => readCachedJobsSnapshot());
  const [snapshot, setSnapshot] = useState<JobsSnapshot>(initialCache?.snapshot ?? defaultSnapshot);
  const [loading, setLoading] = useState(!initialCache);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<MonitorTask | null>(null);
  const [drawerMode, setDrawerMode] = useState<"detail" | "outputs">("detail");
  const [deleteTarget, setDeleteTarget] = useState<MonitorTask | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);
  const query = searchParams.get("q") || "";
  const statusFilter = readEnumParam(searchParams, "status", taskStatusFilters, "全部");
  const sourceFilter = readEnumParam(searchParams, "source", taskSourceFilters, "全部");
  const sortKey = readEnumParam(searchParams, "sort", taskSortKeys, "default");
  const page = readPositiveIntParam(searchParams, "page", 1);
  const pageSize = readPageSizeParam(searchParams, 20);
  const viewUrl = getListViewUrl(location);
  const createdTaskName = (location.state as { createdTaskName?: string } | null)?.createdTaskName;

  const updateView = useCallback((updates: Partial<MonitorViewUpdate>) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (updates.query !== undefined) writeDefaultedParam(next, "q", updates.query, "");
      if (updates.statusFilter !== undefined) writeDefaultedParam(next, "status", updates.statusFilter, "全部");
      if (updates.sourceFilter !== undefined) writeDefaultedParam(next, "source", updates.sourceFilter, "全部");
      if (updates.sortKey !== undefined) writeDefaultedParam(next, "sort", updates.sortKey, "default");
      if (updates.page !== undefined) writeDefaultedParam(next, "page", updates.page, 1);
      if (updates.pageSize !== undefined) writeDefaultedParam(next, "size", updates.pageSize, 20);
      return next;
    }, { replace: true, state: location.state });
  }, [location.state, setSearchParams]);

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
      writeCachedJobsSnapshot(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "未知错误");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const cacheIsFresh = initialCache && Date.now() - initialCache.savedAt < 60_000;
    if (!cacheIsFresh) {
      void loadData(initialCache ? "refresh" : "initial");
    }
  }, [initialCache, loadData]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!createdTaskName) return;
    setToast({ message: `任务「${createdTaskName}」已创建` });
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
  }, [createdTaskName, location.pathname, location.search, navigate]);

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
  }, [query, snapshot.tasks, sortKey, sourceFilter, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filteredTasks.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const visibleTasks = filteredTasks.slice((safePage - 1) * pageSize, safePage * pageSize);

  useEffect(() => {
    if (!loading && page !== safePage) updateView({ page: safePage });
  }, [loading, page, safePage, updateView]);

  useRestoreListScroll(viewUrl, !loading);

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const handleCreate = () => navigate("/tasks/new");

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
      rememberListScroll(viewUrl);
      navigate(`/tasks/${encodeURIComponent(task.id)}/outputs`, {
        state: {
          returnTo: viewUrl,
          returnLabel: "监控任务",
          returnTitle: "监控任务列表"
        }
      });
      return;
    }
    setSelectedTask(task);
    setDrawerMode(mode);
  };

  return (
    <main className="monitor-tasks-page">
      <MonitorPageHeader onCreate={handleCreate} />
      <TaskStatsStrip stats={snapshot.stats} loading={loading} />

      <section className="task-list-panel">
        <TaskToolbar
          query={query}
          statusFilter={statusFilter}
          sourceFilter={sourceFilter}
          sortKey={sortKey}
          refreshing={refreshing}
          onQueryChange={(value) => updateView({ query: value, page: 1 })}
          onStatusChange={(value) => updateView({ statusFilter: value, page: 1 })}
          onSourceChange={(value) => updateView({ sourceFilter: value, page: 1 })}
          onSortChange={(value) => updateView({ sortKey: value, page: 1 })}
          onRefresh={() => void loadData("refresh")}
        />

        {loading ? (
          <LoadingState label="正在加载监控任务..." />
        ) : error && !snapshot.tasks.length ? (
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
          onPageChange={(value) => updateView({ page: value })}
          onPageSizeChange={(value) => updateView({ pageSize: value, page: 1 })}
        />
      </section>

      <TaskDetailDrawer
        task={selectedTask}
        mode={drawerMode}
        onClose={() => setSelectedTask(null)}
        onControl={handleControl}
      />
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
