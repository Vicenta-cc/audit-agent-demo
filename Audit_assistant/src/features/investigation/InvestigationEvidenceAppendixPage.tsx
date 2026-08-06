import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ChevronLeft, ChevronRight } from "lucide-react";
import {
  getListViewUrl,
  rememberListScroll,
  useRestoreListScroll
} from "../../app/listNavigation";
import { fetchAuditResults, getPlatformLabel } from "../../services/jobs";
import type { AuditResult } from "../../types/jobs";
import { getOutputKey, getOutputTitle } from "../task-outputs/taskOutputUtils";

type AppendixSectionId = "normal" | "comments" | "identity" | "relations" | "cases";

interface AppendixRecord {
  result: AuditResult;
  subject: string;
}

const PAGE_SIZE = 12;
const IDENTITY_RESULT_IDS = new Set(["366", "372"]);
const CASE_RESULT_IDS = new Set(["21"]);
const COMMENT_RISK_JOB_IDS = new Set(["3ad102e072f6", "8bc179209e1e"]);
const noRiskValues = new Set(["", "none", "safe", "pass", "unknown"]);

const reportSources = [
  { jobId: "3ad102e072f6", subject: "我的心好累" },
  { jobId: "8bc179209e1e", subject: "麦热依姆古丽" },
  { jobId: "eafef54edea2", subject: "石榴红缘" },
  { jobId: "80faaba76135", subject: "萨娅" }
] as const;

const appendixSections: Array<{
  id: AppendixSectionId;
  number: string;
  navLabel: string;
  title: string;
  description: string;
}> = [
  {
    id: "normal",
    number: "4.1",
    navLabel: "正常内容",
    title: "正常婚恋与家庭生活内容",
    description: "641 条内容未发现风险，覆盖跨民族婚礼、领证、夫妻日常、亲友互动和公益互助等主题。"
  },
  {
    id: "comments",
    number: "4.2",
    navLabel: "评论风险",
    title: "由评论互动触发的风险内容",
    description: "25 条内容以评论为主要风险证据，共保存 46 条风险评论片段。列表按内容归组，证据片段在详情页完整呈现。"
  },
  {
    id: "identity",
    number: "4.3",
    navLabel: "民族与地域线索",
    title: "民族刻板印象与地域排斥线索",
    description: "现有样本发现一条中风险民族刻板印象评论和一条中风险地域攻击评论，均未跨对象重复出现。"
  },
  {
    id: "relations",
    number: "4.4",
    navLabel: "共同互动统计",
    title: "共同互动账号与关联边界",
    description: "共同互动能够说明受众交叉，但现有风险评论没有跨对象复现，尚不能据此构成关联网络结论。"
  },
  {
    id: "cases",
    number: "7",
    navLabel: "其他研判案例",
    title: "其他典型内容与结论边界",
    description: "收录与民族议题无直接关联、但仍需在报告中说明的低风险线索，避免将一般平台引流扩大为民族关系风险。"
  }
];

const relationFacts = [
  { label: "去重互动账号", value: "7,450", note: "四个重点对象的评论账号去重统计" },
  { label: "跨两个及以上对象", value: "131", note: "具有共同受众特征的互动账号" },
  { label: "跨三个对象", value: "1", note: "样本中的最高对象覆盖数量" },
  { label: "风险评论跨对象", value: "0", note: "未发现风险评论账号跨对象出现" }
];

const relationObservations = [
  {
    title: "🌺🌹红花🌹🌺",
    meta: "覆盖 3 个对象 · 共 56 条评论",
    conclusion: "现有评论均未命中民族攻击、侮辱歧视或煽动对立，判断为高频共同受众。",
    label: "无风险"
  },
  {
    title: "维汉胡胡~招红娘",
    meta: "发布 1 条内容 · 留下 187 条评论",
    conclusion: "个人资料指向双方关系，可作为后续身份核验线索，现阶段不认定为已确认关联账号。",
    label: "待核验"
  }
];

export function InvestigationEvidenceAppendixPage() {
  const { investigationId = "session-ethnic-relations" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [records, setRecords] = useState<AppendixRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sectionParam = searchParams.get("section");
  const activeSectionId = appendixSections.some((section) => section.id === sectionParam)
    ? sectionParam as AppendixSectionId
    : "normal";
  const activeSection = appendixSections.find((section) => section.id === activeSectionId) || appendixSections[0];
  const focusId = searchParams.get("focus") || "";
  const viewUrl = getListViewUrl(location);

  const loadRecords = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const payloads = await Promise.all(reportSources.map(async (source) => {
        const payload = await fetchAuditResults({
          jobId: source.jobId,
          limit: 1000,
          sort: "latest",
          compact: true
        });
        return (payload.items || []).map((result) => ({ result, subject: source.subject }));
      }));
      setRecords(payloads.flat());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "证据附录加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadRecords(); }, [loadRecords]);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "证据附录 · 维汉民族关系专项调查";
    return () => { document.title = previousTitle; };
  }, []);

  const normalRecords = useMemo(
    () => records.filter(({ result }) => isNoRiskResult(result)),
    [records]
  );
  const commentRiskRecords = useMemo(
    () => records
      .filter(({ result }) => COMMENT_RISK_JOB_IDS.has(String(result.job_id || "")) && !isNoRiskResult(result))
      .sort(compareRiskRecords),
    [records]
  );
  const identityRecords = useMemo(
    () => records
      .filter(({ result }) => IDENTITY_RESULT_IDS.has(getOutputKey(result)))
      .sort((left, right) => getOutputKey(right.result).localeCompare(getOutputKey(left.result))),
    [records]
  );
  const caseRecords = useMemo(
    () => records.filter(({ result }) => CASE_RESULT_IDS.has(getOutputKey(result))),
    [records]
  );

  const sectionRecords = activeSectionId === "normal"
    ? normalRecords
    : activeSectionId === "comments"
      ? commentRiskRecords
      : activeSectionId === "identity"
        ? identityRecords
        : activeSectionId === "cases"
          ? caseRecords
          : [];
  const visibleSectionRecords = prioritizeFocusedRecord(sectionRecords, focusId);
  const pageCount = Math.max(1, Math.ceil(visibleSectionRecords.length / PAGE_SIZE));
  const requestedPage = Math.max(1, Number(searchParams.get("page")) || 1);
  const focusedIndex = focusId
    ? visibleSectionRecords.findIndex(({ result }) => getOutputKey(result) === focusId)
    : -1;
  const derivedPage = !searchParams.has("page") && focusedIndex >= 0
    ? Math.floor(focusedIndex / PAGE_SIZE) + 1
    : requestedPage;
  const page = Math.min(derivedPage, pageCount);
  const pageRecords = visibleSectionRecords.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useRestoreListScroll(viewUrl, !loading && !error);

  const selectSection = (sectionId: AppendixSectionId) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("section", sectionId);
      next.delete("focus");
      next.delete("page");
      return next;
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const selectPage = (nextPage: number) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (nextPage <= 1) next.delete("page");
      else next.set("page", String(nextPage));
      next.delete("focus");
      return next;
    }, { replace: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const openDetail = ({ result }: AppendixRecord) => {
    const taskId = String(result.job_id || "");
    const outputId = getOutputKey(result);
    if (!taskId || !outputId) return;
    rememberListScroll(viewUrl);
    navigate(`/tasks/${encodeURIComponent(taskId)}/outputs/${encodeURIComponent(outputId)}`, {
      state: {
        returnTo: viewUrl,
        returnLabel: "证据附录",
        returnTitle: "维汉民族关系专项调查"
      }
    });
  };

  const sectionCount = activeSectionId === "relations" ? "131 个共同互动账号" : `${visibleSectionRecords.length} 条内容记录`;
  const evidenceCount = commentRiskRecords.reduce((sum, { result }) => sum + Number(result.evidence_count || 0), 0);

  return (
    <main className="ethnic-report-page ethnic-appendix-page">
      <header className="ethnic-report-toolbar">
        <button
          type="button"
          className="ethnic-report-back"
          onClick={() => navigate(`/investigation/${encodeURIComponent(investigationId)}/report?section=report-findings`)}
        >
          <ArrowLeft size={17} />
          <span>返回调查报告</span>
        </button>
        <div className="ethnic-report-toolbar-title">
          <span>专项调查报告</span>
          <strong>证据附录</strong>
        </div>
        <div aria-hidden="true" />
      </header>

      <div className="ethnic-report-layout ethnic-appendix-layout">
        <nav className="ethnic-report-toc ethnic-appendix-toc" aria-label="证据附录目录">
          <div>附录目录</div>
          {appendixSections.map((section) => (
            <button
              key={section.id}
              type="button"
              className={activeSectionId === section.id ? "is-active" : ""}
              aria-current={activeSectionId === section.id ? "page" : undefined}
              onClick={() => selectSection(section.id)}
            >
              <span>{section.number}</span>
              {section.navLabel}
            </button>
          ))}
        </nav>

        <article className="ethnic-report-paper ethnic-appendix-paper">
          <header className="ethnic-appendix-document-header">
            <span>调查报告附录</span>
            <h1>证据附录</h1>
            <p>维汉民族关系专项调查</p>
            <dl>
              <div><dt>研判内容</dt><dd>667 条</dd></div>
              <div><dt>风险评论证据</dt><dd>{loading ? "--" : `${evidenceCount} 条`}</dd></div>
              <div><dt>报告日期</dt><dd>2026 年 7 月 29 日</dd></div>
            </dl>
          </header>

          <section className="ethnic-report-section ethnic-appendix-section">
            <h2><span>{activeSection.number}</span>{activeSection.title}</h2>
            <p>{activeSection.description}</p>
            <div className="ethnic-appendix-section-meta">
              <strong>{loading ? "正在核对记录" : sectionCount}</strong>
              {activeSectionId === "comments" && !loading ? <span>共 {evidenceCount} 条风险评论证据</span> : null}
              {activeSectionId === "normal" && !loading ? <span>占全部研判内容的 96.1%</span> : null}
              {activeSectionId === "identity" && !loading ? <span>均为中风险评论线索</span> : null}
              {activeSectionId === "cases" && !loading ? <span>与民族议题无直接关联</span> : null}
            </div>

            {activeSectionId === "relations" ? (
              <RelationEvidence />
            ) : loading ? (
              <div className="ethnic-appendix-status" role="status">正在加载证据记录...</div>
            ) : error ? (
              <div className="ethnic-appendix-status is-error" role="alert">
                <p>{error}</p>
                <button type="button" onClick={() => void loadRecords()}>重新加载</button>
              </div>
            ) : (
              <>
                <div className="ethnic-appendix-list" aria-label={`${activeSection.title}列表`}>
                  {pageRecords.map((record, index) => {
                    const outputId = getOutputKey(record.result);
                    return (
                      <button
                        key={`${record.result.job_id}:${outputId}`}
                        type="button"
                        className={`ethnic-appendix-row ${focusId === outputId ? "is-focused" : ""}`}
                        onClick={() => openDetail(record)}
                      >
                        <span className="ethnic-appendix-row-index">
                          {String((page - 1) * PAGE_SIZE + index + 1).padStart(2, "0")}
                        </span>
                        <span className="ethnic-appendix-row-main">
                          <strong>{getOutputTitle(record.result)}</strong>
                          <small>{buildRecordMeta(record, activeSectionId)}</small>
                          <span>{record.result.summary || "已完成内容与上下文研判。"}</span>
                        </span>
                        <span className="ethnic-appendix-row-action">
                          <i className={`is-${riskTone(record.result)}`}>{riskLabel(record.result)}</i>
                          <ChevronRight size={17} />
                        </span>
                      </button>
                    );
                  })}
                </div>

                {pageCount > 1 ? (
                  <nav className="ethnic-appendix-pagination" aria-label="证据列表分页">
                    <span>第 {page} / {pageCount} 页</span>
                    <div>
                      <button
                        type="button"
                        aria-label="上一页"
                        title="上一页"
                        disabled={page <= 1}
                        onClick={() => selectPage(page - 1)}
                      >
                        <ChevronLeft size={17} />
                      </button>
                      <button
                        type="button"
                        aria-label="下一页"
                        title="下一页"
                        disabled={page >= pageCount}
                        onClick={() => selectPage(page + 1)}
                      >
                        <ChevronRight size={17} />
                      </button>
                    </div>
                  </nav>
                ) : null}
              </>
            )}
          </section>

          <footer className="ethnic-report-footer">
            <span>维汉民族关系专项调查 · 证据附录</span>
            <span>内部研判资料</span>
          </footer>
        </article>
      </div>
    </main>
  );
}

function RelationEvidence() {
  return (
    <div className="ethnic-appendix-relations">
      <dl className="ethnic-appendix-relation-facts">
        {relationFacts.map((fact) => (
          <div key={fact.label}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
            <span>{fact.note}</span>
          </div>
        ))}
      </dl>

      <div className="ethnic-appendix-subheading">
        <strong>重点账号观察</strong>
        <span>用于说明共同受众与关联结论之间的边界</span>
      </div>
      <div className="ethnic-appendix-observations">
        {relationObservations.map((item, index) => (
          <article key={item.title}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <strong>{item.title}</strong>
              <small>{item.meta}</small>
              <p>{item.conclusion}</p>
            </div>
            <i>{item.label}</i>
          </article>
        ))}
      </div>
      <p className="ethnic-appendix-boundary">
        现有 46 条风险评论均未跨对象出现。上述账号交叉只支持共同受众判断，不作为协同攻击或关联网络认定依据。
      </p>
    </div>
  );
}

function isNoRiskResult(result: AuditResult) {
  const decision = String(result.decision || "").toLowerCase();
  const riskLevel = String(result.risk_level || "").toLowerCase();
  return decision === "pass" || noRiskValues.has(riskLevel);
}

function compareRiskRecords(left: AppendixRecord, right: AppendixRecord) {
  return riskRank(right.result) - riskRank(left.result)
    || Number(right.result.evidence_count || 0) - Number(left.result.evidence_count || 0)
    || Number(getOutputKey(right.result)) - Number(getOutputKey(left.result));
}

function riskRank(result: AuditResult) {
  return { high: 3, medium: 2, low: 1 }[String(result.risk_level || "").toLowerCase()] || 0;
}

function riskTone(result: AuditResult) {
  const risk = String(result.risk_level || "").toLowerCase();
  if (risk === "high") return "high";
  if (risk === "medium") return "medium";
  if (risk === "low" || risk === "review") return "low";
  return "safe";
}

function riskLabel(result: AuditResult) {
  const tone = riskTone(result);
  return { high: "高风险", medium: "中风险", low: "低风险", safe: "无风险" }[tone];
}

function buildRecordMeta(record: AppendixRecord, sectionId: AppendixSectionId) {
  const result = record.result;
  const base = [record.subject, getPlatformLabel(result.platform)];
  if (sectionId === "comments" || sectionId === "identity") {
    base.push(`${Number(result.evidence_count || 0)} 条评论证据`);
  }
  if (sectionId === "cases") base.push(`${Number(result.evidence_count || 0)} 条画面文字证据`);
  return base.join(" · ");
}

function prioritizeFocusedRecord(records: AppendixRecord[], focusId: string) {
  if (!focusId) return records;
  const focusedIndex = records.findIndex(({ result }) => getOutputKey(result) === focusId);
  if (focusedIndex <= 0) return records;
  return [records[focusedIndex], ...records.slice(0, focusedIndex), ...records.slice(focusedIndex + 1)];
}
