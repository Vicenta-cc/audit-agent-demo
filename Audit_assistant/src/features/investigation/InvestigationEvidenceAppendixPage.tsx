import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ChevronRight,
  FileSearch,
  List,
  Printer,
  X
} from "lucide-react";
import {
  fetchReportAppendix,
  fetchReportPostDetail,
  fetchReportPresentation
} from "../../services/reports";
import type {
  ReportAppendixPage,
  ReportEvidencePresentation,
  ReportPostDetail,
  ReportPostPresentation,
  ReportPresentationProjection
} from "../../types/reports";
import { resolvePublishedReportVersion } from "./reportRoute";
import { ReportPostSourceDetails } from "./ReportPostSourceDetails";

type AppendixView = "posts" | "standalone" | "evidence";

const viewCopy: Record<AppendixView, { number: string; nav: string; title: string; description: string }> = {
  posts: {
    number: "A.1",
    nav: "相关帖子",
    title: "报告相关帖子",
    description: "按发布报告中的稳定帖子引用列示；选择调查发现时，仅展示该发现的关联帖子。"
  },
  standalone: {
    number: "A.2",
    nav: "独立风险帖子",
    title: "其他独立风险帖子",
    description: "列示未形成共性调查发现、但仍需单独关注的发布报告内容。"
  },
  evidence: {
    number: "A.3",
    nav: "研判依据",
    title: "直接研判依据",
    description: "仅列示发布报告中的直接研判依据；选择调查发现时，仅展示该发现的依据。"
  }
};

const decisionLabels: Record<string, string> = {
  pass: "通过",
  review: "复审",
  reject: "拒绝"
};

const riskLabels: Record<string, string> = {
  high: "高风险",
  medium: "中风险",
  low: "低风险",
  none: "无风险"
};

function isAppendixView(value: string | null): value is AppendixView {
  return value === "posts" || value === "standalone" || value === "evidence";
}

function displayNumber(value: number | null | undefined) {
  return value == null ? "暂不可用" : value.toLocaleString("zh-CN");
}

function RiskTag({ risk }: { risk: string }) {
  return <span className={`r31-risk-tag is-${risk || "none"}`}>{riskLabels[risk] || "未分级"}</span>;
}

function DrawerShell({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [onClose]);

  return createPortal(
    <div className="ethnic-evidence-layer r31-drawer-layer" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <aside className="ethnic-evidence-drawer r31-drawer" role="dialog" aria-modal="true" aria-label={title}>
        <header className="r31-drawer-header">
          <div><span>调查报告附录</span><h2>{title}</h2></div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭" title="关闭"><X size={18} /></button>
        </header>
        <div className="r31-drawer-body">{children}</div>
      </aside>
    </div>,
    document.body
  );
}

function EvidenceRows({ items }: { items: ReportEvidencePresentation[] }) {
  return <div className="r31-evidence-list">{items.map((item) => (
    <article key={item.evidence_ref}>
      <div><span>直接依据</span><small>{item.evidence_type || "审核材料"}</small></div>
      <p>{item.summary || "当前发布报告未提供依据摘要。"}</p>
      {item.original_text ? <details><summary>查看原文</summary><pre>{item.original_text}</pre></details> : null}
      {item.translated_text ? <details><summary>查看译文</summary><pre>{item.translated_text}</pre></details> : null}
    </article>
  ))}</div>;
}

function AppendixPostDrawer({ reportVersionId, postRef, onClose }: { reportVersionId: string; postRef: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ReportPostDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void fetchReportPostDetail(reportVersionId, postRef)
      .then((value) => { if (active) setDetail(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "帖子详情加载失败"); });
    return () => { active = false; };
  }, [postRef, reportVersionId]);
  return (
    <DrawerShell title="帖子详情" onClose={onClose}>
      {!detail && !error ? <p className="r31-drawer-status" role="status">正在加载...</p> : null}
      {error ? <p className="r31-drawer-status is-error" role="alert">{error}</p> : null}
      {detail ? (
        <>
          <div className="r31-post-detail-head">
            <div><h3>{detail.title}</h3>{detail.author_display_name ? <span>作者：{detail.author_display_name}</span> : null}</div>
            <RiskTag risk={detail.risk_level} />
          </div>
          <ReportPostSourceDetails detail={detail} reportVersionId={reportVersionId} />
          {detail.content_summary ? <section className="r31-drawer-section"><h3>内容摘要</h3><p>{detail.content_summary}</p></section> : null}
          <section className="r31-drawer-section">
            <h3>审核结论</h3>
            <p>{decisionLabels[detail.decision] || "审核决定未提供"} · {riskLabels[detail.risk_level] || "未分级"}</p>
            <p>{detail.audit_summary || "原审核结果未提供文字说明。"}</p>
          </section>
          {detail.direct_evidence.length ? (
            <section className="r31-drawer-section"><h3>直接研判依据</h3><EvidenceRows items={detail.direct_evidence} /></section>
          ) : null}
        </>
      ) : null}
    </DrawerShell>
  );
}

function PostRows({ items, onOpen }: { items: ReportPostPresentation[]; onOpen: (ref: string) => void }) {
  return <div className="r31-appendix-list">{items.map((item) => (
    <article className="r31-appendix-row" key={item.post_ref}>
      <div className="r31-appendix-row-head">
        <div><strong>{item.title}</strong>{item.author_display_name ? <small>作者：{item.author_display_name}</small> : null}</div>
        <RiskTag risk={item.risk_level} />
      </div>
      <p>{item.content_summary || item.audit_summary || "当前发布报告未提供内容摘要。"}</p>
      <footer>
        <span>{decisionLabels[item.decision] || "审核决定未提供"}</span>
        <button type="button" onClick={() => onOpen(item.post_ref)}>查看帖子详情 <ChevronRight size={14} /></button>
      </footer>
    </article>
  ))}</div>;
}

export function InvestigationEvidenceAppendixPage() {
  const { investigationId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedView = searchParams.get("view");
  const view: AppendixView = isAppendixView(requestedView) ? requestedView : "posts";
  const findingRef = view === "standalone" ? "" : searchParams.get("finding_ref") || "";
  const explicitReportVersion = searchParams.get("report") || "";
  const requestedPostRef = searchParams.get("post_ref") || "";
  const [reportVersionId, setReportVersionId] = useState("");
  const [report, setReport] = useState<ReportPresentationProjection | null>(null);
  const [page, setPage] = useState<ReportAppendixPage | null>(null);
  const [items, setItems] = useState<ReportAppendixPage["items"]>([]);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const [postRef, setPostRef] = useState("");
  const copy = viewCopy[view];

  useEffect(() => {
    if (reportVersionId) setPostRef(requestedPostRef);
  }, [reportVersionId, requestedPostRef]);

  useEffect(() => {
    let active = true;
    setReport(null);
    setPage(null);
    setItems([]);
    setError("");
    void resolvePublishedReportVersion(investigationId, explicitReportVersion)
      .then(async (version) => {
        const [projection, appendix] = await Promise.all([
          fetchReportPresentation(version),
          fetchReportAppendix(version, { view, finding_ref: findingRef || undefined, limit: 50 })
        ]);
        return { version, projection, appendix };
      })
      .then(({ version, projection, appendix }) => {
        if (!active) return;
        setReportVersionId(version);
        setReport(projection);
        setPage(appendix);
        setItems(appendix.items);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "报告附录加载失败");
      });
    return () => { active = false; };
  }, [explicitReportVersion, findingRef, investigationId, view]);

  useEffect(() => {
    if (!report) return;
    const previousTitle = document.title;
    document.title = `报告附录 · ${report.report_metadata.title}`;
    return () => { document.title = previousTitle; };
  }, [report]);

  const selectView = (nextView: AppendixView) => {
    setSearchParams((current) => {
      const next = new URLSearchParams();
      const reportParam = reportVersionId || current.get("report") || "";
      if (reportParam) next.set("report", reportParam);
      next.set("view", nextView);
      return next;
    });
    setTocOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const loadMore = async () => {
    if (!page?.cursor || !reportVersionId) return;
    setLoadingMore(true);
    setError("");
    try {
      const next = await fetchReportAppendix(reportVersionId, {
        view,
        finding_ref: findingRef || undefined,
        limit: 50,
        cursor: page.cursor
      });
      setItems((current) => [...current, ...next.items]);
      setPage(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更多附录内容加载失败");
    } finally {
      setLoadingMore(false);
    }
  };

  const returnToReport = () => {
    const params = new URLSearchParams();
    if (reportVersionId || explicitReportVersion) params.set("report", reportVersionId || explicitReportVersion);
    navigate(`/investigation/${encodeURIComponent(investigationId)}/report?${params.toString()}`);
  };

  if (error && !report) {
    return <main className="ethnic-report-page"><div className="r31-report-load-state is-error" role="alert"><FileSearch size={22} /><strong>报告附录暂不可用</strong><p>{error}</p><button type="button" onClick={returnToReport}>返回调查报告</button></div></main>;
  }
  if (!report || !page) {
    return <main className="ethnic-report-page"><div className="r31-report-load-state" role="status">正在加载发布报告附录...</div></main>;
  }

  const postItems = page.item_kind === "post" ? items as ReportPostPresentation[] : [];
  const evidenceItems = page.item_kind === "evidence" ? items as ReportEvidencePresentation[] : [];
  const appendixCountLabel = page.item_kind === "evidence" ? "条研判依据" : "条帖子";

  return (
    <main className="ethnic-report-page ethnic-appendix-page r31-appendix-page">
      <header className="ethnic-report-toolbar">
        <button type="button" className="ethnic-report-back" aria-label="返回调查报告" title="返回调查报告" onClick={returnToReport}><ArrowLeft size={17} /><span>返回调查报告</span></button>
        <div className="ethnic-report-toolbar-title"><span>{report.report_metadata.source_name}</span><strong>报告附录</strong></div>
        <div className="r31-toolbar-actions">
          <button type="button" className="r31-toc-toggle" aria-label="打开附录目录" title="打开附录目录" onClick={() => setTocOpen(true)}><List size={16} /><span>目录</span></button>
          <button type="button" className="ethnic-report-export" aria-label="打印附录" title="打印附录" onClick={() => window.print()}><Printer size={16} /><span>打印附录</span></button>
        </div>
      </header>

      <div className="ethnic-report-layout ethnic-appendix-layout">
        {tocOpen ? <button type="button" className="r31-toc-backdrop" aria-label="关闭目录" onClick={() => setTocOpen(false)} /> : null}
        <nav className={`ethnic-report-toc ethnic-appendix-toc r31-report-toc ${tocOpen ? "is-open" : ""}`} aria-label="报告附录目录">
          <div className="r31-toc-heading"><span>附录目录</span><button type="button" onClick={() => setTocOpen(false)} aria-label="关闭目录"><X size={17} /></button></div>
          {(Object.keys(viewCopy) as AppendixView[]).filter((item) => item !== "evidence" || report.appendix.direct_evidence_count > 0).map((item) => (
            <button key={item} type="button" className={view === item ? "is-active" : ""} aria-current={view === item ? "page" : undefined} onClick={() => selectView(item)}>
              <span>{viewCopy[item].number}</span>{viewCopy[item].nav}
            </button>
          ))}
        </nav>

        <article className="ethnic-report-paper ethnic-appendix-paper r31-appendix-paper">
          <header className="ethnic-appendix-document-header r31-appendix-header">
            <span>调查报告附录</span>
            <h1>{copy.title}</h1>
            <p>{report.report_metadata.title}</p>
            <div className="r31-appendix-header-summary">
              共 <strong>{displayNumber(page.matched_count)}</strong> {appendixCountLabel}
              {findingRef ? <span>已按调查发现筛选</span> : null}
            </div>
          </header>

          <section className="ethnic-report-section ethnic-appendix-section r31-report-section">
            <h2><span>{copy.number}</span>{copy.title}</h2>
            <p>{copy.description}</p>
            {findingRef ? <p className="r31-filter-note">当前仅展示所选调查发现的稳定关联记录。</p> : null}
            {error ? <p className="r31-availability-note" role="alert">{error}</p> : null}
            {page.item_kind === "post"
              ? <PostRows items={postItems} onOpen={setPostRef} />
              : <EvidenceRows items={evidenceItems} />}
            {!items.length ? <p className="r31-empty-state">当前筛选范围没有可展示的发布报告记录。</p> : null}
            {page.has_more ? <button type="button" className="r31-load-more" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "正在加载..." : "加载更多"}</button> : null}
          </section>

          <footer className="ethnic-report-footer"><span>{report.report_metadata.title}</span><span>发布报告附录</span></footer>
        </article>
      </div>

      {postRef ? <AppendixPostDrawer reportVersionId={reportVersionId} postRef={postRef} onClose={() => setPostRef("")} /> : null}
    </main>
  );
}
