import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
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
  ScanEye,
  ShieldAlert,
  ShieldCheck,
  UserSearch
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
  fetchJobs,
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
import { getOutputKey, mapAuditResultToTaskOutput } from "./taskOutputUtils";

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
  region: string;
  content: string;
  time: string;
  profileUrl: string;
  rawIdentity: string;
  raw: AuditComment;
}

const evidenceTabs: Array<{
  type: EvidenceType;
  label: string;
  shortLabel: string;
  Icon: typeof FileText;
}> = [
  { type: "text", label: "文本证据", shortLabel: "文本", Icon: FileText },
  { type: "ocr", label: "画面文字 OCR", shortLabel: "OCR", Icon: ImageIcon },
  { type: "asr", label: "音频 ASR", shortLabel: "ASR", Icon: Mic },
  { type: "comment", label: "评论证据", shortLabel: "评论", Icon: MessageSquare },
  { type: "vision", label: "视觉理解", shortLabel: "视觉", Icon: ScanEye }
];

export function TaskOutputDetailPage() {
  const { taskId = "", outputId = "" } = useParams();
  const navigate = useNavigate();
  const commentsRef = useRef<HTMLDivElement>(null);
  const [detail, setDetail] = useState<AuditResultDetail | null>(null);
  const [task, setTask] = useState<MonitorTask | null>(null);
  const [job, setJob] = useState<RawJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mediaIndex, setMediaIndex] = useState(0);
  const [activeEvidenceType, setActiveEvidenceType] = useState<EvidenceType>("text");
  const [textExpanded, setTextExpanded] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
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
      const jobsPromise = fetchJobs();
      const nextDetail = await loadOutputDetail(taskId, outputId);
      const jobs = await jobsPromise;
      const resultJobId = String(nextDetail.audit_result.job_id || taskId || "");
      const matchedJob = jobs.find((item) => item.id === resultJobId) || null;
      setDetail(nextDetail);
      setJob(matchedJob);
      setTask(matchedJob ? mapJobsToMonitorTasks([matchedJob], [nextDetail.audit_result])[0] || null : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载风险内容详情失败");
    } finally {
      setLoading(false);
    }
  }, [outputId, taskId]);

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
  const comments = useMemo(() => (detail ? normalizeComments(detail.audit_result.comments || []) : []), [detail]);

  useEffect(() => {
    const firstAvailable = evidenceTabs.find((tab) => (groupsByType.get(tab.type)?.count || 0) > 0)?.type || "text";
    setActiveEvidenceType((current) => {
      const currentCount = groupsByType.get(current)?.count || 0;
      return currentCount > 0 ? current : firstAvailable;
    });
  }, [groupsByType]);

  useEffect(() => {
    setMediaIndex(0);
    setTextExpanded(false);
  }, [outputId]);

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
  const currentMedia = mediaItems.length ? mediaItems[Math.min(mediaIndex, mediaItems.length - 1)] : null;
  const publishedAt = getPublishedAt(result);
  const batchLabel = firstText(stringField(result, "batch_id"), stringField(result, "batch"), result.job_id, "--");
  const commentTotal = Number(result.comments_count || result.comment_count || comments.length);
  const verdictSummary = firstText(item.summary, result.desc, result.title, "暂无内容摘要");

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

  return (
    <main className="task-output-detail-page">
      <section className="detail-summary-shell" aria-label="内容摘要">
        <div className="detail-breadcrumb">
          <button type="button" onClick={() => navigate(task ? `/tasks/${encodeURIComponent(task.id)}/outputs` : "/risk")}>
            <ArrowLeft size={16} />
            <span>{task ? "监控任务" : "风险研判"}</span>
          </button>
          <span>/</span>
          {task ? <Link to={`/tasks/${encodeURIComponent(task.id)}/outputs`}>{task.name}</Link> : <Link to="/risk">风险研判工作台</Link>}
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
              <button type="button" onClick={() => navigate(task ? `/tasks/${encodeURIComponent(task.id)}/outputs` : "/risk")}>返回产出列表</button>
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
                <video controls preload="metadata" playsInline src={currentMedia.url} />
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

          {currentMedia?.caption ? <p className="detail-media-caption">{currentMedia.caption}</p> : null}

          <div className={`detail-original-text${textExpanded ? " is-expanded" : ""}`}>
            <p>{originalText || "暂无原文正文"}</p>
          </div>
          {originalText.length > 96 ? (
            <button className="detail-expand-text" type="button" onClick={() => setTextExpanded((current) => !current)}>
              {textExpanded ? "收起" : "展开"}
              <ChevronRight size={15} />
            </button>
          ) : null}
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
              <span>{activeGroup.confidence || "待确认"}</span>
            </div>

            {activeGroup.items?.length ? (
              <div className="detail-evidence-list">
                {activeGroup.items.map((evidence, index) => (
                  <EvidenceDetail key={evidence.id || `${activeEvidenceType}-${index}`} evidence={evidence} group={activeGroup} />
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

      <section className="detail-comments-section" aria-label="相关评论">
        <header className="detail-comments-header">
          <div>
            <h2>相关评论</h2>
            <span>{commentTotal.toLocaleString("zh-CN")} 条</span>
          </div>
          <button type="button" onClick={() => setToast({ message: `当前已加载 ${comments.length} 条评论`, tone: "info" })}>
            查看全部 {commentTotal.toLocaleString("zh-CN")} 条
            <ChevronRight size={15} />
          </button>
        </header>

        <div className="detail-comments-carousel">
          <IconButton type="button" className="detail-comment-arrow prev" aria-label="向左查看评论" onClick={() => scrollComments(-1)} disabled={!comments.length}>
            <ChevronLeft size={19} />
          </IconButton>
          <div className="detail-comment-track" ref={commentsRef}>
            {comments.length ? comments.map((comment) => (
              <article className="detail-comment-card" key={comment.id}>
                <header>
                  <strong title={comment.name}>{comment.name}</strong>
                  <span>{comment.region || "未知地区"}</span>
                </header>
                <p title={comment.content}>{comment.content}</p>
                <footer>
                  <time>{comment.time}</time>
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
              <div className="detail-comments-empty">暂无相关评论</div>
            )}
          </div>
          <IconButton type="button" className="detail-comment-arrow next" aria-label="向右查看评论" onClick={() => scrollComments(1)} disabled={!comments.length}>
            <ChevronRight size={19} />
          </IconButton>
        </div>
      </section>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

function EvidenceDetail({ evidence, group }: { evidence: AuditEvidenceGroupItem; group: AuditEvidenceGroup }) {
  const library = firstText(evidence.risk_library_label, group.risk_libraries?.map((item) => item.label).filter(Boolean).join("、"), "--");
  const content = firstText(evidence.content, evidence.text, evidence.ocr_text_zh, evidence.ocr_text, "--");
  const explanation = firstText(evidence.hit_explanation, evidence.reason, evidence.context, "暂无命中解释");
  const status = evidenceStatus(evidence.evidence_risk_level);

  return (
    <article className="detail-evidence-item">
      <header>
        <strong>{evidence.title || evidence.rule || group.label || "证据详情"}</strong>
        <span className={`detail-evidence-status status-${status.tone}`}>{status.label}</span>
      </header>
      <dl>
        <div>
          <dt>风险库</dt>
          <dd>{library}</dd>
        </div>
        <div>
          <dt>原文</dt>
          <dd>{content}</dd>
        </div>
        <div>
          <dt>命中解释</dt>
          <dd>{explanation}</dd>
        </div>
        <div className="detail-evidence-inline">
          <dt>位置</dt>
          <dd>{firstText(evidence.position, evidence.source_label, evidence.source, "--")}</dd>
        </div>
        <div className="detail-evidence-inline">
          <dt>证据状态</dt>
          <dd>{status.label}</dd>
        </div>
      </dl>
    </article>
  );
}

async function loadOutputDetail(taskId: string, outputId: string): Promise<AuditResultDetail> {
  if (/^\d+$/.test(outputId)) {
    try {
      return await fetchAuditResultDetail(outputId);
    } catch {
      // Fall back to the task result list below for non-numeric or stale route ids.
    }
  }

  const payload = await fetchAuditResults({ jobId: taskId, limit: 1000, sort: "latest" });
  const matched = (payload.items || []).find((result) => {
    return getOutputKey(result) === outputId || String(result.audit_result_id || result.id || "") === outputId;
  });
  if (!matched) {
    throw new Error("未找到该风险内容");
  }
  const resultId = matched.audit_result_id || matched.id;
  if (resultId) {
    try {
      return await fetchAuditResultDetail(resultId);
    } catch {
      return { audit_result: matched, evidence_groups: buildFallbackGroups(matched), is_historical_config: false };
    }
  }
  return { audit_result: matched, evidence_groups: buildFallbackGroups(matched), is_historical_config: false };
}

function normalizeEvidenceGroups(detail: AuditResultDetail | null): AuditEvidenceGroup[] {
  const rawGroups = detail?.evidence_groups?.length ? detail.evidence_groups : detail ? buildFallbackGroups(detail.audit_result) : [];
  const byType = new Map<EvidenceType, AuditEvidenceGroup>();
  rawGroups.forEach((group) => {
    const type = normalizeEvidenceType(group.type || group.id || "");
    if (!type) return;
    byType.set(type, {
      ...group,
      type,
      label: group.label || evidenceLabel(type),
      count: group.count ?? group.items?.length ?? 0,
      items: group.items || []
    });
  });
  return evidenceTabs.map((tab) => byType.get(tab.type) || emptyGroup(tab.type));
}

function buildFallbackGroups(result: AuditResult): AuditEvidenceGroup[] {
  const buckets = new Map<EvidenceType, AuditEvidenceGroupItem[]>();
  (result.evidence_items || []).forEach((item, index) => {
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
  const title = firstText(result.title, result.content_title);
  const desc = firstText(result.desc, result.summary);
  if (title && desc && title !== desc) {
    return `标题：${title}\n正文：${desc}`;
  }
  return desc || title || "";
}

function normalizeComments(comments: AuditComment[]): DetailComment[] {
  return comments
    .map((comment, index) => {
      const content = firstText(comment.content, comment.text);
      const name = firstText(comment.nickname, comment.user_name, comment.user_unique_id, comment.short_user_id, comment.user_id, "评论用户");
      const rawIdentity = firstText(comment.sec_uid, comment.user_id, comment.user_unique_id, comment.short_user_id, name);
      return {
        id: firstText(comment.comment_id, String(comment.id || ""), `${index}`),
        name,
        region: firstText(comment.ip_location, "未知地区"),
        content,
        time: formatCommentTime(comment.create_time || comment.created_at || comment.time || ""),
        profileUrl: firstText(comment.profile_url, comment.user_url),
        rawIdentity,
        raw: comment
      };
    })
    .filter((comment) => comment.content);
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
  if (normalized === "low" || normalized === "review" || normalized === "待复核") return { label: "待复核证据", tone: "review" };
  return { label: "无风险证据", tone: "safe" };
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
