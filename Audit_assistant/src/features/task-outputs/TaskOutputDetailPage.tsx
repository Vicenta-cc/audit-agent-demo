import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchReportAuditDetail } from "../../services/reports";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { getReturnNavigationState } from "../../app/listNavigation";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock3,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  Link2,
  MessageSquare,
  Mic,
  MoreHorizontal,
  Play,
  Search,
  ScanEye,
  ShieldAlert,
  ShieldCheck,
  UserRound,
  UserSearch,
  X
} from "lucide-react";
import { Button } from "../../components/common/Button";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import { IconButton } from "../../components/common/IconButton";
import { ErrorState } from "../../components/feedback/ErrorState";
import { LoadingState } from "../../components/feedback/LoadingState";
import { Toast } from "../../components/feedback/Toast";
import {
  fetchAuditResultDetail,
  fetchAuditResults,
  fetchJob,
  formatDateTime,
  getPlatformLabel,
  linkCommentUserRelation,
  mapJobsToMonitorTasks
} from "../../services/jobs";
import type {
  AuditComment,
  AuditEvidenceGroup,
  AuditEvidenceGroupItem,
  AuditResult,
  AuditResultDetail,
  MonitorTask,
  RawJob
} from "../../types/jobs";
import type { RiskLevel, TaskOutputItem } from "../../types/taskOutputs";
import { getOutputKey, isNoRiskOutput, mapAuditResultToTaskOutput } from "./taskOutputUtils";

type EvidenceType = "text" | "ocr" | "asr" | "comment" | "vision";

interface MediaItem {
  id: string;
  type: "image" | "video";
  label: string;
  url: string;
  caption: string;
}

interface DetailComment {
  id: string;
  name: string;
  avatarUrl: string;
  region: string;
  content: string;
  time: string;
  profileUrl: string;
  rawIdentity: string;
  translation: string;
  auditStatus: string;
  riskScore: number | null;
  riskLevel: string;
  raw: AuditComment;
}

interface DetailTranscript {
  source: string;
  translation: string;
}

const evidenceTabs: Array<{
  type: EvidenceType;
  label: string;
  shortLabel: string;
  Icon: typeof FileText;
}> = [
  { type: "text", label: "文本证据", shortLabel: "文本", Icon: FileText },
  { type: "ocr", label: "画面文字", shortLabel: "OCR", Icon: ImageIcon },
  { type: "asr", label: "音频证据", shortLabel: "ASR", Icon: Mic },
  { type: "comment", label: "评论证据", shortLabel: "评论", Icon: MessageSquare },
  { type: "vision", label: "视觉证据", shortLabel: "视觉", Icon: ScanEye }
];

const INITIAL_COMMENT_RENDER_COUNT = 20;

export function TaskOutputDetailPage() {
  const { taskId = "", outputId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const returnNavigation = getReturnNavigationState(location.state);
  const reportVersion = new URLSearchParams(location.search).get("report_version") || "";
  const postRef = new URLSearchParams(location.search).get("post_ref") || "";
  const commentsRef = useRef<HTMLDivElement>(null);
  const evidenceListRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [detail, setDetail] = useState<AuditResultDetail | null>(null);
  const [task, setTask] = useState<MonitorTask | null>(null);
  const [job, setJob] = useState<RawJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mediaIndex, setMediaIndex] = useState(0);
  const [activeEvidenceType, setActiveEvidenceType] = useState<EvidenceType>("text");
  const [contentDialogOpen, setContentDialogOpen] = useState(false);
  const [commentsDrawerOpen, setCommentsDrawerOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadData = useCallback(async () => {
    if (!outputId) {
      setError("缺少内容结果 ID");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError("");
    try {
      if (Boolean(reportVersion) !== Boolean(postRef)) throw new Error("报告审核记录引用不完整");
      const routeJobPromise = taskId ? fetchJob(taskId).catch(() => null) : Promise.resolve(null);
      const [nextDetail, routeJob] = await Promise.all([reportVersion && postRef ? fetchReportAuditDetail(reportVersion, postRef) : loadOutputDetail(taskId, outputId), routeJobPromise]);
      const resultJobId = String(nextDetail.audit_result.job_id || taskId || "");
      if (reportVersion && (resultJobId !== taskId || String(nextDetail.audit_result.audit_result_id) !== outputId)) {
        throw new Error("报告记录与当前审核页面不匹配");
      }
      const matchedJob = routeJob?.id === resultJobId
        ? routeJob
        : resultJobId
          ? await fetchJob(resultJobId).catch(() => null)
          : null;
      setDetail(nextDetail);
      setJob(matchedJob);
      setTask(matchedJob ? mapJobsToMonitorTasks([matchedJob], [nextDetail.audit_result])[0] || null : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载风险内容详情失败");
    } finally {
      setLoading(false);
    }
  }, [outputId, taskId, reportVersion, postRef]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const item = useMemo(() => (detail ? mapAuditResultToTaskOutput(detail.audit_result) : null), [detail]);
  const groups = useMemo(() => normalizeEvidenceGroups(detail), [detail]);
  const groupsByType = useMemo(() => new Map(groups.map((group) => [group.type as EvidenceType, group])), [groups]);
  const mediaItems = useMemo(() => (detail && item ? collectOriginalMedia(detail.audit_result, item) : []), [detail, item]);
  const originalText = useMemo(() => (detail ? buildOriginalText(detail.audit_result) : ""), [detail]);
  const translatedText = useMemo(() => (detail ? buildTranslatedText(detail.audit_result) : ""), [detail]);
  const transcript = useMemo(() => (detail ? buildTranscript(detail.audit_result) : { source: "", translation: "" }), [detail]);
  const comments = useMemo(() => (detail ? normalizeComments(detail.audit_result.comments || []) : []), [detail]);
  const visibleComments = comments.slice(0, INITIAL_COMMENT_RENDER_COUNT);
  const currentMedia = mediaItems.length ? mediaItems[Math.min(mediaIndex, mediaItems.length - 1)] : null;

  useEffect(() => {
    const firstAvailable = evidenceTabs.find((tab) => (groupsByType.get(tab.type)?.count || 0) > 0)?.type || "text";
    setActiveEvidenceType((current) => {
      const currentCount = groupsByType.get(current)?.count || 0;
      return currentCount > 0 ? current : firstAvailable;
    });
  }, [groupsByType]);

  useEffect(() => {
    setMediaIndex(0);
    setContentDialogOpen(false);
    setCommentsDrawerOpen(false);
  }, [outputId]);

  useEffect(() => {
    evidenceListRef.current?.scrollTo({ left: 0 });
  }, [activeEvidenceType]);

  useEffect(() => {
    if (!contentDialogOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContentDialogOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [contentDialogOpen]);

  useEffect(() => {
    if (pendingSeek === null || !videoRef.current || currentMedia?.type !== "video") return;
    const video = videoRef.current;
    const seek = () => {
      video.currentTime = pendingSeek;
      void video.play().catch(() => undefined);
      setPendingSeek(null);
    };
    if (video.readyState >= 1) seek();
    else video.addEventListener("loadedmetadata", seek, { once: true });
    return () => video.removeEventListener("loadedmetadata", seek);
  }, [currentMedia?.type, mediaIndex, pendingSeek]);

  if (loading && !detail) {
    return <main className="task-output-detail-page"><LoadingState label="正在加载风险内容询证详情..." /></main>;
  }

  if (error || !detail || !item) {
    return (
      <main className="task-output-detail-page">
        <ErrorState message={error || "未找到风险内容详情"} onRetry={() => void loadData()} />
      </main>
    );
  }

  const result = detail.audit_result;
  const activeGroup = groupsByType.get(activeEvidenceType) || emptyGroup(activeEvidenceType);
  const publishedAt = getPublishedAt(result);
  const batchLabel = firstText(stringField(result, "batch_id"), stringField(result, "batch"), result.job_id, "--");
  const commentTotal = Number(result.comments_count || result.comment_count || comments.length);
  const verdictSummary = firstText(item.summary, result.desc, result.title, "暂无内容摘要");
  const fallbackReturnTo = task ? `/tasks/${encodeURIComponent(task.id)}/outputs` : "/risk";
  const returnLabel = returnNavigation?.returnLabel || (task ? "监控任务" : "风险研判");
  const returnTitle = returnNavigation?.returnTitle || (task?.name || "风险研判工作台");

  const handleReturn = () => {
    if (returnNavigation) navigate(-1);
    else navigate(fallbackReturnTo);
  };

  const goMedia = (direction: -1 | 1) => {
    if (mediaItems.length < 2) return;
    setMediaIndex((current) => (current + direction + mediaItems.length) % mediaItems.length);
  };

  const copyOutputNumber = async () => {
    await navigator.clipboard?.writeText(item.number);
    setToast({ message: "已复制结果编号", tone: "success" });
    setMoreOpen(false);
  };

  const copySourceUrl = async () => {
    if (!item.sourceUrl) {
      setToast({ message: "当前内容没有来源链接", tone: "info" });
      setMoreOpen(false);
      return;
    }
    await navigator.clipboard?.writeText(item.sourceUrl);
    setToast({ message: "已复制来源链接", tone: "success" });
    setMoreOpen(false);
  };

  const handleLinkComment = async (comment: DetailComment) => {
    if (detail.report_snapshot) {
      setToast({ message: "历史报告中的账号可在报告问答中继续查询", tone: "info" });
      return;
    }
    try {
      await linkCommentUserRelation(buildCommentRelationContext(result, comment));
      setToast({ message: "已关联评论用户", tone: "success" });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : "关联失败", tone: "info" });
    }
  };

  const handleAnalyzeCommentUser = (comment: DetailComment) => {
    if (comment.profileUrl) {
      void navigator.clipboard?.writeText(comment.profileUrl);
      setToast({ message: "已复制评论用户主页，可在新建监控任务中使用", tone: "info" });
      return;
    }
    setToast({ message: "该评论缺少可分析的主页链接", tone: "info" });
  };

  const scrollComments = (direction: -1 | 1) => {
    const container = commentsRef.current;
    if (!container) return;
    container.scrollBy({ left: direction * Math.max(320, container.clientWidth * 0.78), behavior: "smooth" });
  };

  const scrollEvidence = (direction: -1 | 1) => {
    const container = evidenceListRef.current;
    if (!container) return;
    const items = Array.from(container.querySelectorAll<HTMLElement>(".detail-evidence-item"));
    if (!items.length) return;
    const paddingLeft = Number.parseFloat(window.getComputedStyle(container).paddingLeft) || 0;
    const containerLeft = container.getBoundingClientRect().left;
    const targets = items.map((item) => (
      item.getBoundingClientRect().left - containerLeft + container.scrollLeft - paddingLeft
    ));
    const currentIndex = targets.reduce((closest, target, index) => (
      Math.abs(target - container.scrollLeft) < Math.abs(targets[closest] - container.scrollLeft) ? index : closest
    ), 0);
    const nextIndex = Math.min(items.length - 1, Math.max(0, currentIndex + direction));
    container.scrollTo({ left: targets[nextIndex], behavior: "smooth" });
  };

  const playEvidenceAt = (seconds: number) => {
    const videoIndex = mediaItems.findIndex((media) => media.type === "video");
    if (videoIndex < 0) {
      setToast({ message: "当前内容没有可播放的视频", tone: "info" });
      return;
    }
    setMediaIndex(videoIndex);
    setPendingSeek(Math.max(0, seconds));
  };

  return (
    <main className="task-output-detail-page">
      <section className="detail-summary-shell" aria-label="内容摘要">
        <div className="detail-breadcrumb">
          <button type="button" onClick={handleReturn}>
            <ArrowLeft size={16} />
            <span>{returnLabel}</span>
          </button>
          <span>/</span>
          <button type="button" onClick={handleReturn}>{returnTitle}</button>
          <span>/ 内容详情</span>
        </div>

        <div className="detail-summary-main">
          <div className="detail-title-area">
            <h1 title={item.title}>{item.title}</h1>
            <div className="detail-compact-meta">
              <span className="detail-platform-tag">{getPlatformLabel(result.platform, job?.input_type)}</span>
              <span>作者：{item.author}</span>
              <span>发布时间：{publishedAt}</span>
            </div>
          </div>

          <div className="detail-top-actions">
            {item.sourceUrl ? (
              <a className="detail-source-link" href={item.sourceUrl} target="_blank" rel="noreferrer">
                查看来源
                <ExternalLink size={15} />
              </a>
            ) : null}
            <DropdownMenu
              open={moreOpen}
              onClose={() => setMoreOpen(false)}
              trigger={(
                <Button type="button" variant="secondary" onClick={() => setMoreOpen((current) => !current)}>
                  更多操作
                  <MoreHorizontal size={16} />
                </Button>
              )}
            >
              <button type="button" onClick={() => void copySourceUrl()}>复制来源链接</button>
              <button type="button" onClick={() => void copyOutputNumber()}>复制结果编号</button>
              <button type="button" onClick={handleReturn}>返回{returnTitle}</button>
            </DropdownMenu>
          </div>
        </div>
      </section>

      <section className="detail-main-grid" aria-label="原始内容与研判证据">
        <article className="detail-card original-card">
          <header className="detail-card-header">
            <h2>原始内容</h2>
            {mediaItems.length > 1 ? <span>{mediaIndex + 1} / {mediaItems.length}</span> : null}
          </header>

          <div className="detail-media-stage">
            {currentMedia ? (
              currentMedia.type === "video" ? (
                <video ref={videoRef} controls preload="metadata" playsInline src={currentMedia.url} />
              ) : (
                <img src={currentMedia.url} alt={currentMedia.label} />
              )
            ) : (
              <div className="detail-media-empty">
                <ImageIcon size={38} />
                <span>暂无可展示的原始素材</span>
              </div>
            )}

            {mediaItems.length > 1 ? (
              <>
                <IconButton className="detail-media-arrow detail-media-prev" type="button" aria-label="上一张" onClick={() => goMedia(-1)}>
                  <ChevronLeft size={20} />
                </IconButton>
                <IconButton className="detail-media-arrow detail-media-next" type="button" aria-label="下一张" onClick={() => goMedia(1)}>
                  <ChevronRight size={20} />
                </IconButton>
                <div className="detail-media-pagination">
                  <span>{mediaIndex + 1} / {mediaItems.length}</span>
                  <div>
                    {mediaItems.map((media, index) => (
                      <button
                        key={media.id}
                        className={index === mediaIndex ? "is-active" : ""}
                        type="button"
                        aria-label={`切换到${media.label}`}
                        onClick={() => setMediaIndex(index)}
                      />
                    ))}
                  </div>
                </div>
              </>
            ) : null}
          </div>

          <div className="detail-content-previews">
            <button className="detail-content-preview" type="button" aria-haspopup="dialog" onClick={() => setContentDialogOpen(true)}>
              <span className="detail-content-preview-head">
                <strong>正文</strong>
                <ChevronRight size={15} />
              </span>
              <span className={`detail-content-preview-copy${originalText ? "" : " is-empty"}`}>
                {originalText || "暂无原文正文"}
              </span>
              {translatedText ? (
                <span className="detail-content-preview-translation">
                  <strong>中文译文</strong>
                  <span>{translatedText}</span>
                </span>
              ) : null}
            </button>

            {transcript.source || transcript.translation ? (
              <button className="detail-content-preview detail-transcript-preview" type="button" aria-haspopup="dialog" onClick={() => setContentDialogOpen(true)}>
                <span className="detail-content-preview-head">
                  <strong>音视频 ASR</strong>
                  <ChevronRight size={15} />
                </span>
                {transcript.source ? <span className="detail-content-preview-copy">{transcript.source}</span> : null}
                {transcript.translation ? (
                  <span className="detail-content-preview-translation">
                    <strong>中文译文</strong>
                    <span>{transcript.translation}</span>
                  </span>
                ) : null}
              </button>
            ) : null}
          </div>
        </article>

        <article className="detail-card judgement-card">
          <header className="detail-card-header">
            <h2>研判结论与风险证据</h2>
            {detail.is_historical_config ? <span className="detail-history-note">历史配置</span> : null}
          </header>

          <section className={`detail-verdict detail-verdict-${riskTone(item.riskLevel)}`} aria-label="研判结论">
            <div className="detail-verdict-status">
              <div className="detail-verdict-icon">{riskIcon(item.riskLevel)}</div>
              <strong>{item.riskLevel}</strong>
            </div>
            <p className="detail-verdict-summary">{verdictSummary}</p>
          </section>

          <div className="detail-evidence-tabs" role="tablist" aria-label="证据类型">
            {evidenceTabs.map(({ type, label, Icon }) => {
              const count = groupsByType.get(type)?.count || 0;
              return (
                <button
                  key={type}
                  className={activeEvidenceType === type ? "is-active" : ""}
                  type="button"
                  role="tab"
                  aria-selected={activeEvidenceType === type}
                  onClick={() => setActiveEvidenceType(type)}
                >
                  <Icon size={17} />
                  <span>{label}</span>
                  <strong>{count}</strong>
                </button>
              );
            })}
          </div>

          <section className="detail-evidence-panel" aria-label={`${activeGroup.label}详情`}>
            <div className="detail-evidence-panel-head">
              <div>
                <strong>{activeGroup.label || evidenceLabel(activeEvidenceType)}</strong>
                <span>{activeGroup.count || 0} 条命中</span>
              </div>
              <div className="detail-evidence-panel-controls">
                <span>{activeGroup.confidence || "待确认"}</span>
                {activeEvidenceType === "comment" && (activeGroup.items?.length || 0) > 1 ? (
                  <span className="detail-evidence-pager">
                    <IconButton type="button" aria-label="上一条评论证据" onClick={() => scrollEvidence(-1)}>
                      <ChevronLeft size={16} />
                    </IconButton>
                    <IconButton type="button" aria-label="下一条评论证据" onClick={() => scrollEvidence(1)}>
                      <ChevronRight size={16} />
                    </IconButton>
                  </span>
                ) : null}
              </div>
            </div>

            {activeGroup.items?.length ? (
              <div ref={evidenceListRef} className={`detail-evidence-list${activeEvidenceType === "comment" ? " is-horizontal" : ""}`}>
                {activeGroup.items.map((evidence, index) => (
                  <EvidenceDetail
                    key={evidence.id || `${activeEvidenceType}-${index}`}
                    evidence={evidence}
                    group={activeGroup}
                    index={index}
                    jobId={String(result.job_id || "")}
                    comment={resolveEvidenceComment(evidence, comments)}
                    onLinkComment={handleLinkComment}
                    onAnalyzeCommentUser={handleAnalyzeCommentUser}
                    onPlayAt={playEvidenceAt}
                  />
                ))}
              </div>
            ) : (
              <div className="detail-evidence-empty">
                <CheckCircle2 size={24} />
                <span>暂无{evidenceLabel(activeEvidenceType)}命中</span>
              </div>
            )}
          </section>

          <footer className="detail-meta-bar">
            <span>来源任务 <strong>{task?.name || job?.display_name || result.job_id || "--"}</strong></span>
            <span>抓取批次 <strong>{batchLabel}</strong></span>
            <span>发布时间 <strong>{publishedAt}</strong></span>
          </footer>
        </article>
      </section>

      {contentDialogOpen ? (
        <div className="detail-transcript-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.currentTarget === event.target) setContentDialogOpen(false);
        }}>
          <section className="detail-transcript-dialog" role="dialog" aria-modal="true" aria-labelledby="detail-transcript-title">
            <header>
              <div>
                <span>原始内容</span>
                <h2 id="detail-transcript-title">正文与音视频文字</h2>
              </div>
              <IconButton type="button" aria-label="关闭文字详情" onClick={() => setContentDialogOpen(false)}>
                <X size={19} />
              </IconButton>
            </header>
            <div className="detail-transcript-body">
              {originalText ? (
                <section>
                  <h3>正文原文</h3>
                  <p dir="auto">{originalText}</p>
                </section>
              ) : null}
              {translatedText ? (
                <section>
                  <h3>正文中文译文</h3>
                  <p dir="auto">{translatedText}</p>
                </section>
              ) : null}
              {transcript.source ? (
                <section>
                  <h3>音视频 ASR 原文</h3>
                  <p dir="auto">{transcript.source}</p>
                </section>
              ) : null}
              {transcript.translation ? (
                <section>
                  <h3>音视频 ASR 中文译文</h3>
                  <p dir="auto">{transcript.translation}</p>
                </section>
              ) : null}
              {!originalText && !translatedText && !transcript.source && !transcript.translation ? (
                <div className="detail-transcript-empty">暂无可展示的正文或音视频文字</div>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}

      <section className="detail-comments-section" aria-label="逐条评论审核">
        <header className="detail-comments-header">
          <div>
            <h2>逐条评论审核</h2>
            <span>{commentTotal.toLocaleString("zh-CN")} 条</span>
          </div>
          {comments.length > INITIAL_COMMENT_RENDER_COUNT ? (
            <button type="button" aria-haspopup="dialog" onClick={() => setCommentsDrawerOpen(true)}>
              查看全部 {comments.length.toLocaleString("zh-CN")} 条
              <ChevronRight size={15} />
            </button>
          ) : null}
        </header>

        <div className="detail-comments-carousel">
          <IconButton type="button" className="detail-comment-arrow prev" aria-label="向左查看评论" onClick={() => scrollComments(-1)} disabled={!comments.length}>
            <ChevronLeft size={19} />
          </IconButton>
          <div className="detail-comment-track" ref={commentsRef}>
            {visibleComments.length ? visibleComments.map((comment) => (
              <article className="detail-comment-card" key={comment.id}>
                <header>
                  <div className="detail-comment-author">
                    <span className="detail-comment-avatar" aria-hidden="true">
                      <UserRound size={17} />
                      {comment.avatarUrl ? (
                        <img
                          src={comment.avatarUrl}
                          alt=""
                          onError={(event) => event.currentTarget.remove()}
                        />
                      ) : null}
                    </span>
                    <strong title={comment.name}>{comment.name}</strong>
                  </div>
                  <span className={`detail-comment-risk status-${evidenceStatus(comment.riskLevel).tone}`}>
                    {comment.auditStatus === "failed" ? "审核失败" : `${comment.riskScore ?? 0}分 · ${evidenceStatus(comment.riskLevel).label.replace("证据", "")}`}
                  </span>
                </header>
                <div className="detail-comment-body">
                  <p title={comment.content}>{comment.content}</p>
                  {comment.translation ? <p className="detail-comment-translation">译文：{comment.translation}</p> : null}
                  {comment.auditStatus === "failed" ? (
                    <p className="detail-comment-audit-failed">本条未完成审核，不计为 0 分</p>
                  ) : null}
                </div>
                <footer>
                  <time>{comment.region || "未知地区"} · {comment.time}</time>
                  <span>
                    <button type="button" onClick={() => void handleLinkComment(comment)}>
                      <Link2 size={13} />
                      关联
                    </button>
                    <button type="button" onClick={() => handleAnalyzeCommentUser(comment)}>
                      <UserSearch size={13} />
                      分析该用户主页
                    </button>
                  </span>
                </footer>
              </article>
            )) : (
              <div className="detail-comments-empty">暂无可审核评论</div>
            )}
          </div>
          <IconButton type="button" className="detail-comment-arrow next" aria-label="向右查看评论" onClick={() => scrollComments(1)} disabled={!comments.length}>
            <ChevronRight size={19} />
          </IconButton>
        </div>
      </section>

      {commentsDrawerOpen ? (
        <CommentReviewDrawer
          comments={comments}
          total={comments.length}
          onClose={() => setCommentsDrawerOpen(false)}
          onLinkComment={handleLinkComment}
          onAnalyzeCommentUser={handleAnalyzeCommentUser}
        />
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

type CommentRiskFilter = "all" | "high" | "medium" | "review" | "safe" | "failed";

function CommentReviewDrawer({
  comments,
  total,
  onClose,
  onLinkComment,
  onAnalyzeCommentUser
}: {
  comments: DetailComment[];
  total: number;
  onClose: () => void;
  onLinkComment: (comment: DetailComment) => Promise<void>;
  onAnalyzeCommentUser: (comment: DetailComment) => void;
}) {
  const [query, setQuery] = useState("");
  const [riskFilter, setRiskFilter] = useState<CommentRiskFilter>("all");
  const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
  const filteredComments = useMemo(() => comments.filter((comment) => {
    const tone = evidenceStatus(comment.riskLevel).tone;
    const matchesRisk = riskFilter === "all"
      || (riskFilter === "failed" ? comment.auditStatus === "failed" : comment.auditStatus !== "failed" && tone === riskFilter);
    if (!matchesRisk) return false;
    if (!normalizedQuery) return true;
    return [comment.name, comment.content, comment.translation, comment.region]
      .some((value) => value.toLocaleLowerCase("zh-CN").includes(normalizedQuery));
  }), [comments, normalizedQuery, riskFilter]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [onClose]);

  return (
    <div className="detail-comments-drawer-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <aside className="detail-comments-drawer" role="dialog" aria-modal="true" aria-labelledby="detail-comments-drawer-title">
        <header className="detail-comments-drawer-header">
          <div>
            <h2 id="detail-comments-drawer-title">全部评论</h2>
            <span>{total.toLocaleString("zh-CN")} 条</span>
          </div>
          <IconButton type="button" aria-label="关闭全部评论" onClick={onClose}>
            <X size={19} />
          </IconButton>
        </header>

        <div className="detail-comments-drawer-toolbar">
          <label className="detail-comments-drawer-search">
            <Search size={15} aria-hidden="true" />
            <input
              type="search"
              value={query}
              placeholder="搜索用户或评论内容"
              aria-label="搜索用户或评论内容"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <select value={riskFilter} aria-label="按评论风险筛选" onChange={(event) => setRiskFilter(event.target.value as CommentRiskFilter)}>
            <option value="all">全部风险</option>
            <option value="high">高危</option>
            <option value="medium">中危</option>
            <option value="review">待复核</option>
            <option value="safe">无风险</option>
            <option value="failed">审核失败</option>
          </select>
          <span>{filteredComments.length.toLocaleString("zh-CN")} 条结果</span>
        </div>

        <div className="detail-comments-drawer-list">
          {filteredComments.length ? filteredComments.map((comment) => {
            const status = evidenceStatus(comment.riskLevel);
            return (
              <article className="detail-comments-drawer-row" key={comment.id}>
                <div className="detail-comments-drawer-author">
                  <strong title={comment.name}>{comment.name}</strong>
                  <span>{comment.region || "未知地区"}</span>
                  <time>{comment.time}</time>
                </div>
                <div className="detail-comments-drawer-copy">
                  <p dir="auto">{comment.content}</p>
                  {comment.translation ? (
                    <p className="detail-comments-drawer-translation" dir="auto"><strong>译文</strong>{comment.translation}</p>
                  ) : null}
                  {comment.auditStatus === "failed" ? (
                    <p className="detail-comments-drawer-failed">本条未完成审核，不计为 0 分</p>
                  ) : null}
                </div>
                <div className="detail-comments-drawer-side">
                  <span className={`detail-comment-risk status-${status.tone}`}>
                    {comment.auditStatus === "failed" ? "审核失败" : `${comment.riskScore ?? 0}分 · ${status.label.replace("证据", "")}`}
                  </span>
                  <div>
                    <button type="button" onClick={() => void onLinkComment(comment)}>
                      <Link2 size={13} />
                      关联
                    </button>
                    <button type="button" onClick={() => onAnalyzeCommentUser(comment)}>
                      <UserSearch size={13} />
                      分析该用户主页
                    </button>
                  </div>
                </div>
              </article>
            );
          }) : (
            <div className="detail-comments-drawer-empty">没有符合条件的评论</div>
          )}
        </div>
      </aside>
    </div>
  );
}

function EvidenceDetail({
  evidence,
  group,
  index,
  jobId,
  comment,
  onLinkComment,
  onAnalyzeCommentUser,
  onPlayAt
}: {
  evidence: AuditEvidenceGroupItem;
  group: AuditEvidenceGroup;
  index: number;
  jobId: string;
  comment: DetailComment | null;
  onLinkComment: (comment: DetailComment) => Promise<void>;
  onAnalyzeCommentUser: (comment: DetailComment) => void;
  onPlayAt: (seconds: number) => void;
}) {
  const type = normalizeEvidenceType(group.type || evidence.primary_modality || evidence.source || "") || "text";
  const library = firstText(evidence.risk_library_label, group.risk_libraries?.map((item) => item.label).filter(Boolean).join("、"), "--");
  const content = firstText(evidence.content, evidence.text, evidence.ocr_text_zh, evidence.ocr_text, "--");
  const explanation = firstText(evidence.hit_explanation, evidence.reason, evidence.context, "暂无命中解释");
  const status = evidenceStatus(evidence.evidence_risk_level);
  const assetUrl = resolveMediaUrl(jobId, firstText(
    evidence.frame_asset_rel,
    evidence.asset_rel,
    evidence.review_sheet_rel,
    evidence.local_path,
    evidence.url
  ));
  const start = finiteNumber(evidence.start ?? evidence.timestamp);
  const end = finiteNumber(evidence.end);
  const position = type === "vision" || type === "ocr"
    ? [evidence.frame_number !== undefined ? `帧 ${evidence.frame_number}` : "", evidence.timestamp !== undefined ? formatEvidenceTime(evidence.timestamp) : ""].filter(Boolean).join(" · ")
    : firstText(evidence.position, evidence.source_label, evidence.source, "--");
  const ocrContext = formatOcrContext(evidence.ocr_context);
  const evidenceTitle = firstText(evidence.evidence_type, `${evidenceLabel(type)}命中`);
  const commentId = firstText(comment?.id, evidence.comment_id, evidence.id?.replace(/^comment:/, ""), "--");
  const commentName = firstText(comment?.name, evidence.nickname, "评论用户");

  return (
    <article className="detail-evidence-item">
      <header>
        <strong>{index + 1}. {evidenceTitle}</strong>
        <div className="detail-evidence-header-actions">
          {type === "comment" && comment ? (
            <>
              <button type="button" onClick={() => void onLinkComment(comment)}>
                <Link2 size={13} />
                关联
              </button>
              <button type="button" onClick={() => onAnalyzeCommentUser(comment)}>
                <UserSearch size={13} />
                分析该用户主页
              </button>
            </>
          ) : null}
          <span className={`detail-evidence-status status-${status.tone}`}>{status.label}</span>
        </div>
      </header>
      {assetUrl && (type === "vision" || type === "ocr") ? (
        <img className="detail-evidence-frame" src={assetUrl} alt={position || "命中视频帧"} />
      ) : null}
      <dl>
        <div>
          <dt>风险库</dt>
          <dd>{library}</dd>
        </div>
        {type === "ocr" ? (
          <>
            {ocrContext ? <div><dt>帧上下文</dt><dd>{ocrContext}</dd></div> : null}
            <div><dt>OCR 原文</dt><dd>{firstText(evidence.ocr_text, content)}</dd></div>
            <div><dt>中文译文</dt><dd>{firstText(evidence.ocr_text_zh, evidence.translation_zh, "--")}</dd></div>
          </>
        ) : null}
        {type === "asr" ? (
          <>
            <div><dt>时间范围</dt><dd>{formatEvidenceRange(evidence.start, evidence.end)}</dd></div>
            <div><dt>Dolphin</dt><dd>{firstText(evidence.source_text_dolphin, content)}</dd></div>
            <div><dt>MMS 复核</dt><dd>{firstText(evidence.source_text_mms, "未触发或无可对齐文本")}</dd></div>
            <div><dt>中文译文</dt><dd>{firstText(evidence.translation_zh, "--")}</dd></div>
            {evidence.asr_consistency ? <div><dt>一致性</dt><dd>{evidence.asr_consistency}</dd></div> : null}
          </>
        ) : null}
        {type === "comment" ? (
          <>
            <div><dt>评论用户</dt><dd>{commentName}</dd></div>
            <div><dt>评论ID</dt><dd>{commentId}</dd></div>
            <div><dt>评论原文</dt><dd>{content}</dd></div>
            {evidence.translation_zh ? <div><dt>中文译文</dt><dd>{evidence.translation_zh}</dd></div> : null}
          </>
        ) : null}
        {type === "text" || type === "vision" ? <div><dt>{type === "vision" ? "画面描述" : "原文"}</dt><dd>{content}</dd></div> : null}
        {type === "text" && evidence.translation_zh ? <div><dt>中文译文</dt><dd>{evidence.translation_zh}</dd></div> : null}
        <div>
          <dt>命中解释</dt>
          <dd>{explanation}</dd>
        </div>
        <div className="detail-evidence-inline">
          <dt>位置</dt>
          <dd>{position || "--"}</dd>
        </div>
      </dl>
      {type === "asr" && start !== null ? (
        <button className="detail-evidence-play" type="button" onClick={() => onPlayAt(start)}>
          <Play size={14} />
          从 {formatEvidenceTime(start)} 播放{end !== null ? `，至 ${formatEvidenceTime(end)}` : ""}
        </button>
      ) : null}
    </article>
  );
}

async function loadOutputDetail(taskId: string, outputId: string): Promise<AuditResultDetail> {
  if (/^\d+$/.test(outputId)) {
    try {
      return await fetchAuditResultDetail(outputId, taskId);
    } catch {
      // Fall back to the task result list below for non-numeric or stale route ids.
    }
  }

  const payload = await fetchAuditResults({ jobId: taskId, limit: 1000, sort: "latest", compact: true });
  const matched = (payload.items || []).find((result) => {
    return getOutputKey(result) === outputId || String(result.audit_result_id || result.id || "") === outputId;
  });
  if (!matched) {
    throw new Error("未找到该风险内容");
  }
  const resultId = matched.audit_result_id || matched.id;
  if (resultId) {
    try {
      return await fetchAuditResultDetail(resultId, taskId);
    } catch {
      return { audit_result: matched, evidence_groups: buildFallbackGroups(matched), is_historical_config: false };
    }
  }
  return { audit_result: matched, evidence_groups: buildFallbackGroups(matched), is_historical_config: false };
}

function normalizeEvidenceGroups(detail: AuditResultDetail | null): AuditEvidenceGroup[] {
  if (detail && isNoRiskOutput(detail.audit_result)) {
    return evidenceTabs.map((tab) => emptyGroup(tab.type));
  }
  const rawGroups = detail?.evidence_groups?.length ? detail.evidence_groups : detail ? buildFallbackGroups(detail.audit_result) : [];
  const byType = new Map<EvidenceType, AuditEvidenceGroup>();
  rawGroups.forEach((group) => {
    const type = normalizeEvidenceType(group.type || group.id || "");
    if (!type) return;
    const resultRiskLevel = detail?.audit_result.risk_level || "";
    const items = sortEvidenceByRisk(
      (group.items || [])
        .map((item) => {
          const itemRiskLevel = item.evidence_risk_level || item.risk_level;
          if (itemRiskLevel || !isRiskEvidenceLevel(resultRiskLevel)) return item;
          return { ...item, evidence_risk_level: resultRiskLevel };
        })
        .filter((item) => isRiskEvidenceLevel(item.evidence_risk_level || item.risk_level))
    );
    byType.set(type, {
      ...group,
      type,
      label: group.label || evidenceLabel(type),
      count: items.length,
      items
    });
  });
  return evidenceTabs.map((tab) => byType.get(tab.type) || emptyGroup(tab.type));
}

function buildFallbackGroups(result: AuditResult): AuditEvidenceGroup[] {
  const buckets = new Map<EvidenceType, AuditEvidenceGroupItem[]>();
  (result.evidence_items || []).forEach((item, index) => {
    if (!isRiskEvidenceLevel(item.evidence_risk_level)) return;
    const type = normalizeEvidenceType(item.primary_modality || item.modality || item.source || "text") || "text";
    const next: AuditEvidenceGroupItem = {
      ...item,
      id: item.id || item.evidence_id || `${type}-${index}`,
      title: item.reason || "风险证据",
      content: item.text || item.ocr_text_zh || item.ocr_text || "",
      hit_explanation: item.reason || "",
      position: item.source_label || item.source || "",
      risk_library_label: item.risk_library_label,
      evidence_risk_level: item.evidence_risk_level
    };
    buckets.set(type, [...(buckets.get(type) || []), next]);
  });
  return evidenceTabs.map((tab) => {
    const items = buckets.get(tab.type) || [];
    return { id: tab.type, type: tab.type, label: evidenceLabel(tab.type), count: items.length, items };
  });
}

function emptyGroup(type: EvidenceType): AuditEvidenceGroup {
  return { id: type, type, label: evidenceLabel(type), count: 0, items: [], confidence: "待确认" };
}

function collectOriginalMedia(result: AuditResult, item: TaskOutputItem): MediaItem[] {
  const jobId = String(result.job_id || "");
  const images: MediaItem[] = [];
  const videos: MediaItem[] = [];
  const seen = new Set<string>();

  const add = (bucket: MediaItem[], type: "image" | "video", label: string, path: unknown, caption = "") => {
    const url = resolveMediaUrl(jobId, String(path || ""));
    if (!url || seen.has(url)) return;
    seen.add(url);
    bucket.push({ id: `${type}-${bucket.length}-${url}`, type, label, url, caption: compactText(caption, 120) });
  };

  (result.evidence_index?.video_units || []).forEach((unit, index) => {
    add(videos, "video", `视频 ${index + 1}`, firstText(unit.asset_rel, unit.local_path, unit.path, unit.url, unit.video_url, unit.video_play_url, unit.video_download_url), unit.transcript_summary || "");
  });
  (result.video_results || []).forEach((video, index) => {
    add(videos, "video", `视频 ${videos.length + index + 1}`, firstText(video.asset_rel, video.local_path, video.url), video.transcript?.text || "");
  });

  (result.evidence_index?.image_units || []).forEach((image, index) => {
    add(images, "image", `图 ${index + 1}`, firstText(image.asset_rel, image.local_path, image.url), image.visual_summary || image.benign_context || "");
  });
  (result.image_analyses || []).forEach((image, index) => {
    add(images, "image", `图 ${images.length + index + 1}`, firstText(image.asset_rel, image.local_path, image.original_path, image.url), image.visual_summary || image.ocr_text || "");
  });
  recordArray(result.risk_images).forEach((image, index) => {
    add(images, "image", `图 ${images.length + index + 1}`, firstText(recordText(image, "asset_rel"), recordText(image, "local_path"), recordText(image, "path"), recordText(image, "url")), firstText(recordText(image, "summary"), recordText(image, "reason")));
  });

  if (!videos.length && !images.length && item.thumbnailUrl) {
    add(images, "image", "内容缩略图", item.thumbnailUrl);
  }

  return videos.length ? videos : images;
}

function buildOriginalText(result: AuditResult) {
  const title = firstText(result.title, result.evidence_index?.text_context?.title);
  const desc = firstText(result.desc, result.evidence_index?.text_context?.desc);
  if (title && desc && title !== desc) {
    return `标题：${title}\n正文：${desc}`;
  }
  return desc || title || "";
}

function buildTranslatedText(result: AuditResult) {
  const title = firstText(result.title_zh, result.evidence_index?.text_context?.title_zh);
  const desc = firstText(result.desc_zh, result.evidence_index?.text_context?.desc_zh);
  if (title && desc && title !== desc) {
    return `标题：${title}\n正文：${desc}`;
  }
  return desc || title || "";
}

function buildTranscript(result: AuditResult): DetailTranscript {
  const sourceParts: string[] = [];
  const translationParts: string[] = [];
  const sourceSeen = new Set<string>();
  const translationSeen = new Set<string>();
  const append = (bucket: string[], seen: Set<string>, value: unknown) => {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    bucket.push(text);
  };

  (result.video_results || []).forEach((video) => {
    const transcript = video.transcript;
    if (!transcript) return;
    append(sourceParts, sourceSeen, transcript.text);
    append(translationParts, translationSeen, firstText(transcript.text_zh, transcript.translation?.text));
  });

  const needsSourceFallback = sourceParts.length === 0;
  const needsTranslationFallback = translationParts.length === 0;
  const appendSegment = (segment: Record<string, unknown>) => {
    if (needsSourceFallback) append(sourceParts, sourceSeen, firstText(segment.source_text_dolphin, segment.source_text, segment.text));
    if (needsTranslationFallback) append(translationParts, translationSeen, firstText(segment.translation_zh, segment.text_zh));
  };
  recordArray(result.asr_segments).forEach(appendSegment);
  recordArray(result.evidence_index?.asr_segments).forEach(appendSegment);
  (result.evidence_items || [])
    .filter((item) => normalizeEvidenceType(item.primary_modality || item.modality || item.source || "") === "asr")
    .forEach((item) => {
      const record = item as Record<string, unknown>;
      if (needsSourceFallback) append(sourceParts, sourceSeen, firstText(record.source_text_dolphin, item.text));
      if (needsTranslationFallback) append(translationParts, translationSeen, item.translation_zh);
    });

  return {
    source: sourceParts.join("\n\n"),
    translation: translationParts.join("\n\n")
  };
}

function normalizeComments(comments: AuditComment[]): DetailComment[] {
  return comments
    .map((comment, index) => {
      const content = firstText(comment.content, comment.text) || "该评论未保存文字内容";
      const name = firstText(comment.nickname, comment.user_name, comment.user_unique_id, comment.short_user_id, comment.user_id, "评论用户");
      const rawIdentity = firstText(comment.sec_uid, comment.user_id, comment.user_unique_id, comment.short_user_id, name);
      const riskScore = comment.audit_status === "completed" && Number.isFinite(Number(comment.risk_score))
        ? Number(comment.risk_score)
        : null;
      return {
        id: firstText(comment.comment_id, String(comment.id || ""), `${index}`),
        name,
        avatarUrl: firstText(comment.avatar),
        region: firstText(comment.ip_location, "未知地区"),
        content,
        time: formatCommentTime(comment.create_time || comment.created_at || comment.time || ""),
        profileUrl: firstText(comment.profile_url, comment.user_url),
        rawIdentity,
        translation: firstText(comment.translation_zh),
        auditStatus: firstText(comment.audit_status, "pending"),
        riskScore,
        riskLevel: firstText(comment.risk_level, riskScore !== null ? "none" : "unknown"),
        raw: comment
      };
    })
    .sort((left, right) => (right.riskScore ?? -1) - (left.riskScore ?? -1));
}

function resolveEvidenceComment(evidence: AuditEvidenceGroupItem, comments: DetailComment[]): DetailComment | null {
  const evidenceId = firstText(evidence.comment_id, evidence.id?.replace(/^comment:/, ""));
  const exactMatch = evidenceId ? comments.find((comment) => comment.id === evidenceId) : undefined;
  if (exactMatch) return exactMatch;

  const content = firstText(evidence.content, evidence.text);
  return comments.find((comment) => comment.content === content) || null;
}

function buildCommentRelationContext(result: AuditResult, comment: DetailComment) {
  return {
    source: "comment_user_analysis",
    parent_audit_result_id: Number(result.audit_result_id || result.id || 0),
    source_job_id: result.job_id || "",
    source_comment_id: comment.id,
    suspect_platform: platformCode(result.platform || stringField(result.author || {}, "platform")),
    suspect_display_name: comment.name,
    suspect_profile_url: comment.profileUrl,
    suspect_raw_identity: comment.rawIdentity,
    source_comment_text: comment.content,
    source_risk_content: comment.content || result.summary || result.desc || ""
  };
}

function resolveMediaUrl(jobId: string, value: string) {
  const text = value.trim();
  if (!text) return "";
  if (/^https?:\/\//i.test(text) || text.startsWith("/api/")) return text;
  if (!jobId) return text;
  const marker = `/outputs/${jobId}/`;
  const path = text.includes(marker) ? text.split(marker, 2)[1] : text.replace(/^\/+/, "");
  return `/api/jobs/${encodeURIComponent(jobId)}/assets?path=${encodeURIComponent(path)}`;
}

function normalizeEvidenceType(value: string): EvidenceType | null {
  const text = value.toLowerCase();
  if (text.includes("ocr") || text.includes("image")) return "ocr";
  if (text.includes("asr") || text.includes("audio")) return "asr";
  if (text.includes("comment")) return "comment";
  if (text.includes("vision") || text.includes("frame") || text.includes("video")) return "vision";
  if (text.includes("text") || text.includes("title") || text.includes("desc")) return "text";
  return null;
}

function evidenceLabel(type: EvidenceType) {
  return evidenceTabs.find((tab) => tab.type === type)?.label || "证据";
}

function evidenceStatus(level = "") {
  const normalized = level.toLowerCase();
  if (normalized === "high" || normalized === "高危") return { label: "高风险证据", tone: "high" };
  if (normalized === "medium" || normalized === "中危") return { label: "中风险证据", tone: "medium" };
  if (normalized === "low" || normalized === "review" || normalized === "待复核") return { label: "低风险证据", tone: "review" };
  return { label: "无风险证据", tone: "safe" };
}

function isRiskEvidenceLevel(level: unknown) {
  const normalized = String(level || "").trim().toLowerCase();
  return ["low", "medium", "high", "review", "低危", "中危", "高危", "待复核"].includes(normalized);
}

function sortEvidenceByRisk(items: AuditEvidenceGroupItem[]) {
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const levelDifference = evidenceRiskRank(right.item.evidence_risk_level || right.item.risk_level)
        - evidenceRiskRank(left.item.evidence_risk_level || left.item.risk_level);
      return levelDifference || left.index - right.index;
    })
    .map(({ item }) => item);
}

function evidenceRiskRank(level: unknown) {
  const normalized = String(level || "").trim().toLowerCase();
  if (["high", "高危", "高风险"].includes(normalized)) return 3;
  if (["medium", "中危", "中风险"].includes(normalized)) return 2;
  if (["low", "review", "低危", "低风险", "待复核"].includes(normalized)) return 1;
  return 0;
}

function riskTone(level: RiskLevel) {
  if (level === "高危") return "high";
  if (level === "中危") return "medium";
  if (level === "待复核") return "review";
  return "safe";
}

function riskIcon(level: RiskLevel) {
  if (level === "高危") return <ShieldAlert size={25} />;
  if (level === "中危") return <CircleAlert size={25} />;
  if (level === "待复核") return <Clock3 size={25} />;
  return <ShieldCheck size={25} />;
}

function getPublishedAt(result: AuditResult) {
  if (result.report_snapshot_source) {
    const publishedAt = stringField(result, "published_at");
    return publishedAt ? formatDateTime(publishedAt) : "--";
  }
  const direct = firstText(
    stringField(result, "publish_time"),
    stringField(result, "published_at"),
    stringField(result, "created_time"),
    stringField(result, "time"),
    result.analyzed_at,
    result.updated_at,
    result.created_at
  );
  return direct ? formatDateTime(direct) : "--";
}

function formatCommentTime(value: string | number | undefined) {
  if (!value) return "--";
  if (typeof value === "number" || /^\d+$/.test(String(value))) {
    const numeric = Number(value);
    const timestamp = numeric > 10_000_000_000 ? numeric : numeric * 1000;
    const date = new Date(timestamp);
    if (Number.isFinite(date.getTime())) {
      return `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
    }
  }
  return formatDateTime(String(value));
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatEvidenceTime(value: unknown) {
  const seconds = finiteNumber(value);
  if (seconds === null) return "--";
  const rounded = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const rest = rounded % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function formatEvidenceRange(start: unknown, end: unknown) {
  const startText = formatEvidenceTime(start);
  const endText = formatEvidenceTime(end);
  if (startText === "--") return endText;
  return endText === "--" ? startText : `${startText} - ${endText}`;
}

function formatOcrContext(context: AuditEvidenceGroupItem["ocr_context"]) {
  if (!Array.isArray(context)) return "";
  return context
    .map((item) => {
      const frameIds = Array.isArray(item.frame_ids) ? item.frame_ids.map(String).join("、") : "";
      const source = recordText(item, "source_text");
      const translation = recordText(item, "translation_zh");
      return [frameIds ? `[${frameIds}]` : "", source ? `原文：${source}` : "", translation ? `译文：${translation}` : ""]
        .filter(Boolean)
        .join("\n");
    })
    .filter(Boolean)
    .join("\n\n");
}

function platformCode(value: string) {
  const text = value.toLowerCase();
  if (text.includes("douyin") || text === "dy" || value.includes("抖音")) return "dy";
  if (text.includes("kuaishou") || text === "ks" || value.includes("快手")) return "ks";
  return "xhs";
}

function compactText(value: string, maxLength: number) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized;
}

function firstText(...values: Array<unknown>) {
  for (const value of values) {
    const text = String(value || "").trim();
    if (text) return text;
  }
  return "";
}

function stringField(source: object, key: string) {
  const value = (source as Record<string, unknown>)[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function recordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function recordText(source: Record<string, unknown>, key: string) {
  const value = source[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}
