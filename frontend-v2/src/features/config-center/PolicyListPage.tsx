import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import { deletePolicy, publishPolicy } from "../../services/configCenter";
import type {
  PolicyCategory,
  PolicySortKey,
  PolicySummary,
  ReferenceFilter,
  ResearchPolicy
} from "../../types/configCenter";
import { ConfigPagination } from "./ConfigPagination";
import { ConfigSummaryBar } from "./ConfigSummaryBar";
import { PolicyDetailDrawer } from "./PolicyDetailDrawer";
import { PolicyTable } from "./PolicyTable";
import { PolicyStatusFilter, PolicyToolbar } from "./PolicyToolbar";

interface PolicyListPageProps {
  policies: ResearchPolicy[];
  summary: PolicySummary;
  loading: boolean;
  refreshing: boolean;
  error: string;
  onRefresh: () => void;
  onPolicyDeleted: (policyId: string) => void;
}

export function PolicyListPage({
  policies,
  summary,
  loading,
  refreshing,
  error,
  onRefresh,
  onPolicyDeleted
}: PolicyListPageProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<"全部" | PolicyCategory>("全部");
  const [statusFilter, setStatusFilter] = useState<PolicyStatusFilter>("全部");
  const [referenceFilter, setReferenceFilter] = useState<ReferenceFilter>("全部");
  const [sortKey, setSortKey] = useState<PolicySortKey>("updated");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [selectedPolicy, setSelectedPolicy] = useState<ResearchPolicy | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ResearchPolicy | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    setPage(1);
  }, [query, categoryFilter, statusFilter, referenceFilter, sortKey, pageSize]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredPolicies = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const matched = policies.filter((policy) => {
      const matchesKeyword =
        !keyword ||
        [
          policy.name,
          policy.id,
          policy.description,
          policy.category,
          policy.scenarioTags.join(" "),
          policy.lexiconNames.join(" ")
        ]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      const matchesCategory = categoryFilter === "全部" || policy.category === categoryFilter;
      const matchesStatus = statusFilter === "全部" || policy.status === statusFilter;
      const matchesReference =
        referenceFilter === "全部" ||
        (referenceFilter === "已被引用" ? policy.references.length > 0 : policy.references.length === 0);
      return matchesKeyword && matchesCategory && matchesStatus && matchesReference;
    });

    return matched.slice().sort((a, b) => {
      if (sortKey === "references") {
        return b.references.length - a.references.length;
      }
      if (sortKey === "name") {
        return a.name.localeCompare(b.name, "zh-CN");
      }
      if (sortKey === "created") {
        return Date.parse(b.createdAt || "") - Date.parse(a.createdAt || "");
      }
      return Date.parse(b.updatedAt || "") - Date.parse(a.updatedAt || "");
    });
  }, [categoryFilter, policies, query, referenceFilter, sortKey, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filteredPolicies.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const visiblePolicies = filteredPolicies.slice((safePage - 1) * pageSize, safePage * pageSize);

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const handleEdit = (policy: ResearchPolicy) => {
    setOpenMenuId(null);
    navigate(`/config/policies/${encodeURIComponent(policy.id)}/edit`);
  };

  const handleCopy = (policy: ResearchPolicy) => {
    setOpenMenuId(null);
    showToast(`已创建「${policy.name}」的复制入口，编辑页将在下一阶段接入`, "info");
  };

  const handleToggleStatus = async (policy: ResearchPolicy) => {
    setOpenMenuId(null);
    if (policy.status === "published") {
      showToast("停用入口已保留，后续接入方案状态接口", "info");
      return;
    }
    try {
      await publishPolicy(policy.id);
      showToast("发布操作已提交");
      onRefresh();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "发布接口暂不可用", "info");
    }
  };

  const handleViewHistory = (policy: ResearchPolicy) => {
    setOpenMenuId(null);
    showToast(`「${policy.name}」版本历史入口已保留`, "info");
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) {
      return;
    }
    setDeleting(true);
    try {
      await deletePolicy(deleteTarget.id);
      onPolicyDeleted(deleteTarget.id);
      if (selectedPolicy?.id === deleteTarget.id) {
        setSelectedPolicy(null);
      }
      showToast("方案已删除");
      setDeleteTarget(null);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "删除失败", "info");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <section className="config-list-panel">
      <ConfigSummaryBar type="policies" policySummary={summary} />
      <PolicyToolbar
        query={query}
        categoryFilter={categoryFilter}
        statusFilter={statusFilter}
        referenceFilter={referenceFilter}
        sortKey={sortKey}
        refreshing={refreshing}
        onQueryChange={setQuery}
        onCategoryChange={setCategoryFilter}
        onStatusChange={setStatusFilter}
        onReferenceChange={setReferenceFilter}
        onSortChange={setSortKey}
        onRefresh={onRefresh}
      />

      {loading ? (
        <LoadingState label="正在加载研判方案..." />
      ) : error ? (
        <ErrorState message={error} onRetry={onRefresh} />
      ) : visiblePolicies.length ? (
        <PolicyTable
          policies={visiblePolicies}
          openMenuId={openMenuId}
          onMenuChange={setOpenMenuId}
          onOpenDetail={setSelectedPolicy}
          onEdit={handleEdit}
          onCopy={handleCopy}
          onToggleStatus={(policy) => void handleToggleStatus(policy)}
          onViewHistory={handleViewHistory}
          onDelete={(policy) => {
            setOpenMenuId(null);
            setDeleteTarget(policy);
          }}
        />
      ) : (
        <EmptyState title="暂无匹配方案" description="调整搜索、筛选或排序条件后再查看研判方案" />
      )}

      <ConfigPagination
        total={filteredPolicies.length}
        page={safePage}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />

      <PolicyDetailDrawer
        policy={selectedPolicy}
        onClose={() => setSelectedPolicy(null)}
        onEdit={handleEdit}
        onCopy={handleCopy}
        onToggleStatus={(policy) => void handleToggleStatus(policy)}
        onViewHistory={handleViewHistory}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除方案"
        description={`确认删除「${deleteTarget?.name || ""}」？删除后无法在列表中继续引用该方案。`}
        confirmText={deleting ? "删除中..." : "确认删除"}
        onCancel={() => {
          if (!deleting) {
            setDeleteTarget(null);
          }
        }}
        onConfirm={handleConfirmDelete}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </section>
  );
}
