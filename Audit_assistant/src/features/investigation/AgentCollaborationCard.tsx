import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FileSearch, ListFilter, Loader2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import type { AgentExecutionPhase } from "../../types/investigation";
import { AnalysisBasisDrawer } from "./AnalysisBasisDrawer";
import {
  createAnalysisRecord,
  createCompletedAnalysisRecords,
  readStoredAnalysisRecords,
  storeAnalysisRecords,
  type AnalysisScenario,
  type AnalysisRecord
} from "./analysisRecords";

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
}

export function EvidenceRelayPipeline({ isDone }: { isDone: boolean }) {
  const relayStartDelays = [0, 1.9, 3.8];

  return (
    <div className="evidence-pipeline" aria-label="证据接力流水线">
      <svg
        viewBox="0 0 640 112"
        className={`evidence-pipeline-svg ${isDone ? "is-done" : "is-running"}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <text x="76" y="19" className="pipeline-node-label">数据采集</text>
        <text x="236" y="19" className="pipeline-node-label">证据分析 Agent</text>
        <text x="404" y="19" className="pipeline-node-label">风险研判 Agent</text>
        <text x="564" y="19" className="pipeline-node-label">报告归纳 Agent</text>

        <line x1="100" y1="66" x2="212" y2="66" className="pipeline-track" />
        <line x1="260" y1="66" x2="380" y2="66" className="pipeline-track" />
        <line x1="428" y1="66" x2="540" y2="66" className="pipeline-track" />
        {!isDone ? (
          <g aria-hidden="true">
            <line x1="100" y1="66" x2="212" y2="66" className="pipeline-track-flow pipeline-track-flow--1" />
            <line x1="260" y1="66" x2="380" y2="66" className="pipeline-track-flow pipeline-track-flow--2" />
            <line x1="428" y1="66" x2="540" y2="66" className="pipeline-track-flow pipeline-track-flow--3" />
          </g>
        ) : null}

        <g className="pipeline-node pipeline-node--source">
          <circle cx="76" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(76, 66)">
            <circle cx="0" cy="0" r="14" className="pipeline-radar-ring" />
            <circle cx="0" cy="0" r="8" className="pipeline-radar-ring pipeline-radar-ring--inner" />
            <path d="M 0 0 L 14 0 A 14 14 0 0 0 0 -14 Z" className="pipeline-radar-sweep" />
            <circle cx="0" cy="0" r="2.5" className="pipeline-radar-dot" />
          </g>
        </g>

        <g className={`pipeline-node pipeline-node--analysis ${isDone ? "is-done" : ""}`}>
          <circle cx="236" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(236, 66)" className="pipeline-analysis-glyph">
            <path d="M 0 -9 L 8 0 L 0 9 L -8 0 Z" />
            <circle cx="0" cy="0" r="2.5" />
          </g>
        </g>

        <g className={`pipeline-node pipeline-node--risk ${isDone ? "is-done" : ""}`}>
          <circle cx="404" cy="66" r="22" className="pipeline-node-bg" />
          <g transform="translate(404, 66)" className="pipeline-risk-glyph">
            <path d="M -9 1 A 9 9 0 0 1 9 1" />
            <path d="M -6 6 A 8 8 0 0 0 6 6" />
            <line x1="0" y1="-8" x2="0" y2="8" />
            <circle cx="0" cy="1" r="2.5" />
          </g>
        </g>

        <g className={`pipeline-node pipeline-node--report ${isDone ? "is-done" : ""}`}>
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

        {!isDone ? (
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
  investigationTitle
}: AgentCollaborationCardProps) {
  const navigate = useNavigate();
  const scenario: AnalysisScenario = investigationTitle.includes("维汉民族关系")
    ? "ethnic-relations"
    : "default";
  const isEthnicRelationsDemo = scenario === "ethnic-relations";
  const timerRef = useRef<number | null>(null);
  const phaseRef = useRef(phase);
  const [analysisRecords, setAnalysisRecords] = useState<AnalysisRecord[]>(() => (
    readStoredAnalysisRecords(investigationId)
      || (phase === "completed"
        ? createCompletedAnalysisRecords(isEthnicRelationsDemo ? ETHNIC_RELATIONS_DEMO_RECORDS : 4, scenario)
        : [])
  ));
  const nextRecordNumberRef = useRef(
    Math.max(0, ...analysisRecords.map((record) => record.itemNumber)) + 1
  );
  const [feedRevision, setFeedRevision] = useState(0);
  const [selectedRecord, setSelectedRecord] = useState<AnalysisRecord | null>(null);

  phaseRef.current = phase;

  useEffect(() => {
    storeAnalysisRecords(investigationId, analysisRecords);
  }, [analysisRecords, investigationId]);

  useEffect(() => {
    if (phase !== "collection_waking") return;
    setAnalysisRecords([]);
    nextRecordNumberRef.current = 1;
    setFeedRevision((current) => current + 1);
  }, [phase]);

  // Auto progression through phases
  useEffect(() => {
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
  }, [isEthnicRelationsDemo, phase, onPhaseChange, onCompleted]);

  useEffect(() => {
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
  }, [isEthnicRelationsDemo, phase, scenario]);

  // Determine active step (1-4)
  const getStepStatus = () => {
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

  const { done } = getStepStatus();
  const visibleRecords = analysisRecords.slice(0, 3);
  const displayedAnalyzedCount = isEthnicRelationsDemo && (phase.startsWith("report") || done)
    ? ETHNIC_RELATIONS_TOTAL
    : analysisRecords.length;

  const handleOpenAllRecords = () => {
    storeAnalysisRecords(investigationId, analysisRecords);
    navigate(`/investigation/${encodeURIComponent(investigationId)}/analysis-records`, {
      state: { records: analysisRecords, investigationTitle }
    });
  };

  const handleViewCompleteEvidence = (record: AnalysisRecord) => {
    setSelectedRecord(null);
    navigate(`/tasks/${encodeURIComponent(record.taskId)}/outputs/${encodeURIComponent(record.outputId)}`, {
      state: {
        returnTo: `/investigation/${encodeURIComponent(investigationId)}`,
        returnLabel: "调查报告",
        returnTitle: investigationTitle
      }
    });
  };

  return (
    <div className="agent-exec-clean-card" aria-label="Agent 执行进度">
      <div className="agent-exec-clean-head">
        <span className="agent-exec-title">
          {done ? "Agent 协同研判完成" : "Agent 协同研判中"}
        </span>
        {done ? (
          <span className="agent-exec-status-tag is-done">
            <CheckCircle2 size={13} />
            研判完成
          </span>
        ) : (
          <span className="agent-exec-status-tag is-running">
            <Loader2 size={13} className="spin" />
            进行中
          </span>
        )}
      </div>

      <EvidenceRelayPipeline isDone={done} />

      {analysisRecords.length > 0 ? (
      <section className="analysis-feed" aria-label="最新分析进展">
        <div className="analysis-feed-head">
          <div className="analysis-feed-heading">
            <strong>最新分析进展</strong>
            {isEthnicRelationsDemo ? (
              <span className={`analysis-feed-count${displayedAnalyzedCount === ETHNIC_RELATIONS_TOTAL ? " is-complete" : ""}`}>
                已完成 {displayedAnalyzedCount} / {ETHNIC_RELATIONS_TOTAL}
              </span>
            ) : null}
          </div>
          <button
            type="button"
            className="analysis-history-trigger"
            onClick={handleOpenAllRecords}
          >
            <ListFilter size={14} />
            查看全部分析记录
          </button>
        </div>

        <div
          className={`analysis-feed-window is-count-${Math.min(visibleRecords.length, 3)}`}
          aria-live="polite"
        >
          {visibleRecords.map((record, index) => {
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
                        {record.riskLabel}
                      </span>
                    </div>
                    <span className="analysis-feed-summary">{record.summary}</span>
                  </div>
                  <button
                    type="button"
                    className="analysis-evidence-toggle"
                    onClick={() => setSelectedRecord(record)}
                  >
                    <FileSearch size={14} />
                    查看研判依据
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>
      ) : null}

      <AnalysisBasisDrawer
        record={selectedRecord}
        onClose={() => setSelectedRecord(null)}
        onViewCompleteEvidence={handleViewCompleteEvidence}
      />
    </div>
  );
}
