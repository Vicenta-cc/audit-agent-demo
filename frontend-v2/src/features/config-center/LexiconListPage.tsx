import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import { deleteLexicon } from "../../services/configCenter";
import type {
  LexiconCategory,
  LexiconSortKey,
  LexiconSummary,
  ReferenceFilter,
  RiskLexicon
} from "../../types/configCenter";
import { ConfigPagination } from "./ConfigPagination";
import { ConfigSummaryBar } from "./ConfigSummaryBar";
import { LexiconTable } from "./LexiconTable";
import { LexiconToolbar } from "./LexiconToolbar";

interface LexiconListPageProps {
  lexicons: RiskLexicon[];
  summary: LexiconSummary;
  loading: boolean;
  refreshing: boolean;
  error: string;
  onRefresh: () => void;
  onLexiconDeleted: (lexiconId: string) => void;
}

export function LexiconListPage({
  lexicons,
  summary,
  loading,
  refreshing,
  error,
  onRefresh,
  onLexiconDeleted
}: LexiconListPageProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<"全部" | LexiconCategory>("全部");
  const [referenceFilter, setReferenceFilter] = useState<ReferenceFilter>("全部");
  const [sortKey, setSortKey] = useState<LexiconSortKey>("updated");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RiskLexicon | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    setPage(1);
  }, [query, categoryFilter, referenceFilter, sortKey, pageSize]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredLexicons = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const matched = lexicons.filter((lexicon) => {
      const matchesKeyword =
        !keyword ||
        [lexicon.name, lexicon.id, lexicon.category, lexicon.keywords.join(" "), lexicon.platformTags.join(" ")]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      const matchesCategory = categoryFilter === "全部" || lexicon.category === categoryFilter;
      const matchesReference =
        referenceFilter === "全部" ||
        (referenceFilter === "已被引用" ? lexicon.references.length > 0 : lexicon.references.length === 0);
      return matchesKeyword && matchesCategory && matchesReference;
    });

    return matched.slice().sort((a, b) => {
      if (sortKey === "entries") {
        return b.entryCount - a.entryCount;
      }
      if (sortKey === "references") {
        return b.references.length - a.references.length;
      }
      if (sortKey === "name") {
        return a.name.localeCompare(b.name, "zh-CN");
      }
      return Date.parse(b.updatedAt || "") - Date.parse(a.updatedAt || "");
    });
  }, [categoryFilter, lexicons, query, referenceFilter, sortKey]);

  const pageCount = Math.max(1, Math.ceil(filteredLexicons.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const visibleLexicons = filteredLexicons.slice((safePage - 1) * pageSize, safePage * pageSize);

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const handleView = (lexicon: RiskLexicon) => {
    showToast(`「${lexicon.name}」详情页将在后续阶段接入`, "info");
  };

  const handleEdit = (lexicon: RiskLexicon) => {
    setOpenMenuId(null);
    navigate(`/config/lexicons/${encodeURIComponent(lexicon.id)}/edit`);
  };

  const handleCopy = (lexicon: RiskLexicon) => {
    setOpenMenuId(null);
    showToast(`已创建「${lexicon.name}」的复制入口，编辑页将在下一阶段接入`, "info");
  };

  const handleViewHistory = (lexicon: RiskLexicon) => {
    setOpenMenuId(null);
    showToast(`「${lexicon.name}」版本历史入口已保留`, "info");
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) {
      return;
    }
    setDeleting(true);
    try {
      await deleteLexicon(deleteTarget.id);
      onLexiconDeleted(deleteTarget.id);
      showToast("黑话库已删除");
      setDeleteTarget(null);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "删除失败", "info");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <section className="config-list-panel">
      <ConfigSummaryBar type="lexicons" lexiconSummary={summary} />
      <LexiconToolbar
        query={query}
        categoryFilter={categoryFilter}
        referenceFilter={referenceFilter}
        sortKey={sortKey}
        refreshing={refreshing}
        onQueryChange={setQuery}
        onCategoryChange={setCategoryFilter}
        onReferenceChange={setReferenceFilter}
        onSortChange={setSortKey}
        onRefresh={onRefresh}
      />

      {loading ? (
        <LoadingState label="正在加载黑话库..." />
      ) : error ? (
        <ErrorState message={error} onRetry={onRefresh} />
      ) : visibleLexicons.length ? (
        <LexiconTable
          lexicons={visibleLexicons}
          openMenuId={openMenuId}
          onMenuChange={setOpenMenuId}
          onView={handleView}
          onEdit={handleEdit}
          onCopy={handleCopy}
          onViewHistory={handleViewHistory}
          onDelete={(lexicon) => {
            setOpenMenuId(null);
            setDeleteTarget(lexicon);
          }}
        />
      ) : (
        <EmptyState title="暂无匹配黑话库" description="调整搜索、筛选或排序条件后再查看黑话库" />
      )}

      <ConfigPagination
        total={filteredLexicons.length}
        page={safePage}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除黑话库"
        description={`确认删除「${deleteTarget?.name || ""}」？相关方案会失去该知识库引用。`}
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
