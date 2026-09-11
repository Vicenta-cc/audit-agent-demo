import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, FileSearch, ListFilter } from "lucide-react";
import type { AnalysisRecord } from "./analysisRecords";
import { loadHistoricalAnalysisRecords } from "./historicalAnalysisRecords";
import { EvidenceRelayPipeline } from "./AgentCollaborationCard";
import { reportAuditDetailPath } from "./m3AnalysisRecords";

export function HistoricalProgressCard({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const [data, setData] = useState<Awaited<ReturnType<typeof loadHistoricalAnalysisRecords>> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    loadHistoricalAnalysisRecords(workspaceId)
      .then(value => { if (active) setData(value); })
      .catch(reason => {
        if (active) setError(reason instanceof Error ? reason.message : "分析记录加载失败");
      });
    return () => { active = false; };
  }, [workspaceId]);

  const openDetail = (record: AnalysisRecord) => {
    const path = reportAuditDetailPath(record);
    if (path) navigate(path, {
      state: {
        returnTo: `/investigation/${encodeURIComponent(workspaceId)}`,
        returnLabel: "调查报告",
        returnTitle: data?.title
      }
    });
  };
  const visibleRecords = data?.records.slice(-3).reverse() || [];

  return (
    <div className="agent-exec-clean-card" aria-label="历史调查进度">
      <div className="agent-exec-clean-head">
        <span className="agent-exec-title">调查流水线已完成</span>
        <span className="agent-exec-status-tag is-done"><CheckCircle2 size={13} />研判完成</span>
      </div>
      {error ? <p role="alert">{error}</p> : !data ? <p role="status">正在读取已保存的分析记录……</p> : <>
        <div className="investigation-run-projection is-completed">
          <div className="investigation-run-summary">
            <strong>报告已发布</strong>
            <span>采集 已有资料 · 分析 完成 · 报告 已生成</span>
          </div>
          {/* Historical counters describe the published report's scope, not a replayed run. */}
          <dl className="investigation-run-metrics" aria-label="纳入报告的帖子进度统计">
            <div><dt>进入研判</dt><dd>{data.record_count}</dd></div>
            <div><dt>待分析</dt><dd>0</dd></div>
            <div><dt>分析中</dt><dd>0</dd></div>
            <div><dt>已完成</dt><dd>{data.record_count}</dd></div>
          </dl>
        </div>
        <EvidenceRelayPipeline isDone authoritative />
        <section className="analysis-feed" aria-label="已保存分析记录">
          <div className="analysis-feed-head">
            <div className="analysis-feed-heading">
              <strong>最新分析进展</strong>
              <span className="analysis-feed-count is-complete">已完成 {data.record_count} / {data.record_count}</span>
            </div>
            <button type="button" className="analysis-history-trigger" onClick={() => navigate(
              `/investigation/${encodeURIComponent(workspaceId)}/analysis-records?historical=1`,
              { state: { records: data.records, investigationTitle: data.title } }
            )}>
              <ListFilter size={14} />查看全部分析记录
            </button>
          </div>
          <div className={`analysis-feed-window is-count-${visibleRecords.length}`}>
            {visibleRecords.map((record, index) => (
              <article className="analysis-feed-item" key={record.postRef}>
                <div className="analysis-feed-row">
                  <span className={`analysis-feed-marker ${index === 0 ? "is-current" : ""}`} />
                  <div className="analysis-feed-copy">
                    <div className="analysis-feed-primary">
                      <strong>第 {record.itemNumber} 条分析完成</strong>
                      <span className={`analysis-risk-tag is-${record.risk}`}>{record.decisionLabel} · {record.riskLabel}</span>
                    </div>
                    <span className="analysis-feed-summary">{record.conclusion}</span>
                  </div>
                  <button type="button" className="analysis-evidence-toggle" onClick={() => openDetail(record)}>
                    <FileSearch size={14} />查看研判依据
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      </>}
    </div>
  );
}
