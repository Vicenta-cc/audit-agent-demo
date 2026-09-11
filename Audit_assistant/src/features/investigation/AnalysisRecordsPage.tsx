import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpDown,
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FileSearch,
  Search,
  SlidersHorizontal
} from "lucide-react";
import {
  analysisEvidenceTypes,
  analysisRiskOrder,
  createCompletedAnalysisRecords,
  normalizeAnalysisRecords,
  readStoredAnalysisRecords,
  type AnalysisRecord,
  type AnalysisRisk
} from "./analysisRecords";
import { getInvestigationWorkspaceState } from "../../services/investigationCreation";
import { AnalysisBasisDrawer } from "./AnalysisBasisDrawer";
import { loadM3AnalysisRecords, reportAuditDetailPath, reportPostDetailPath } from "./m3AnalysisRecords";

import { loadHistoricalAnalysisRecords } from "./historicalAnalysisRecords";

type RiskFilter = "all" | AnalysisRisk;
type SortKey = "latest" | "risk";

interface AnalysisRecordsRouteState {
  records?: AnalysisRecord[];
  investigationTitle?: string;
}

const PAGE_SIZE = 10;
const riskFilters: Array<{ value: AnalysisRisk; label: string }> = [
  { value: "high", label: "高风险" },
  { value: "medium", label: "中风险" },
  { value: "low", label: "低风险" },
  { value: "safe", label: "无风险" }
];

export function AnalysisRecordsPage() {
  const { investigationId = "session-wc-gambling" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const routeState = (location.state || {}) as AnalysisRecordsRouteState;
  const authoritativeRunId = searchParams.get("run") || "";
  const historical = investigationId.startsWith("historical-report-");
  const authoritative = historical || Boolean(authoritativeRunId);
  const [records, setRecords] = useState<AnalysisRecord[]>(() => (
    authoritative
      ? normalizeAnalysisRecords(routeState.records || [])
      : routeState.records
        ? normalizeAnalysisRecords(routeState.records)
        : readStoredAnalysisRecords(investigationId) || createCompletedAnalysisRecords()
  ));
  const [recordsLoading, setRecordsLoading] = useState(authoritative);
  const [recordsError, setRecordsError] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<AnalysisRecord | null>(null);
  const [investigationTitle, setInvestigationTitle] = useState(
    routeState.investigationTitle || "当前调查"
  );
  const query = searchParams.get("q") || "";
  const riskParam = searchParams.get("risk") || "all";
  const normalizedRiskParam = riskParam === "review" ? "low" : riskParam;
  const riskFilter: RiskFilter = ["all", "high", "medium", "low", "safe"].includes(normalizedRiskParam)
    ? normalizedRiskParam as RiskFilter
    : "all";
  const sort: SortKey = searchParams.get("sort") === "risk" ? "risk" : "latest";
  const requestedPage = Math.max(1, Number(searchParams.get("page")) || 1);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = `全部分析记录 · ${investigationTitle}`;
    return () => {
      document.title = previousTitle;
    };
  }, [investigationTitle]);

  useEffect(() => {
    if (!authoritative) return;
    let current = true;
    setRecordsLoading(true);
    setRecordsError("");
    const request = historical
      ? loadHistoricalAnalysisRecords(investigationId).then(result => {
          if (current) setInvestigationTitle(result.title);
          return result;
        })
      : getInvestigationWorkspaceState(investigationId).then((state) => {
        if (!state.run || state.run.run_id !== authoritativeRunId) {
          throw new Error("当前调查没有匹配的真实 Run");
        }
        const expectedReport = searchParams.get("report") || "";
        if (expectedReport && state.run.report_version_id !== expectedReport) {
          throw new Error("Run 与 ReportVersion 引用不一致");
        }
        setInvestigationTitle(state.draft_artifact?.draft.title || "当前调查");
        return loadM3AnalysisRecords(state.run);
      });
    void request.then((result) => {
        if (!current) return;
        setRecords(result.records);
        setRecordsLoading(false);
      })
      .catch((error) => {
        if (!current) return;
        setRecords([]);
        setRecordsError(error instanceof Error ? error.message : "分析记录加载失败");
        setRecordsLoading(false);
      });
    return () => {
      current = false;
    };
  }, [authoritative, authoritativeRunId, historical, investigationId, searchParams]);

  const riskCounts = useMemo(() => (
    records.reduce<Record<AnalysisRisk, number>>((counts, record) => {
      counts[record.risk] += 1;
      return counts;
    }, { high: 0, medium: 0, low: 0, safe: 0 })
  ), [records]);

  const filteredRecords = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
    const next = records.filter((record) => {
      if (riskFilter !== "all" && record.risk !== riskFilter) return false;
      if (!normalizedQuery) return true;
      return [
        record.contentTitle,
        record.author,
        record.platform,
        record.summary,
        record.conclusion
      ].some((value) => value.toLocaleLowerCase("zh-CN").includes(normalizedQuery));
    });
    return next.sort((left, right) => (
      sort === "risk"
        ? analysisRiskOrder[right.risk] - analysisRiskOrder[left.risk]
          || right.itemNumber - left.itemNumber
        : right.itemNumber - left.itemNumber
    ));
  }, [query, records, riskFilter, sort]);

  const pageCount = Math.max(1, Math.ceil(filteredRecords.length / PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount);
  const visibleRecords = filteredRecords.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => {
    if (page === requestedPage) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (page === 1) next.delete("page");
      else next.set("page", String(page));
      return next;
    }, { replace: true, state: location.state });
  }, [location.state, page, requestedPage, setSearchParams]);

  const updateParam = (key: string, value: string, fallback: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value === fallback || value === "") next.delete(key);
      else next.set(key, value);
      if (key !== "page") next.delete("page");
      return next;
    }, { replace: true, state: location.state });
  };

  const handleViewEvidence = (record: AnalysisRecord) => {
    if (historical) {
      const path = reportAuditDetailPath(record);
      if (path) navigate(path, {state:{returnTo:`${location.pathname}${location.search}`, returnLabel:"全部分析记录", returnTitle:investigationTitle}});
      return;
    }
    if (authoritative && (!record.taskId || !record.outputId)) {
      setSelectedRecord(record);
      return;
    }
    if (!record.taskId || !record.outputId) return;
    const returnTo = `${location.pathname}${location.search}`;
    navigate(`/tasks/${encodeURIComponent(record.taskId)}/outputs/${encodeURIComponent(record.outputId)}`, {
      state: {
        returnTo,
        returnLabel: "全部分析记录",
        returnTitle: investigationTitle
      }
    });
  };

  const handleViewCompleteEvidence = (record: AnalysisRecord) => {
    setSelectedRecord(null);
    if (record.taskId && record.outputId) {
      handleViewEvidence(record);
      return;
    }
    const path = reportPostDetailPath(investigationId, record);
    if (path) navigate(path);
  };

  return (
    <main className="analysis-records-page">
      <header className="analysis-records-topbar">
        <button
          type="button"
          className="analysis-records-back"
          onClick={() => navigate(`/investigation/${encodeURIComponent(investigationId)}`, {
            state: {
              restoreAnalysisProgress: {
                recordCount: records.length,
                investigationTitle
              }
            }
          })}
        >
          <ArrowLeft size={17} />
          <span>返回调查报告</span>
        </button>
        <div className="analysis-records-context">
          <span>调查会话</span>
          <strong>{investigationTitle}</strong>
        </div>
      </header>

      <section className="analysis-records-hero">
        <div className="analysis-records-hero-inner">
          <div className="analysis-records-title-block">
            <span>{historical ? "历史调查" : "本轮调查"}</span>
            <h1>全部分析记录</h1>
            <p>{historical ? "已保存的审核结果 · 不代表历史执行顺序" : `${records[0]?.analyzedAt.split(" ")[0] || "等待分析结果"} · 本轮 Agent 研判批次`}</p>
          </div>
          <div className="analysis-records-total">
            <BarChart3 size={20} />
            <div>
              <span>分析结果总数</span>
              <strong>{records.length}</strong>
            </div>
          </div>
        </div>
      </section>

      <div className="analysis-records-content">
        <section className="analysis-risk-overview" aria-label="风险等级统计">
          {riskFilters.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              className={`${riskFilter === value ? "is-active" : ""} is-${value}`}
              aria-pressed={riskFilter === value}
              onClick={() => updateParam("risk", riskFilter === value ? "all" : value, "all")}
            >
              <span>{label}</span>
              <strong>{riskCounts[value]}</strong>
            </button>
          ))}
        </section>

        <section className="analysis-records-toolbar" aria-label="分析记录筛选">
          <label className="analysis-records-search">
            <Search size={16} />
            <input
              type="search"
              value={query}
              placeholder="搜索内容标题、作者或研判结论"
              onChange={(event) => updateParam("q", event.target.value, "")}
            />
          </label>
          <label className="analysis-records-select">
            <SlidersHorizontal size={15} />
            <span>风险等级</span>
            <select
              value={riskFilter}
              onChange={(event) => updateParam("risk", event.target.value, "all")}
            >
              <option value="all">全部等级</option>
              {riskFilters.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label className="analysis-records-select">
            <ArrowUpDown size={15} />
            <span>排序</span>
            <select value={sort} onChange={(event) => updateParam("sort", event.target.value, "latest")}>
              <option value="latest">{historical ? "记录编号倒序" : "最新完成优先"}</option>
              <option value="risk">风险等级优先</option>
            </select>
          </label>
        </section>

        <div className="analysis-records-list-head">
          <strong>分析结果</strong>
          <span>当前显示 {filteredRecords.length} 条</span>
        </div>

        {recordsLoading ? (
          <div className="analysis-records-empty" role="status">
            <FileSearch size={22} />
            <strong>正在加载当前 Run 的分析记录</strong>
            <span>数据来自已发布的 ReportVersion。</span>
          </div>
        ) : recordsError ? (
          <div className="analysis-records-empty is-error" role="alert">
            <FileSearch size={22} />
            <strong>分析记录暂不可用</strong>
            <span>{recordsError}</span>
          </div>
        ) : visibleRecords.length > 0 ? (
          <section className="analysis-records-list" aria-label="完整分析结果列表">
            {visibleRecords.map((record) => (
              <article key={record.itemNumber} className="analysis-record-row">
                <div className="analysis-record-index">
                  <span>分析结果</span>
                  <strong>#{String(record.itemNumber).padStart(2, "0")}</strong>
                  <div><Clock3 size={13} />{record.analyzedAt.slice(11)}</div>
                </div>

                <div className="analysis-record-main">
                  <div className="analysis-record-title-row">
                    <h2 title={record.contentTitle}>{record.contentTitle}</h2>
                    <span className={`analysis-risk-tag is-${record.risk}`}>{record.riskLabel}</span>
                  </div>
                  <div className="analysis-record-meta">
                    <span>{record.author}</span>
                    <i aria-hidden="true" />
                    <span>{record.platform}</span>
                    <i aria-hidden="true" />
                    <span>{record.analyzedAt}</span>
                  </div>
                  <p>{record.conclusion}</p>
                  <div className="analysis-record-evidence-counts">
                    {analysisEvidenceTypes.map(({ type, label }) => (
                      <span key={type} className={record.evidenceCounts[type] === 0 ? "is-empty" : ""}>
                        {label} {record.evidenceCounts[type]}
                      </span>
                    ))}
                  </div>
                </div>

                <button
                  type="button"
                  className="analysis-record-detail-button"
                  onClick={() => handleViewEvidence(record)}
                >
                  <FileSearch size={16} />
                  <span>{authoritative ? "查看研判依据" : "查看完整证据"}</span>
                </button>
              </article>
            ))}
          </section>
        ) : (
          <div className="analysis-records-empty">
            <Search size={22} />
            <strong>没有匹配的分析结果</strong>
            <span>请调整搜索词或风险等级筛选。</span>
          </div>
        )}

        <nav className="analysis-records-pagination" aria-label="分析记录分页">
          <span>第 {page} / {pageCount} 页</span>
          <div>
            <button
              type="button"
              aria-label="上一页"
              title="上一页"
              disabled={page <= 1}
              onClick={() => updateParam("page", String(page - 1), "1")}
            >
              <ChevronLeft size={17} />
            </button>
            <button
              type="button"
              aria-label="下一页"
              title="下一页"
              disabled={page >= pageCount}
              onClick={() => updateParam("page", String(page + 1), "1")}
            >
              <ChevronRight size={17} />
            </button>
          </div>
        </nav>
      </div>
      <AnalysisBasisDrawer
        record={selectedRecord}
        onClose={() => setSelectedRecord(null)}
        onViewCompleteEvidence={handleViewCompleteEvidence}
      />
    </main>
  );
}
