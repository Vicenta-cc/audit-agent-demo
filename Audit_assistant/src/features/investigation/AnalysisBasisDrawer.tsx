import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, FileSearch, X } from "lucide-react";
import { fetchAuditResultDetail } from "../../services/jobs";
import { fetchReportPostDetail } from "../../services/reports";
import type { AuditEvidenceGroupItem } from "../../types/jobs";
import type { AnalysisEvidenceType, AnalysisKeyEvidence, AnalysisRecord } from "./analysisRecords";
import { analysisEvidenceTypes } from "./analysisRecords";
import { mapReportEvidence } from "./m3AnalysisRecords";

interface AnalysisBasisDrawerProps {
  record: AnalysisRecord | null;
  onClose: () => void;
  onViewCompleteEvidence: (record: AnalysisRecord) => void;
}

interface DrawerEvidence extends AnalysisKeyEvidence {
  position?: string;
}

const emptyEvidenceCounts = (): Record<AnalysisEvidenceType, number> => ({
  text: 0,
  ocr: 0,
  asr: 0,
  comment: 0,
  vision: 0
});

export function AnalysisBasisDrawer({
  record,
  onClose,
  onViewCompleteEvidence
}: AnalysisBasisDrawerProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [activeEvidenceType, setActiveEvidenceType] = useState<AnalysisEvidenceType>("text");
  const [completeEvidence, setCompleteEvidence] = useState<DrawerEvidence[] | null>(null);
  const [isEvidenceLoading, setIsEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState("");
  const [loadRevision, setLoadRevision] = useState(0);

  useEffect(() => {
    if (!record) return;
    let isCurrent = true;
    setCompleteEvidence(null);
    setEvidenceError("");
    setIsEvidenceLoading(true);
    const request = record.source === "m3-job"
      ? Promise.resolve(record.keyEvidence)
      : record.source === "m3-report"
      ? record.reportVersionId && record.postRef
        ? fetchReportPostDetail(record.reportVersionId, record.postRef).then((detail) => (
            detail.direct_evidence.map(mapReportEvidence).filter(Boolean) as DrawerEvidence[]
          ))
        : Promise.reject(new Error("当前记录缺少真实 ReportVersion 或帖子引用"))
      : record.outputId
        ? fetchAuditResultDetail(record.outputId).then((detail) => mapEvidenceGroups(detail.evidence_groups))
        : Promise.reject(new Error("当前记录缺少真实任务输出引用"));
    void request
      .then((detail) => {
        if (!isCurrent) return;
        setCompleteEvidence(detail);
        setIsEvidenceLoading(false);
      })
      .catch((error) => {
        if (!isCurrent) return;
        setCompleteEvidence(null);
        setEvidenceError(error instanceof Error ? error.message : "证据加载失败");
        setIsEvidenceLoading(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [loadRevision, record]);

  useEffect(() => {
    if (!record) return;
    const previousActiveElement = document.activeElement as HTMLElement | null;
    const timeline = document.querySelector<HTMLElement>(".inv-timeline-container");
    const previousTimelineOverflow = timeline?.style.overflowY || "";
    const initialTimelineScrollTop = timeline?.scrollTop || 0;

    if (timeline) timeline.style.overflowY = "hidden";
    closeButtonRef.current?.focus();

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);

    return () => {
      window.removeEventListener("keydown", handleEscape);
      if (timeline) timeline.style.overflowY = previousTimelineOverflow;
      previousActiveElement?.focus({ preventScroll: true });
      if (timeline) timeline.scrollTop = initialTimelineScrollTop;
    };
  }, [onClose, record]);

  const evidence = useMemo<DrawerEvidence[]>(
    () => completeEvidence ?? (isEvidenceLoading || evidenceError || record?.source === "m3-report"
      ? []
      : record?.keyEvidence ?? []),
    [completeEvidence, evidenceError, isEvidenceLoading, record]
  );
  const evidenceCounts = useMemo(() => {
    if (isEvidenceLoading && record) return record.evidenceCounts;
    return evidence.reduce((counts, item) => {
      counts[item.type] += 1;
      return counts;
    }, emptyEvidenceCounts());
  }, [evidence, isEvidenceLoading, record]);
  const visibleEvidence = useMemo(
    () => evidence.filter((item) => item.type === activeEvidenceType),
    [activeEvidenceType, evidence]
  );
  const activeEvidenceLabel = analysisEvidenceTypes.find(({ type }) => type === activeEvidenceType)?.label || "";

  useEffect(() => {
    if (evidenceCounts[activeEvidenceType] > 0) return;
    const firstAvailable = analysisEvidenceTypes.find(({ type }) => evidenceCounts[type] > 0)?.type;
    setActiveEvidenceType(firstAvailable || "text");
  }, [activeEvidenceType, evidenceCounts]);

  if (!record) return null;

  return createPortal(
    <div className="analysis-basis-layer">
      <button
        type="button"
        className="analysis-basis-backdrop"
        aria-label="关闭研判依据"
        onClick={onClose}
      />
      <aside
        className="analysis-basis-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="analysis-basis-title"
      >
        <header className="analysis-basis-header">
          <div className="analysis-basis-heading">
            <span>第 {record.itemNumber} 条分析结果</span>
            <h2 id="analysis-basis-title">研判依据</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="analysis-basis-close"
            aria-label="关闭"
            title="关闭"
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>

        <div className="analysis-basis-subject">
          <strong title={record.contentTitle}>{record.contentTitle}</strong>
          <div>
            <span>{record.author}</span>
            <i aria-hidden="true" />
            <span>{record.platform}</span>
          </div>
        </div>

        <div className="analysis-basis-body">
          <section className="analysis-basis-section analysis-basis-verdict">
            <div className="analysis-basis-section-label">Agent 研判结论</div>
            <p>{record.conclusion}</p>
            <span className={`analysis-basis-risk is-${record.risk}`}>{record.riskLabel}</span>
          </section>

          <section className="analysis-basis-section">
            <div className="analysis-basis-section-label">证据概览</div>
            <div className="analysis-evidence-tabs" aria-label="五类证据数量概览">
              {analysisEvidenceTypes.map(({ type, label }) => {
                const count = evidenceCounts[type];
                const isActive = activeEvidenceType === type && count > 0;
                return (
                  <button
                    key={type}
                    type="button"
                    className={`${count === 0 ? "is-empty" : "has-evidence"} ${isActive ? "is-active" : ""}`}
                    disabled={count === 0}
                    aria-label={`${label} ${count}`}
                    aria-pressed={isActive}
                    onClick={() => setActiveEvidenceType(type)}
                  >
                    <span>{label}</span>
                    <strong>{count}</strong>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="analysis-basis-section analysis-key-section">
            <div className="analysis-key-heading">
              <div className="analysis-basis-section-label">{activeEvidenceLabel}证据</div>
              <span>{evidenceCounts[activeEvidenceType]} 条</span>
            </div>
            <div className="analysis-key-list">
              {isEvidenceLoading ? (
                <div className="analysis-evidence-loading" role="status" aria-label="正在加载完整证据">
                  <span />
                  <span />
                  <span />
                </div>
              ) : evidenceError ? (
                <div className="analysis-evidence-error" role="alert">
                  <strong>研判依据暂不可用</strong>
                  <span>{evidenceError}</span>
                  <div>
                    <button type="button" onClick={() => setLoadRevision((current) => current + 1)}>重试</button>
                    <button type="button" onClick={onClose}>关闭</button>
                  </div>
                </div>
              ) : visibleEvidence.map((evidence, index) => {
                const typeLabel = analysisEvidenceTypes.find(({ type }) => type === evidence.type)?.label;
                return (
                  <article
                    key={`${evidence.type}:${evidence.id}:${index}`}
                    className={`analysis-evidence-card is-${record.risk}`}
                  >
                    <header>
                      <span className="analysis-evidence-index">
                        <b>{String(index + 1).padStart(2, "0")}</b>
                        {typeLabel}证据
                      </span>
                      {evidence.position ? <small>{evidence.position}</small> : null}
                    </header>
                    <div className="analysis-evidence-field is-original">
                      <span>{originalFieldLabel(evidence.type)}</span>
                      <p className="analysis-evidence-original" dir="auto">{evidence.content}</p>
                    </div>
                    {evidence.translation ? (
                      <div className="analysis-evidence-field is-translation">
                        <span>中文译文</span>
                        <p dir="auto">{evidence.translation}</p>
                      </div>
                    ) : null}
                    <div className="analysis-evidence-field is-explanation">
                      <span>命中解释</span>
                      <p>{evidence.explanation}</p>
                    </div>
                  </article>
                );
              })}
              {!isEvidenceLoading && !evidenceError && visibleEvidence.length === 0 ? (
                <div className="analysis-key-empty">该类型暂无风险证据</div>
              ) : null}
            </div>
          </section>

          {record.source !== "m3-report" ? <section className="analysis-basis-section analysis-overall-judgment">
            <div className="analysis-basis-section-label">综合判断</div>
            <p>
              当前结论由多模态证据交叉形成，仍属于 Agent 自动研判结果；进入人工复核后再确定最终处理结论。
            </p>
          </section> : null}
        </div>

        <footer className="analysis-basis-footer">
          <button
            type="button"
            disabled={
              record.source === "m3-job"
                ? record.keyEvidence.length === 0
                : record.source === "m3-report" && (!record.reportVersionId || !record.findingRef)
            }
            onClick={() => onViewCompleteEvidence(record)}
          >
            <FileSearch size={17} />
            <span>{(record.source === "m3-job" && record.keyEvidence.length === 0)
              || (record.source === "m3-report" && (!record.reportVersionId || !record.findingRef))
              ? "研判依据暂不可用"
              : "查看完整证据"}</span>
            <ArrowRight size={16} />
          </button>
        </footer>
      </aside>
    </div>,
    document.body
  );
}

function mapEvidenceGroups(groups: Awaited<ReturnType<typeof fetchAuditResultDetail>>["evidence_groups"]): DrawerEvidence[] {
  return (groups || []).flatMap((group) => {
    const type = normalizeEvidenceType(group.type || group.id || "");
    if (!type) return [];
    return (group.items || []).map((item, index) => {
      const content = evidenceContent(type, item);
      const translation = firstItemText(item, ["translation_zh", "text_zh", "ocr_text_zh"]);
      return {
        id: String(item.id || `${type}-${index + 1}`),
        type,
        content: content || "--",
        translation: translation && translation !== content ? translation : undefined,
        explanation: firstItemText(item, ["hit_explanation", "reason", "risk_basis", "rule", "context"]) || "暂无命中解释",
        position: firstItemText(item, ["nickname", "position", "source_label"])
      };
    });
  });
}

function normalizeEvidenceType(value: string): AnalysisEvidenceType | null {
  const normalized = value.toLowerCase();
  if (normalized.includes("ocr") || normalized.includes("image")) return "ocr";
  if (normalized.includes("asr") || normalized.includes("audio")) return "asr";
  if (normalized.includes("comment")) return "comment";
  if (normalized.includes("vision") || normalized.includes("frame") || normalized.includes("video")) return "vision";
  if (normalized.includes("text") || normalized.includes("title") || normalized.includes("desc")) return "text";
  return null;
}

function evidenceContent(type: AnalysisEvidenceType, item: AuditEvidenceGroupItem) {
  if (type === "comment") return firstItemText(item, ["text", "content"]);
  if (type === "ocr") return firstItemText(item, ["ocr_text", "text", "content"]);
  if (type === "asr") return firstItemText(item, ["source_text_dolphin", "text", "content"]);
  if (type === "vision") return firstItemText(item, ["visual_summary", "content", "text", "context"]);
  return firstItemText(item, ["text", "content"]);
}

function firstItemText(item: AuditEvidenceGroupItem, keys: string[]) {
  for (const key of keys) {
    const value = item[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function originalFieldLabel(type: AnalysisEvidenceType) {
  if (type === "comment") return "评论原文";
  if (type === "ocr") return "OCR 原文";
  if (type === "asr") return "音频原文";
  if (type === "vision") return "画面描述";
  return "原文";
}
