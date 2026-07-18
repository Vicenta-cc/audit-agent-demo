import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowDownUp,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck
} from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import { EmptyState } from "../../components/feedback/EmptyState";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { fetchAuditResults, fetchJobs, formatDateTime, getPlatformLabel, getResultTime } from "../../services/jobs";
import type { AuditResult, RawJob } from "../../types/jobs";
import type { RiskLevel, TaskOutputItem, TaskOutputSummary } from "../../types/taskOutputs";
import {
  buildOutputSummary,
  getOutputSearchText,
  getRiskLibraries,
  mapAuditResultToTaskOutput
} from "../task-outputs/taskOutputUtils";

type RiskFilter = "全部" | RiskLevel;
type RiskSortKey = "latest" | "risk";

interface RiskFilters {
  query: string;
  level: RiskFilter;
  sort: RiskSortKey;
}

const initialFilters: RiskFilters = {
  query: "",
  level: "全部",
  sort: "latest"
};

const riskLevels: RiskFilter[] = ["全部", "高危", "中危", "待复核", "无风险"];
const sortOptions: Array<{ label: string; value: RiskSortKey }> = [
  { label: "最新发布时间", value: "latest" },
  { label: "风险等级优先", value: "risk" }
];

const riskColors: Record<RiskLevel, string> = {
  高危: "#dc2626",
  中危: "#f59e0b",
  待复核: "#2563eb",
  无风险: "#16a34a"
};

const riskToneMap: Record<RiskLevel, "high" | "medium" | "review" | "safe"> = {
  高危: "high",
  中危: "medium",
  待复核: "review",
  无风险: "safe"
};

const summaryCards = [
  { key: "highRisk", label: "高危内容", level: "高危", Icon: ShieldAlert },
  { key: "mediumRisk", label: "中危内容", level: "中危", Icon: CircleAlert },
  { key: "pendingReview", label: "待复核", level: "待复核", Icon: Clock3 },
  { key: "noRisk", label: "无风险", level: "无风险", Icon: ShieldCheck }
] as const;

export function RiskWorkbenchPage() {
  const navigate = useNavigate();
  const [rawOutputs, setRawOutputs] = useState<AuditResult[]>([]);
  const [jobs, setJobs] = useState<RawJob[]>([]);
  const [apiTotal, setApiTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [draftFilters, setDraftFilters] = useState<RiskFilters>(initialFilters);
  const [filters, setFilters] = useState<RiskFilters>(initialFilters);

  const loadData = useCallback(async (asRefresh = false) => {
    if (asRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError("");
    try {
      const [outputPayload, jobPayload] = await Promise.all([
        fetchAuditResults({ limit: 1000, sort: "latest" }),
        fetchJobs()
      ]);
      setRawOutputs(outputPayload.items || []);
      setApiTotal(outputPayload.total ?? outputPayload.items?.length ?? 0);
      setJobs(jobPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载风险内容失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const outputs = useMemo(() => rawOutputs.map(mapAuditResultToTaskOutput), [rawOutputs]);
  const summary = useMemo(() => buildOutputSummary(outputs, apiTotal || outputs.length), [apiTotal, outputs]);
  const riskLibraries = useMemo(() => getRiskLibraries(outputs), [outputs]);
  const jobsById = useMemo(() => new Map(jobs.map((job) => [job.id, job])), [jobs]);

  const filteredOutputs = useMemo(() => {
    const keyword = filters.query.trim().toLowerCase();
    return outputs
      .filter((item) => {
        const matchesKeyword = !keyword || getOutputSearchText(item).includes(keyword);
        const matchesLevel = filters.level === "全部" || item.riskLevel === filters.level;
        return matchesKeyword && matchesLevel;
      })
      .slice()
      .sort((a, b) => {
        if (filters.sort === "risk") {
          return riskRank(b.riskLevel) - riskRank(a.riskLevel) || parseTime(b.timestamp) - parseTime(a.timestamp);
        }
        return parseTime(b.timestamp) - parseTime(a.timestamp);
      });
  }, [filters, outputs]);

  if (loading && !rawOutputs.length) {
    return <main className="risk-workbench-page"><LoadingState label="正在加载风险研判数据..." /></main>;
  }

  if (error && !rawOutputs.length) {
    return <main className="risk-workbench-page"><ErrorState message={error} onRetry={() => void loadData()} /></main>;
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFilters(draftFilters);
  };

  const handleReset = () => {
    setDraftFilters(initialFilters);
    setFilters(initialFilters);
  };

  const handleViewDetail = (item: TaskOutputItem) => {
    const jobId = String(item.raw.job_id || "");
    if (!jobId) {
      return;
    }
    navigate(`/tasks/${encodeURIComponent(jobId)}/outputs/${encodeURIComponent(item.id)}`);
  };

  return (
    <main className="risk-workbench-page">
      <header className="risk-workbench-header">
        <div>
          <h1>风险研判工作台</h1>
          <p>快速查看风险态势，研判处置风险内容</p>
        </div>
        <IconButton
          type="button"
          aria-label="刷新风险内容"
          onClick={() => void loadData(true)}
          disabled={refreshing}
        >
          <RefreshCw className={refreshing ? "spin" : ""} size={18} />
        </IconButton>
      </header>

      <section className="risk-overview-grid" aria-label="风险内容概览">
        <RiskDonut summary={summary} />
        <RiskMetricStrip summary={summary} />
      </section>

      <form className="risk-filter-panel" onSubmit={handleSubmit}>
        <label className="risk-search-field">
          <Search size={18} />
          <input
            value={draftFilters.query}
            type="search"
            placeholder="搜索标题 / 作者 / 风险库"
            onChange={(event) => setDraftFilters((current) => ({ ...current, query: event.target.value }))}
          />
        </label>

        <label className="risk-select-field">
          <span>风险等级</span>
          <select
            value={draftFilters.level}
            onChange={(event) => setDraftFilters((current) => ({ ...current, level: event.target.value as RiskFilter }))}
          >
            {riskLevels.map((level) => (
              <option key={level}>{level}</option>
            ))}
          </select>
          <ChevronDown size={17} aria-hidden="true" />
        </label>

        <label className="risk-select-field risk-sort-field">
          <ArrowDownUp size={17} aria-hidden="true" />
          <select
            value={draftFilters.sort}
            onChange={(event) => setDraftFilters((current) => ({ ...current, sort: event.target.value as RiskSortKey }))}
            aria-label="排序"
          >
            {sortOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <ChevronDown size={17} aria-hidden="true" />
        </label>

        <div className="risk-filter-actions">
          <Button type="submit" variant="primary">查询</Button>
          <Button type="button" variant="secondary" onClick={handleReset}>重置</Button>
        </div>
      </form>

      <section className="risk-list-section" aria-label="风险内容列表">
        <div className="risk-list-heading">
          <span>共 {filteredOutputs.length.toLocaleString("zh-CN")} 条风险内容</span>
          <span>{riskLibraries.length ? `覆盖 ${riskLibraries.length} 个风险库` : "暂无风险库命中"}</span>
        </div>
        {filteredOutputs.length ? (
          <div className="risk-output-list">
            {filteredOutputs.map((item) => (
              <RiskOutputRow
                key={item.id}
                item={item}
                job={jobsById.get(String(item.raw.job_id || ""))}
                onViewDetail={handleViewDetail}
              />
            ))}
          </div>
        ) : (
          <EmptyState title="暂无匹配内容" description="调整筛选条件后再查看风险内容" />
        )}
      </section>
    </main>
  );
}

function RiskDonut({ summary }: { summary: TaskOutputSummary }) {
  const segments = getSummarySegments(summary);
  return (
    <section className="risk-donut-panel" aria-label="风险内容占比">
      <div className="risk-panel-title">
        <h2>风险内容占比</h2>
        <CheckCircle2 size={18} aria-hidden="true" />
      </div>
      <div className="risk-donut-content">
        <div className="risk-donut-chart" style={{ background: buildDonutGradient(segments) }} aria-hidden="true">
          <div>
            <strong>{summary.total.toLocaleString("zh-CN")}</strong>
            <span>总内容</span>
          </div>
        </div>
        <div className="risk-donut-legend">
          {segments.map((item) => (
            <div className="risk-donut-legend-item" key={item.level}>
              <span className={`risk-dot risk-dot-${riskToneMap[item.level]}`} aria-hidden="true" />
              <strong>{item.level}</strong>
              <span>{formatPercent(item.value, summary.total)}</span>
              <em>({item.value.toLocaleString("zh-CN")})</em>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function RiskMetricStrip({ summary }: { summary: TaskOutputSummary }) {
  return (
    <section className="risk-metric-strip" aria-label="风险等级统计">
      {summaryCards.map((item) => {
        const Icon = item.Icon;
        const value = summary[item.key];
        const tone = riskToneMap[item.level];
        return (
          <div className={`risk-metric-item risk-metric-${tone}`} key={item.key}>
            <div className="risk-metric-icon" aria-hidden="true">
              <Icon size={22} />
            </div>
            <div className="risk-metric-copy">
              <span>{item.label}</span>
              <strong>{value.toLocaleString("zh-CN")}</strong>
              <small>占比 {formatPercent(value, summary.total)}</small>
            </div>
          </div>
        );
      })}
    </section>
  );
}

function RiskOutputRow({
  item,
  job,
  onViewDetail
}: {
  item: TaskOutputItem;
  job?: RawJob;
  onViewDetail: (item: TaskOutputItem) => void;
}) {
  const tone = riskToneMap[item.riskLevel];
  const jobName = job?.display_name || job?.keyword || job?.input_filename || item.raw.job_id || "平台内容抓取任务";
  const platform = getPlatformLabel(item.raw.platform, job?.input_type || "");
  const evidenceTotal = Object.values(item.evidenceCounts).reduce((sum, value) => sum + value, 0);

  return (
    <article className={`risk-output-row risk-row-${tone}`}>
      <div className="risk-output-level">
        <span className={`risk-level-tag is-${tone}`}>{item.riskLevel}</span>
      </div>
      <div className="risk-output-main">
        <div className="risk-output-title-line">
          {platform && platform !== "-" ? <span className="risk-platform-badge">{platform}</span> : null}
          <h3 title={item.title}>{item.title}</h3>
        </div>
        {item.summary ? <p title={item.summary}>{item.summary}</p> : null}
        <div className="risk-output-meta">
          <span className="risk-output-meta-item" title={`来源任务：${jobName}`}>来源任务：{jobName}</span>
          <span className="risk-output-meta-item" title={`发布人：${item.author}`}>发布人：{item.author}</span>
          <span className="risk-output-meta-item" title={`命中证据：${evidenceTotal}`}>命中证据：{evidenceTotal}</span>
          <span className="risk-output-meta-item" title={`发布时间：${formatRiskTime(item)}`}>发布时间：{formatRiskTime(item)}</span>
        </div>
      </div>
      <div className="risk-output-side">
        <span>{item.riskLibrary?.label || "未命中风险库"}</span>
        <Button type="button" variant="secondary" size="small" onClick={() => onViewDetail(item)} disabled={!item.raw.job_id}>
          查看详情
        </Button>
      </div>
    </article>
  );
}

function getSummarySegments(summary: TaskOutputSummary) {
  return [
    { level: "高危" as const, value: summary.highRisk },
    { level: "中危" as const, value: summary.mediumRisk },
    { level: "待复核" as const, value: summary.pendingReview },
    { level: "无风险" as const, value: summary.noRisk }
  ];
}

function buildDonutGradient(segments: Array<{ level: RiskLevel; value: number }>) {
  const total = segments.reduce((sum, item) => sum + item.value, 0);
  if (!total) {
    return "conic-gradient(#e2e8f0 0deg 360deg)";
  }
  let cursor = 0;
  const stops = segments.map((item) => {
    const start = cursor;
    const size = (item.value / total) * 360;
    cursor += size;
    return `${riskColors[item.level]} ${start}deg ${cursor}deg`;
  });
  return `conic-gradient(${stops.join(", ")})`;
}

function formatPercent(value: number, total: number) {
  if (!total) {
    return "0%";
  }
  return `${((value / total) * 100).toFixed(1)}%`;
}

function formatRiskTime(item: TaskOutputItem) {
  const value = getResultTime(item.raw) || item.timestamp;
  return value ? formatDateTime(value) : "--";
}

function riskRank(level: RiskLevel) {
  return { 高危: 4, 中危: 3, 待复核: 2, 无风险: 1 }[level];
}

function parseTime(value: string) {
  const timestamp = Date.parse(value || "");
  return Number.isFinite(timestamp) ? timestamp : 0;
}
