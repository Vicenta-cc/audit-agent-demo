import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, FileSearch, ListFilter, Loader2, Pause, Play, ScrollText } from "lucide-react";
import { useNavigate } from "react-router-dom";
import type { AgentExecutionPhase } from "../../types/investigation";
import type { InvestigationRunProjection } from "../../types/investigationCreation";
import { mapInvestigationRunState, runOutcomePresentation } from "./investigationRunState";
import { AnalysisBasisDrawer } from "./AnalysisBasisDrawer";
import {
  createAnalysisRecord,
  createCompletedAnalysisRecords,
  readStoredAnalysisRecords,
  storeAnalysisRecords,
  type AnalysisScenario,
  type AnalysisRecord
} from "./analysisRecords";
import { loadM3AnalysisRecords, readRunAnalysisCounts, reportPostDetailPath } from "./m3AnalysisRecords";
import { buildRunProgressItems, formatRunFailureMessage } from "./runPresentation";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import { endTask, controlJob } from "../../services/jobs";

export { buildRunProgressItems, formatRunFailureMessage } from "./runPresentation";

const ETHNIC_RELATIONS_TOTAL = 667;
const ETHNIC_RELATIONS_DEMO_RECORDS = 10;

interface AgentCollaborationCardProps {
  phase: AgentExecutionPhase;
  onPhaseChange?: (newPhase: AgentExecutionPhase) => void;
  onCompleted?: () => void;
  taskName?: string;
  platformsText?: string;
  keywordsCount?: number;
  ruleSetName?: string;
  investigationId: string;
  investigationTitle: string;
  authoritative?: boolean;
  run?: InvestigationRunProjection;
  onControlAccepted?: () => void;
}

export function EvidenceRelayPipeline({
  isDone,
  auditCompleted = false,
  isStopped = false,
  authoritative = false,
  activeStep = 1,
  isQueued = false
}: {
  isDone: boolean;
  auditCompleted?: boolean;
  isStopped?: boolean;
  authoritative?: boolean;
  activeStep?: 1 | 2 | 3 | 4;
  isQueued?: boolean;
}) {
  const relayStartDelays = [0, 1.9, 3.8];

  return (
    <div className="evidence-pipeline" aria-label="证据接力流水线">
      <svg
        viewBox="0 0 640 112"
        className={`evidence-pipeline-svg ${isDone ? "is-done" : auditCompleted ? "is-audit-completed" : isStopped ? "is-stopped" : isQueued ? "is-queued" : "is-running"}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <text x="76" y="19" className="pipeline-node-label">数据采集</text>
        <text x="236" y="19" className="pipeline-node-label">证据分析</text>
        <text x="404" y="19" className="pipeline-node-label">风险研判</text>
        <text x="564" y="19" className="pipeline-node-label">报告归纳</text>

        <line x1="100" y1="66" x2="212" y2="66" className="pipeline-track" />
        <line x1="260" y1="66" x2="380" y2="66" className="pipeline-track" />
        <line x1="428" y1="66" x2="540" y2="66" className="pipeline-track" />
        {!isDone && !auditCompleted && !isStopped && !isQueued ? (
          <g aria-hidden="true">
            {(!authoritative || activeStep >= 2) ? <line x1="100" y1="66" x2="212" y2="66" className="pipeline-track-flow pipeline-track-flow--1" /> : null}
            {(!authoritative || activeStep >= 3) ? <line x1="260" y1="66" x2="380" y2="66" className="pipeline-track-flow pipeline-track-flow--2" /> : null}
            {(!authoritative || activeStep >= 4) ? <line x1="428" y1="66" x2="540" y2="66" className="pipeline-track-flow pipeline-track-flow--3" /> : null}
          </g>
        ) : null}

        <g className={`pipeline-node pipeline-node--source ${isDone || auditCompleted || activeStep > 1 ? "is-done" : ""} ${!isDone && !auditCompleted && !isStopped && !isQueued && activeStep === 1 ? "is-active" : ""}`}>
          <circle cx="76" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(76, 66)">
            <circle cx="0" cy="0" r="14" className="pipeline-radar-ring" />
            <circle cx="0" cy="0" r="8" className="pipeline-radar-ring pipeline-radar-ring--inner" />
            <path d="M 0 0 L 14 0 A 14 14 0 0 0 0 -14 Z" className="pipeline-radar-sweep" />
            <circle cx="0" cy="0" r="2.5" className="pipeline-radar-dot" />
          </g>
        </g>

        <g className={`pipeline-node pipeline-node--analysis ${isDone || auditCompleted || activeStep > 2 ? "is-done" : ""} ${!isDone && !auditCompleted && !isStopped && !isQueued && activeStep === 2 ? "is-active" : ""}`}>
          <circle cx="236" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(236, 66)" className="pipeline-analysis-glyph">
            <path d="M 0 -9 L 8 0 L 0 9 L -8 0 Z" />
            <circle cx="0" cy="0" r="2.5" />
          </g>
        </g>

        <g className={`pipeline-node pipeline-node--risk ${isDone || auditCompleted || activeStep > 3 ? "is-done" : ""} ${!isDone && !auditCompleted && !isStopped && !isQueued && (activeStep === 3 || (authoritative && activeStep === 2)) ? "is-active" : ""}`}>
          <circle cx="404" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(404, 66)" className="pipeline-risk-glyph">
            <path d="M -9 1 A 9 9 0 0 1 9 1" />
            <path d="M -6 6 A 8 8 0 0 0 6 6" />
            <line x1="0" y1="-8" x2="0" y2="8" />
            <circle cx="0" cy="1" r="2.5" />
          </g>
        </g>

        <g className={`pipeline-node pipeline-node--report ${isDone ? "is-done" : ""} ${!isDone && !isStopped && !isQueued && activeStep === 4 ? "is-active" : ""}`}>
          <circle cx="564" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(564, 66)" className="pipeline-report-glyph">
            <rect x="-7" y="-11" width="18" height="24" rx="2" className="pipeline-report-back" />
            <g className="pipeline-report-sheet">
              <rect x="-11" y="-13" width="18" height="24" rx="2" className="pipeline-report-front" />
              <line x1="-7" y1="-7" x2="3" y2="-7" className="pipeline-report-line pipeline-report-line--1" />
              <line x1="-7" y1="-2" x2="1" y2="-2" className="pipeline-report-line pipeline-report-line--2" />
              <line x1="-7" y1="3" x2="4" y2="3" className="pipeline-report-line pipeline-report-line--3" />
            </g>
            {isDone ? (
              <g className="pipeline-report-seal" transform="translate(7, 8)">
                <circle cx="0" cy="0" r="6" />
                <path d="M -3 -0.5 L -1 2 L 3 -2" />
              </g>
            ) : null}
          </g>
        </g>

        {!isDone && !auditCompleted && !isStopped && !isQueued ? (
          <g className="pipeline-relay-layer" aria-hidden="true">
            {relayStartDelays.map((delay, index) => (
              <g key={index} className="pipeline-relay-item" style={{ animationDelay: `${delay}s` }}>
                <circle r="4.5" className="relay-content-dot" style={{ animationDelay: `${delay}s` }} />
                <circle r="2.4" className="relay-sub-particle relay-sub-particle--1" style={{ animationDelay: `${delay}s` }} />
                <circle r="2.4" className="relay-sub-particle relay-sub-particle--2" style={{ animationDelay: `${delay}s` }} />
                <circle r="2.4" className="relay-sub-particle relay-sub-particle--3" style={{ animationDelay: `${delay}s` }} />
                <polygon points="0,-7 7,0 0,7 -7,0" className="relay-crystal" style={{ animationDelay: `${delay}s` }} />
                <g className="relay-scan-beam" style={{ animationDelay: `${delay}s` }}>
                  <rect x="-15" y="-2.5" width="30" height="5" rx="2.5" className="relay-scan-band" />
                  <path d="M -14 0 Q 0 4 14 0" className="relay-scan-arc" />
                </g>
                <path d="M 0 -7 L 7 -3 L 5 6 L 0 9 L -5 6 L -7 -3 Z" className="relay-verdict-mark" style={{ animationDelay: `${delay}s` }} />
              </g>
            ))}
          </g>
        ) : null}
      </svg>
    </div>
  );
}

export function AgentCollaborationCard({
  phase,
  onPhaseChange,
  onCompleted,
  investigationId,
  investigationTitle,
  authoritative = false,
  run,
  onControlAccepted
}: AgentCollaborationCardProps) {
  const navigate = useNavigate();
  const scenario: AnalysisScenario = investigationTitle.includes("维汉民族关系")
    ? "ethnic-relations"
    : "default";
  const isEthnicRelationsDemo = scenario === "ethnic-relations";
  const timerRef = useRef<number | null>(null);
  const phaseRef = useRef(phase);
  const [analysisRecords, setAnalysisRecords] = useState<AnalysisRecord[]>(() => (
    authoritative
      ? []
      : readStoredAnalysisRecords(investigationId)
      || (phase === "completed"
        ? createCompletedAnalysisRecords(isEthnicRelationsDemo ? ETHNIC_RELATIONS_DEMO_RECORDS : 4, scenario)
        : [])
  ));
  const nextRecordNumberRef = useRef(
    Math.max(0, ...analysisRecords.map((record) => record.itemNumber)) + 1
  );
  const [feedRevision, setFeedRevision] = useState(0);
  const [selectedRecord, setSelectedRecord] = useState<AnalysisRecord | null>(null);
  const [authoritativeCounts, setAuthoritativeCounts] = useState(() => (
    run ? readRunAnalysisCounts(run) : { completedCount: 0, totalCount: 0 }
  ));
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState("");
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [endSubmitted, setEndSubmitted] = useState(false);
  useEffect(() => { setEndSubmitted(false); }, [run?.run_id]);
  const [controlAction, setControlAction] = useState("");
  const [controlError, setControlError] = useState("");
  const [logsExpanded, setLogsExpanded] = useState(false);

  phaseRef.current = phase;

  useEffect(() => {
    if (authoritative) return;
    storeAnalysisRecords(investigationId, analysisRecords);
  }, [analysisRecords, authoritative, investigationId]);

  useEffect(() => {
    if (!authoritative || !run) return;
    const runCounts = readRunAnalysisCounts(run);
    setAuthoritativeCounts(runCounts);
    setRecordsError("");
    if (run.audit_results.length === 0 && run.status !== "AUDIT_COMPLETED" && (run.status !== "PUBLISHED" || !run.report_version_id)) {
      setAnalysisRecords([]);
      setRecordsLoading(false);
      return;
    }
    let current = true;
    setRecordsLoading(true);
    void loadM3AnalysisRecords(run)
      .then((result) => {
        if (!current) return;
        setAnalysisRecords(result.records);
        setAuthoritativeCounts({
          completedCount: result.completedCount,
          totalCount: result.totalCount
        });
        setRecordsLoading(false);
      })
      .catch((error) => {
        if (!current) return;
        setAnalysisRecords([]);
        setRecordsError(error instanceof Error ? error.message : "分析记录加载失败");
        setRecordsLoading(false);
      });
    return () => {
      current = false;
    };
  }, [authoritative, run?.run_id, run?.status, run?.report_version_id, run?.updated_at, run?.audit_results.length]);

  useEffect(() => {
    if (phase !== "collection_waking") return;
    setAnalysisRecords([]);
    nextRecordNumberRef.current = 1;
    setFeedRevision((current) => current + 1);
  }, [phase]);

  // Auto progression through phases
  useEffect(() => {
    if (authoritative) return;
    if (phase === "completed") return;

    const phaseTimings: Record<string, { next: AgentExecutionPhase; delay: number }> = {
      idle: { next: "collection_working", delay: 800 },
      collection_waking: { next: "collection_working", delay: 800 },
      collection_working: { next: "evidence_working", delay: 1800 },
      collection_completed: { next: "evidence_working", delay: 600 },
      evidence_handoff: { next: "evidence_working", delay: 600 },
      evidence_working: { next: "audit_working", delay: 2000 },
      evidence_completed: { next: "audit_working", delay: 600 },
      audit_handoff: { next: "audit_working", delay: 600 },
      audit_working: { next: "report_generating", delay: isEthnicRelationsDemo ? 9400 : 2400 },
      evidence_requested: { next: "audit_working", delay: 1000 },
      evidence_supplementing: { next: "audit_working", delay: 1200 },
      audit_resumed: { next: "audit_working", delay: 1000 },
      audit_completed: { next: "report_generating", delay: 600 },
      report_handoff: { next: "report_generating", delay: 600 },
      report_generating: { next: "completed", delay: 2200 }
    };

    const currentConfig = phaseTimings[phase];
    if (currentConfig && currentConfig.delay > 0) {
      timerRef.current = window.setTimeout(() => {
        if (onPhaseChange) onPhaseChange(currentConfig.next);
        if (currentConfig.next === "completed" && onCompleted) {
          onCompleted();
        }
      }, currentConfig.delay);
    }

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [authoritative, isEthnicRelationsDemo, phase, onPhaseChange, onCompleted]);

  useEffect(() => {
    if (authoritative) return;
    const isAnalysisPhase = isEthnicRelationsDemo
      ? phase === "audit_working"
      : phase.startsWith("audit") || phase.startsWith("report");
    if (!isAnalysisPhase) return;

    let cancelled = false;
    let broadcastTimer: number | null = null;

    const publishNextRecord = () => {
      if (cancelled || phaseRef.current === "completed") return;

      if (isEthnicRelationsDemo && nextRecordNumberRef.current > ETHNIC_RELATIONS_DEMO_RECORDS) return;

      const nextRecord = createAnalysisRecord(nextRecordNumberRef.current, scenario);
      nextRecordNumberRef.current += 1;
      setAnalysisRecords((current) => [nextRecord, ...current]);
      setFeedRevision((current) => current + 1);

      broadcastTimer = window.setTimeout(publishNextRecord, isEthnicRelationsDemo ? 850 : 1300);
    };

    const firstResultDelay = isEthnicRelationsDemo ? 320 : (phase.startsWith("audit") ? 850 : 500);
    broadcastTimer = window.setTimeout(publishNextRecord, firstResultDelay);

    return () => {
      cancelled = true;
      if (broadcastTimer) window.clearTimeout(broadcastTimer);
    };
  }, [authoritative, isEthnicRelationsDemo, phase, scenario]);

  // Determine active step (1-4)
  const getStepStatus = (): { step: 1 | 2 | 3 | 4; done: boolean } => {
    if (phase === "completed") {
      return { step: 4, done: true };
    }
    if (phase.startsWith("collection") || phase === "idle") {
      return { step: 1, done: false };
    }
    if (phase.startsWith("evidence")) {
      return { step: 2, done: false };
    }
    if (phase.startsWith("audit")) {
      return { step: 3, done: false };
    }
    if (phase.startsWith("report")) {
      return { step: 4, done: false };
    }
    return { step: 1, done: false };
  };

  const legacyStep = getStepStatus();
  const runView = run ? mapInvestigationRunState(run) : null;
  const auditCompleted = authoritative && run?.status === "AUDIT_COMPLETED";
  const done = authoritative ? runView?.activity === "completed" : legacyStep.done;
  const activeStep = authoritative ? runView?.step || 1 : legacyStep.step;
  const terminalError = runView?.activity === "stopped";
  const queued = runView?.activity === "queued";
  const visibleRecords = analysisRecords.slice(0, 3);
  const displayedAnalyzedCount = isEthnicRelationsDemo && (phase.startsWith("report") || done)
    ? ETHNIC_RELATIONS_TOTAL
    : analysisRecords.length;
  const runStats = run ? buildRunProgressItems(run) : [];
  const totalStat = runStats.find(item => item.label === "进入研判");
  const visibleStats = ["待分析", "分析中", "已完成", "审核失败"].flatMap(label => runStats.filter(item => item.label === label));
  const outcome = run ? runOutcomePresentation(run) : null;
  const isHistoricalRun = run?.status === "AUDIT_COMPLETED" || run?.status === "PUBLISHED";
  const availableActions = run?.available_actions || {};
  const ending = availableActions.ending || (endSubmitted && !availableActions.ended);
  const hasRuntimeControls = !ending && !availableActions.ended && Boolean(
    run?.job_id
    && !isHistoricalRun
  );

  const submitControl = async (action: string) => {
    if (!run?.job_id || controlAction) return;
    setControlAction(action);
    setControlError("");
    try {
      await controlJob(run.job_id, action);
      onControlAccepted?.();
    } catch (error) {
      setControlError(controlErrorMessage(error));
    } finally {
      setControlAction("");
    }
  };

  const submitEnd = async () => {
    if (!run || controlAction) return;
    setConfirmEnd(false);
    setControlAction("end_task");
    setControlError("");
    try {
      const result = await endTask(run.run_id);
      setEndSubmitted(result.state === "RESERVED" && result.decision === "CANCELLED");
      onControlAccepted?.();
    } catch (error) {
      setControlError(controlErrorMessage(error));
    } finally {
      setControlAction("");
    }
  };

  const handleOpenAllRecords = () => {
    if (authoritative && run) {
      const query = new URLSearchParams({ run: run.run_id });
      if (run.report_version_id) query.set("report", run.report_version_id);
      navigate(`/investigation/${encodeURIComponent(investigationId)}/analysis-records?${query}`, {
        state: { records: analysisRecords, investigationTitle }
      });
      return;
    }
    storeAnalysisRecords(investigationId, analysisRecords);
    navigate(`/investigation/${encodeURIComponent(investigationId)}/analysis-records`, {
      state: { records: analysisRecords, investigationTitle }
    });
  };

  const handleViewCompleteEvidence = (record: AnalysisRecord) => {
    setSelectedRecord(null);
    if (record.source === "m3-report" && (!record.taskId || !record.outputId)) {
      const path = reportPostDetailPath(investigationId, record);
      if (path) navigate(path);
      return;
    }
    if (!record.taskId || !record.outputId) return;
    navigate(`/tasks/${encodeURIComponent(record.taskId)}/outputs/${encodeURIComponent(record.outputId)}`, {
      state: {
        returnTo: `/investigation/${encodeURIComponent(investigationId)}`,
        returnLabel: "调查报告",
        returnTitle: investigationTitle
      }
    });
  };

  return (
    <div className="agent-exec-clean-card" aria-label="调查流水线执行进度">
      <div className="agent-exec-clean-head">
        <span className="agent-exec-title">
          {ending ? "正在结束任务" : auditCompleted
            ? runView?.label
            : done
              ? "调查流水线已完成"
            : terminalError
              ? runView?.label
              : queued
                ? runView?.label
                : "调查流水线运行中"}
        </span>
        {outcome ? (
          <span className={`agent-exec-status-tag is-${outcome.tone}`}>
            {outcome.tone === "done" ? <CheckCircle2 size={13} /> : outcome.tone === "error" || outcome.tone === "warning" ? <AlertTriangle size={13} /> : null}
            {outcome.label}
          </span>
        ) : auditCompleted ? (
          <span className="agent-exec-status-tag is-done">
            <CheckCircle2 size={13} />
            {runView?.label}
          </span>
        ) : done ? (
          <span className="agent-exec-status-tag is-done">
            <CheckCircle2 size={13} />
            研判完成
          </span>
        ) : terminalError ? (
          <span className="agent-exec-status-tag is-error">
            <AlertTriangle size={13} />
            {runView?.terminal === "ended" ? "已结束" : runView?.terminal === "failed" ? "失败" : runView?.terminal === "paused" ? "已暂停" : "已中断"}
          </span>
        ) : queued ? (
          <span className="agent-exec-status-tag is-queued">等待中</span>
        ) : (
          <span className="agent-exec-status-tag is-running">
            <Loader2 size={13} className="spin" />
            {ending ? "正在结束" : "进行中"}
          </span>
        )}
      </div>

      {run && runView ? (
        <div className={`investigation-run-projection tone-${outcome?.tone || "running"}`}>
          <div className="investigation-run-summary">
            <strong>{runView.label}</strong>
            <span>
              采集 {statusLabel(run.crawl_status)} · 分析 {statusLabel(run.analysis_status)} · 报告 {reportStatusLabel(run.report_status)}
            </span>
            {totalStat ? <span className="investigation-run-total">纳入分析 {totalStat.value} 条</span> : null}
          </div>
          {visibleStats.length ? (
            <dl className="investigation-run-metrics" aria-label="本次调查进度统计">
              {visibleStats.map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {run.error_message ? <p role="alert">{formatRunFailureMessage(run)}</p> : null}
          {hasRuntimeControls || availableActions.end_task || ending ? (
            <div className="investigation-task-actions">
              {hasRuntimeControls ? (
                <div className="investigation-run-controls" aria-label="流水线实时控制">
                  {(["crawl", "analysis"] as const).map(stage => {
                    const resume = Boolean(availableActions[`resume_${stage}`]);
                    const action = `${resume ? "resume" : "pause"}_${stage}`;
                    const enabled = resume || availableActions[`pause_${stage}`];
                    if (!enabled && !["pausing", "stopping"].includes(stage === "crawl" ? run.crawl_status : run.analysis_status)) return null;
                    const label = `${resume ? "继续" : "暂停"}${stage === "crawl" ? "采集" : "分析"}`;
                    return <button key={stage} type="button" disabled={!enabled || Boolean(controlAction)} onClick={() => void submitControl(action)}>
                      {controlAction === action ? <Loader2 size={13} className="spin" /> : resume ? <Play size={13} /> : <Pause size={13} />}
                      {enabled ? label : `正在${label}`}
                    </button>;
                  })}
                </div>
              ) : null}
              {(availableActions.end_task && !availableActions.ended) || ending ? (
                <div className="investigation-run-controls investigation-end-control" aria-label="整个任务控制">
                  <button type="button" disabled={Boolean(ending || controlAction)} onClick={() => setConfirmEnd(true)}>
                    {ending ? "正在结束…" : "结束任务"}
                  </button>
                </div>
              ) : null}
              {ending ? <p role="status">正在结束，后台停止后可新建任务或删除会话。</p>
                : <small className="investigation-control-hint">暂停、继续不重复扣次。</small>}
            </div>
          ) : null}
          {controlError ? <p className="investigation-control-error" role="alert">{controlError}</p> : null}
        </div>
      ) : null}

      {run ? (
        <section className="investigation-runtime-logs" aria-label="流水线运行日志">
          <button type="button" className="investigation-runtime-logs-trigger" aria-expanded={logsExpanded} onClick={() => setLogsExpanded((value) => !value)}>
            <span><ScrollText size={15} />流水线运行日志 <small>{run.logs?.length || 0} 条</small></span>
            <ChevronDown size={15} className={logsExpanded ? "is-expanded" : ""} />
          </button>
          {logsExpanded ? (
            <div className="investigation-runtime-log-list">
              {run.logs?.length ? run.logs.slice(-100).map((entry, index) => (
                <div className={`investigation-runtime-log is-${entry.level || "info"}`} key={`${entry.time || "log"}-${index}`}>
                  <time>{formatLogTime(entry.time)}</time>
                  <span>{logStageLabel(entry.stage)}</span>
                  <em>{logLevelLabel(entry.level)}</em>
                  <p>{entry.message || "无日志内容"}</p>
                </div>
              )) : <div className="investigation-runtime-log-empty" role="status">暂无运行日志，任务开始后将在此显示。</div>}
            </div>
          ) : null}
        </section>
      ) : null}

      <EvidenceRelayPipeline
        isDone={Boolean(done && !auditCompleted && outcome?.tone !== "error")}
        auditCompleted={Boolean(auditCompleted && outcome?.tone !== "error")}
        isStopped={terminalError || outcome?.tone === "error"}
        authoritative={authoritative}
        activeStep={activeStep}
        isQueued={queued}
      />

      {authoritative || analysisRecords.length > 0 ? (
      <section className="analysis-feed" aria-label="最新分析进展">
        <div className="analysis-feed-head">
          <div className="analysis-feed-heading">
            <strong>最新分析进展</strong>
            {authoritative && authoritativeCounts.totalCount > 0 ? (
              <span className={`analysis-feed-count${authoritativeCounts.completedCount === authoritativeCounts.totalCount ? " is-complete" : ""}`}>
                已完成 {authoritativeCounts.completedCount} / {authoritativeCounts.totalCount}
              </span>
            ) : isEthnicRelationsDemo ? (
              <span className={`analysis-feed-count${displayedAnalyzedCount === ETHNIC_RELATIONS_TOTAL ? " is-complete" : ""}`}>
                已完成 {displayedAnalyzedCount} / {ETHNIC_RELATIONS_TOTAL}
              </span>
            ) : null}
          </div>
          <button
            type="button"
            className="analysis-history-trigger"
            onClick={handleOpenAllRecords}
            disabled={authoritative && recordsLoading}
          >
            <ListFilter size={14} />
            查看全部分析记录
          </button>
        </div>

        <div
          className={`analysis-feed-window is-count-${Math.min(visibleRecords.length, 3)}`}
          aria-live="polite"
        >
          {recordsLoading ? (
            <div className="analysis-feed-empty" role="status"><Loader2 size={16} className="spin" />正在加载分析结果</div>
          ) : recordsError ? (
            <div className="analysis-feed-empty is-error" role="alert">研判依据暂不可用，请稍后重试。</div>
          ) : visibleRecords.length === 0 ? (
            <div className="analysis-feed-empty" role="status">{availableActions.ended || done || terminalError ? "本次任务暂无可显示的分析结果" : "正在等待分析结果"}</div>
          ) : visibleRecords.map((record, index) => {
            const motionClass = index === 0 ? "is-latest" : "is-shifted";

            return (
              <article
                key={`${feedRevision}-${record.itemNumber}`}
                className={`analysis-feed-item ${motionClass}`}
              >
                <div className="analysis-feed-row">
                  <span className={`analysis-feed-marker ${index === 0 ? "is-current" : ""}`} />
                  <div className="analysis-feed-copy">
                    <div className="analysis-feed-primary">
                      <strong>第 {record.itemNumber} 条分析完成</strong>
                      <span className={`analysis-risk-tag is-${record.risk}`}>
                        {record.decisionLabel ? `${record.decisionLabel} · ` : ""}{record.riskLabel}
                      </span>
                    </div>
                    <span className="analysis-feed-summary">{record.summary}</span>
                  </div>
                  <button
                    type="button"
                    className="analysis-evidence-toggle"
                    onClick={() => record.taskId && record.outputId
                      ? handleViewCompleteEvidence(record)
                      : setSelectedRecord(record)}
                    disabled={authoritative && !record.outputId && !(record.reportVersionId && record.postRef) && record.keyEvidence.length === 0}
                  >
                    <FileSearch size={14} />
                    {authoritative && !record.outputId && !(record.reportVersionId && record.postRef) && record.keyEvidence.length === 0
                      ? "审核详情暂不可用"
                      : "查看研判依据"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>
      ) : null}

      <ConfirmDialog open={confirmEnd} title="结束整个任务？"
        description="结束后无法继续此任务，已扣次数不返还，已采集的数据和结果保留。后台停止后可新建任务或删除会话。"
        confirmText="确认结束任务" onConfirm={() => void submitEnd()} onCancel={() => setConfirmEnd(false)} />
      <AnalysisBasisDrawer
        record={selectedRecord}
        onClose={() => setSelectedRecord(null)}
        onViewCompleteEvidence={handleViewCompleteEvidence}
      />
    </div>
  );
}

function statusLabel(status: string) {
  return ({ completed: "完成", partial: "处理完成，部分帖子失败", pending: "待处理", queued: "排队中", running: "进行中", pausing: "正在暂停", stopping: "正在停止", interrupted: "已中断", idle: "未启动", failed: "失败", stopped: "已停止", paused: "已暂停", skipped: "跳过" } as Record<string, string>)[status] || "处理中";
}

function reportStatusLabel(status: string) {
  return ({ cancelled: "已结束，未生成报告", blocked_by_failed_posts: "本轮处理结束，无可发布报告", pending: "待生成", generating: "生成中", published: "已生成", failed: "失败", interrupted: "已中断" } as Record<string, string>)[status] || "待处理";
}

function controlErrorMessage(error: unknown) {
  if (!(error instanceof Error)) return "操作失败，请稍后重试。";
  try {
    const payload = JSON.parse(error.message) as { detail?: string };
    return payload.detail || error.message;
  } catch {
    return error.message || "操作失败，请稍后重试。";
  }
}

function formatLogTime(value?: string) {
  if (!value) return "--:--:--";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString("zh-CN", { hour12: false });
}

function logStageLabel(stage?: string) {
  return ({
    configuration: "参数解析",
    account: "账号选择",
    crawl: "内容采集",
    ingestion: "内容入库",
    analysis: "内容分析",
    media: "评论/媒体",
    control: "暂停/恢复",
    recovery: "恢复处理",
    pipeline: "流水线"
  } as Record<string, string>)[stage || ""] || stage || "流水线";
}

function logLevelLabel(level?: string) {
  return ({ info: "信息", warning: "警告", error: "错误" } as Record<string, string>)[level || ""] || "信息";
}
