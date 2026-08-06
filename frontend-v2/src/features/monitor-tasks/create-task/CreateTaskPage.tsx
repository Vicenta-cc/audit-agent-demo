import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  Eye,
  ExternalLink,
  FileSearch,
  FileUp,
  Gauge,
  Info,
  MonitorPlay,
  RadioTower,
  Search,
  Upload,
  UserRoundSearch,
  Video
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "../../../components/common/Button";
import { ErrorState } from "../../../components/feedback/ErrorState";
import { LoadingState } from "../../../components/feedback/LoadingState";
import { Toast } from "../../../components/feedback/Toast";
import { fetchConfigCenterSnapshot } from "../../../services/configCenter";
import { fetchCrawlerAccounts } from "../../../services/crawlerAccounts";
import {
  createJob,
  createLocalVideoJob,
  invalidateJobsSnapshotCache
} from "../../../services/jobs";
import type { ResearchPolicy } from "../../../types/configCenter";
import type { CrawlerAccount } from "../../../types/crawlerAccounts";

type TaskType = "platform" | "live" | "user" | "video";
type PlatformCode = "xhs" | "dy" | "ks";

interface CreateTaskDraft {
  type: TaskType;
  name: string;
  platforms: PlatformCode[];
  crawlerAccountId: string;
  maxItemsPerMinute: number;
  policyId: string;
  creatorUrl: string;
  liveUrl: string;
  videoDescription: string;
}

interface TaskTypeOption {
  id: TaskType;
  title: string;
  description: string;
  icon: LucideIcon;
  unavailable?: boolean;
}

const draftStorageKey = "saasv2:create-monitor-task:draft:v1";

const taskTypes: TaskTypeOption[] = [
  {
    id: "platform",
    title: "平台内容抓取",
    description: "按话词与标签抓取笔记、视频、图片、评论和弹幕。",
    icon: Search
  },
  {
    id: "live",
    title: "直播监控",
    description: "接入实时流、回放流、弹幕流和评论流做分析。",
    icon: RadioTower,
    unavailable: true
  },
  {
    id: "user",
    title: "重点用户监控",
    description: "按账号监控主页、发布、互动、直播与疑似关联账号。",
    icon: UserRoundSearch
  },
  {
    id: "video",
    title: "本地视频分析",
    description: "上传本地视频做逐帧、OCR、ASR 和关键帧分析。",
    icon: Video
  }
];

const platformOptions: Array<{ code: PlatformCode; label: string }> = [
  { code: "xhs", label: "小红书" },
  { code: "dy", label: "抖音" },
  { code: "ks", label: "快手" }
];

const defaultDraft: CreateTaskDraft = {
  type: "platform",
  name: "未命名监控任务",
  platforms: ["dy"],
  crawlerAccountId: "",
  maxItemsPerMinute: 5,
  policyId: "",
  creatorUrl: "",
  liveUrl: "",
  videoDescription: ""
};

export function CreateTaskPage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<CreateTaskDraft>(() => readDraft());
  const [policies, setPolicies] = useState<ResearchPolicy[]>([]);
  const [accounts, setAccounts] = useState<CrawlerAccount[]>([]);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  const loadOptions = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [snapshot, crawlerAccounts] = await Promise.all([
        fetchConfigCenterSnapshot(),
        fetchCrawlerAccounts()
      ]);
      setPolicies(snapshot.policies);
      setAccounts(crawlerAccounts);
      setDraft((current) => {
        if (snapshot.policies.some((policy) => policy.id === current.policyId)) return current;
        const preferred = snapshot.policies.find((policy) => policy.status === "published") || snapshot.policies[0];
        return { ...current, policyId: preferred?.id || "" };
      });
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "创建任务所需配置加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadOptions();
  }, []);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "新建监控任务 · 内容巡查研判平台";
    return () => {
      document.title = previousTitle;
    };
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const requiresExecutionSettings = draft.type === "platform" || draft.type === "user";
  const platformAccounts = useMemo(
    () => accounts.filter((account) => draft.platforms.includes(account.platform)),
    [accounts, draft.platforms]
  );
  const availableAccounts = useMemo(
    () => platformAccounts.filter(isAccountAvailable),
    [platformAccounts]
  );

  useEffect(() => {
    if (!requiresExecutionSettings) return;
    setDraft((current) => {
      const selectedIsAvailable = availableAccounts.some((account) => account.id === current.crawlerAccountId);
      const nextAccountId = selectedIsAvailable
        ? current.crawlerAccountId
        : availableAccounts.length === 1
          ? availableAccounts[0].id
          : "";
      return nextAccountId === current.crawlerAccountId
        ? current
        : { ...current, crawlerAccountId: nextAccountId };
    });
  }, [availableAccounts, requiresExecutionSettings]);

  const selectedType = taskTypes.find((item) => item.id === draft.type) || taskTypes[0];
  const selectedPolicy = policies.find((policy) => policy.id === draft.policyId);
  const selectedPlatforms = platformOptions.filter((platform) => draft.platforms.includes(platform.code));
  const selectedPlatformLabel = selectedPlatforms.map((platform) => platform.label).join(" / ");
  const selectedAccount = accounts.find((account) => account.id === draft.crawlerAccountId);
  const previewTarget = buildTargetPreview(draft, selectedPlatformLabel, videoFile);
  const expectedOutputs = useMemo(() => buildExpectedOutputs(selectedPolicy), [selectedPolicy]);

  const updateDraft = (patch: Partial<CreateTaskDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setFormError("");
  };

  const togglePlatform = (platform: PlatformCode) => {
    setDraft((current) => {
      const selected = current.platforms.includes(platform);
      if (selected && current.platforms.length === 1) {
        setToast({ message: "至少保留一个采集平台", tone: "info" });
        return current;
      }
      const platforms = selected
        ? current.platforms.filter((item) => item !== platform)
        : [...current.platforms, platform];
      const selectedAccountStillMatches = accounts.some(
        (account) => account.id === current.crawlerAccountId && platforms.includes(account.platform)
      );
      return {
        ...current,
        platforms,
        crawlerAccountId: selectedAccountStillMatches ? current.crawlerAccountId : ""
      };
    });
    setFormError("");
  };

  const saveDraft = () => {
    try {
      window.localStorage.setItem(draftStorageKey, JSON.stringify(draft));
      setToast({ message: "草稿已保存，下次打开会自动恢复" });
    } catch {
      setToast({ message: "浏览器未允许保存草稿", tone: "info" });
    }
  };

  const submitTask = async () => {
    const error = validateDraft(draft, selectedPolicy, selectedAccount, videoFile);
    if (error) {
      setFormError(error);
      return;
    }
    if (draft.type === "live") {
      setFormError("直播监控后端尚未接入，当前只能完成页面配置与预览。");
      return;
    }

    setSubmitting(true);
    setFormError("");
    try {
      if (draft.type === "video" && videoFile) {
        await createLocalVideoJob({
          video: videoFile,
          displayName: draft.name.trim(),
          description: draft.videoDescription.trim(),
          policyId: draft.policyId
        });
      } else {
        await createJob({
          display_name: draft.name.trim(),
          platform: selectedAccount?.platform || draft.platforms[0],
          crawl_mode: draft.type === "user" ? "creator" : "search",
          keyword: "",
          keyword_source: "lexicon",
          creator_url: draft.type === "user" ? draft.creatorUrl.trim() : undefined,
          crawler_account_id: draft.crawlerAccountId,
          max_comments: 100,
          max_concurrency: 3,
          max_items_per_minute: draft.maxItemsPerMinute,
          run_crawler: true,
          source_output_id: null,
          analysis_batch_size: 5,
          policy_id: draft.policyId
        });
      }
      window.localStorage.removeItem(draftStorageKey);
      invalidateJobsSnapshotCache();
      navigate("/tasks", { replace: true, state: { createdTaskName: draft.name.trim() } });
    } catch (err) {
      setFormError(normalizeApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="create-task-page">
      <header className="create-task-header">
        <div className="create-task-heading">
          <button className="create-task-back" type="button" onClick={() => navigate("/tasks")}>
            <ArrowLeft size={18} />
            返回监控任务
          </button>
          <div>
            <h1>新建监控任务</h1>
            <p>选择来源类型，配置采集对象与研判方案，创建后回到任务列表。</p>
          </div>
        </div>
        <div className="create-task-header-actions">
          <Button type="button" variant="secondary" onClick={() => navigate("/tasks")} disabled={submitting}>
            取消
          </Button>
          <Button type="button" variant="secondary" onClick={saveDraft} disabled={submitting}>
            保存草稿
          </Button>
          <Button type="submit" form="create-monitor-task-form" variant="primary" loading={submitting}>
            创建任务
          </Button>
        </div>
      </header>

      {loading ? (
        <section className="create-task-state">
          <LoadingState label="正在加载研判方案..." />
        </section>
      ) : loadError ? (
        <section className="create-task-state">
          <ErrorState message={loadError} onRetry={() => void loadOptions()} />
        </section>
      ) : (
        <form
          id="create-monitor-task-form"
          className="create-task-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submitTask();
          }}
        >
          <section className="create-task-panel create-task-type-panel" aria-labelledby="task-type-heading">
            <StepHeading
              step="1"
              title="选择任务类型"
              description="按采集入口选择任务类型，研判范围由后续绑定方案决定。"
              id="task-type-heading"
            />
            <div className="create-task-types" role="radiogroup" aria-label="任务类型">
              {taskTypes.map((item) => {
                const Icon = item.icon;
                const selected = draft.type === item.id;
                return (
                  <button
                    className={`create-task-type${selected ? " is-selected" : ""}`}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    key={item.id}
                    onClick={() => updateDraft({ type: item.id })}
                  >
                    <span className="create-task-type-icon" aria-hidden="true">
                      <Icon size={23} />
                    </span>
                    <span className="create-task-type-title">
                      <strong>{item.title}</strong>
                      {item.unavailable ? <small>待接入</small> : null}
                    </span>
                    <span>{item.description}</span>
                  </button>
                );
              })}
            </div>
          </section>

          <div className={`create-task-collection-grid${requiresExecutionSettings ? " has-execution-settings" : ""}`}>
            <section className="create-task-panel create-collection-panel" aria-labelledby="collection-heading">
              <StepHeading
                step="2"
                title="配置采集对象"
                description={draft.type === "video" ? "选择本地视频文件并填写任务信息。" : "可同时选择多个平台，抓取范围与识别能力由研判方案决定。"}
                id="collection-heading"
              />
              <div className="create-task-field create-task-name-field">
                <label htmlFor="create-task-name">任务名称</label>
                <input
                  id="create-task-name"
                  value={draft.name}
                  placeholder="例如 重点账号软色情巡查"
                  maxLength={80}
                  onChange={(event) => updateDraft({ name: event.target.value })}
                />
              </div>

              {draft.type === "video" ? (
                <VideoUploadField
                  file={videoFile}
                  inputRef={fileInputRef}
                  description={draft.videoDescription}
                  onDescriptionChange={(value) => updateDraft({ videoDescription: value })}
                  onFileChange={setVideoFile}
                />
              ) : (
                <>
                  <div className="create-task-platforms" role="group" aria-label="采集平台">
                    {platformOptions.map((platform) => {
                      const selected = draft.platforms.includes(platform.code);
                      return (
                        <button
                          type="button"
                          role="checkbox"
                          className={`create-platform-option${selected ? " is-selected" : ""}`}
                          aria-checked={selected}
                          key={platform.code}
                          onClick={() => togglePlatform(platform.code)}
                        >
                          <span className="create-platform-check" aria-hidden="true">
                            {selected ? <Check size={13} /> : null}
                          </span>
                          {platform.label}
                        </button>
                      );
                    })}
                  </div>

                  {draft.type === "user" ? (
                    <div className="create-task-field">
                      <label htmlFor="creator-url">用户主页 URL</label>
                      <input
                        id="creator-url"
                        value={draft.creatorUrl}
                        placeholder="粘贴完整用户主页链接"
                        onChange={(event) => updateDraft({ creatorUrl: event.target.value })}
                      />
                    </div>
                  ) : null}
                  {draft.type === "live" ? (
                    <div className="create-task-field">
                      <label htmlFor="live-url">直播间 URL</label>
                      <input
                        id="live-url"
                        value={draft.liveUrl}
                        placeholder="粘贴完整直播间地址"
                        onChange={(event) => updateDraft({ liveUrl: event.target.value })}
                      />
                      <span className="create-field-hint"><Info size={14} />直播流接入服务尚未开放。</span>
                    </div>
                  ) : null}
                </>
              )}
            </section>

            {requiresExecutionSettings ? (
              <aside className="create-task-panel create-execution-panel" aria-labelledby="execution-heading">
                <div className="create-execution-heading">
                  <span aria-hidden="true"><Gauge size={18} /></span>
                  <div>
                    <h2 id="execution-heading">执行设置</h2>
                    <p>选择本次任务使用的采集账号与主内容抓取频率。</p>
                  </div>
                </div>

                <div className="create-execution-field-header">
                  <label>采集账号</label>
                  <button type="button" onClick={() => navigate("/crawler-accounts")}>
                    管理账号<ExternalLink size={13} />
                  </button>
                </div>
                {platformAccounts.length ? (
                  <div className="create-account-list" role="radiogroup" aria-label={`${selectedPlatformLabel}采集账号`}>
                    {platformAccounts.map((account) => {
                      const available = isAccountAvailable(account);
                      const selected = draft.crawlerAccountId === account.id;
                      const accountPlatformLabel = platformOptions.find((platform) => platform.code === account.platform)?.label || account.platform;
                      return (
                        <button
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          disabled={!available}
                          className={`create-account-option${selected ? " is-selected" : ""}`}
                          key={account.id}
                          onClick={() => updateDraft({ crawlerAccountId: account.id })}
                        >
                          <span className="create-account-radio" aria-hidden="true" />
                          <span className="create-account-copy">
                            <strong title={account.displayName}>{account.displayName}</strong>
                            <small>{accountPlatformLabel}{account.platformAccountId ? ` · ${account.platformAccountId}` : ""}</small>
                          </span>
                          <span className={`create-account-status is-${accountStatusKey(account)}`}>
                            {accountStatusLabel(account)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className="create-account-empty">
                    <strong>暂无所选平台账号</strong>
                    <span>前往采集账号页添加并完成登录。</span>
                  </div>
                )}

                <div className="create-frequency-field">
                  <div>
                    <label>主内容抓取频率</label>
                    <span>{draft.maxItemsPerMinute} 条/分钟</span>
                  </div>
                  <div className="create-frequency-segments" role="radiogroup" aria-label="每分钟主内容抓取上限">
                    {[1, 2, 3, 4, 5].map((value) => (
                      <button
                        type="button"
                        role="radio"
                        aria-checked={draft.maxItemsPerMinute === value}
                        className={draft.maxItemsPerMinute === value ? "is-selected" : ""}
                        key={value}
                        onClick={() => updateDraft({ maxItemsPerMinute: value })}
                      >
                        {value}
                      </button>
                    ))}
                  </div>
                </div>
              </aside>
            ) : null}
          </div>

          <div className="create-task-lower-grid">
            <section className="create-task-panel create-policy-panel" aria-labelledby="policy-heading">
              <StepHeading
                step="3"
                title="绑定研判方案"
                description="方案决定检测什么、怎么判、产出什么。"
                id="policy-heading"
              />
              {policies.length ? (
                <div className="create-policy-list" role="radiogroup" aria-label="研判方案">
                  {policies.map((policy) => {
                    const selected = policy.id === draft.policyId;
                    return (
                      <button
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        className={`create-policy-option${selected ? " is-selected" : ""}`}
                        key={policy.id}
                        onClick={() => updateDraft({ policyId: policy.id })}
                      >
                        <span className="create-policy-option-main">
                          <strong>{policy.name}</strong>
                          <span>{policy.lexiconNames.join(" / ") || "未绑定风险知识库"} · {policy.recognitionCapabilities.join(" / ") || "未配置识别能力"}</span>
                        </span>
                        <small>{policy.version.version}</small>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="create-policy-empty">
                  <FileSearch size={24} />
                  <strong>暂无可用研判方案</strong>
                  <span>请先在配置底座创建研判方案。</span>
                  <Button type="button" variant="secondary" size="small" onClick={() => navigate("/config/policies/new/edit")}>
                    新建方案
                  </Button>
                </div>
              )}
            </section>

            <aside className="create-task-panel create-task-preview" aria-labelledby="preview-heading">
              <div className="create-preview-title">
                <Eye size={18} />
                <h2 id="preview-heading">任务预览</h2>
              </div>
              <dl className="create-preview-list">
                <PreviewRow label="任务名称" value={draft.name.trim() || "未命名监控任务"} />
                <PreviewRow label="来源类型" value={selectedType.title} />
                <PreviewRow label="采集对象" value={previewTarget} />
                {requiresExecutionSettings ? <PreviewRow label="执行账号" value={selectedAccount?.displayName || "未选择"} /> : null}
                {requiresExecutionSettings ? <PreviewRow label="抓取频率" value={`${draft.maxItemsPerMinute} 条/分钟`} /> : null}
                <PreviewRow label="绑定方案" value={selectedPolicy?.name || "未选择"} />
                <PreviewRow label="风险范围" value={selectedPolicy?.lexiconNames.join(" / ") || "未配置"} />
                <PreviewRow label="采集/分析方式" value={selectedPolicy?.recognitionCapabilities.join(" / ") || "未配置"} />
                <PreviewRow label="预计产出" value={expectedOutputs.join("、")} multiline />
              </dl>
              {formError ? <div className="create-task-form-error" role="alert">{formError}</div> : null}
              <Button type="submit" variant="primary" loading={submitting} disabled={!policies.length}>
                创建任务
              </Button>
            </aside>
          </div>

          <section className="create-task-panel create-flow-panel" aria-labelledby="flow-heading">
            <div className="create-preview-title">
              <MonitorPlay size={18} />
              <h2 id="flow-heading">创建后的数据流向</h2>
            </div>
            <div className="create-flow-list">
              <FlowStep icon={RadioTower} title="监控任务" detail="执行采集 / 接入 / 上传" />
              <FlowStep icon={FileSearch} title="风险内容" detail="异常内容进入风险研判" />
              <FlowStep icon={FileUp} title="询证详情" detail="保留原贴、截图与片段" />
              <FlowStep icon={UserRoundSearch} title="重点用户归并" detail="把异常聚合到用户维度" />
            </div>
          </section>
        </form>
      )}

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

function StepHeading({ step, title, description, id }: { step: string; title: string; description: string; id: string }) {
  return (
    <div className="create-step-heading">
      <div>
        <span aria-hidden="true">{step}</span>
        <h2 id={id}>{title}</h2>
      </div>
      <p>{description}</p>
    </div>
  );
}

function VideoUploadField({
  file,
  inputRef,
  description,
  onDescriptionChange,
  onFileChange
}: {
  file: File | null;
  inputRef: React.RefObject<HTMLInputElement>;
  description: string;
  onDescriptionChange: (value: string) => void;
  onFileChange: (file: File | null) => void;
}) {
  return (
    <>
      <div className={`create-video-upload${file ? " has-file" : ""}`}>
        <input
          ref={inputRef}
          type="file"
          accept="video/*,.mkv,.avi,.m4v"
          onChange={(event) => onFileChange(event.target.files?.[0] || null)}
        />
        <span className="create-video-upload-icon"><Upload size={22} /></span>
        <span className="create-video-upload-copy">
          <strong>{file?.name || "选择本地视频文件"}</strong>
          <small>{file ? formatFileSize(file.size) : "支持 MP4、MOV、M4V、AVI、MKV、WebM"}</small>
        </span>
        <Button type="button" variant="secondary" size="small" onClick={() => inputRef.current?.click()}>
          {file ? "重新选择" : "选择文件"}
        </Button>
      </div>
      <div className="create-task-field">
        <label htmlFor="video-description">视频说明（选填）</label>
        <input
          id="video-description"
          value={description}
          placeholder="补充视频来源或分析背景"
          onChange={(event) => onDescriptionChange(event.target.value)}
        />
      </div>
    </>
  );
}

function PreviewRow({ label, value, multiline = false }: { label: string; value: string; multiline?: boolean }) {
  return (
    <div className={multiline ? "is-multiline" : ""}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function FlowStep({ icon: Icon, title, detail }: { icon: LucideIcon; title: string; detail: string }) {
  return (
    <div className="create-flow-step">
      <span><Icon size={18} /></span>
      <div><strong>{title}</strong><small>{detail}</small></div>
    </div>
  );
}

function buildTargetPreview(draft: CreateTaskDraft, platformLabel: string, videoFile: File | null) {
  if (draft.type === "video") return videoFile?.name || "待选择视频";
  if (draft.type === "user") return `${platformLabel} · ${compactUrl(draft.creatorUrl) || "待填写用户 URL"}`;
  if (draft.type === "live") return `${platformLabel} · ${compactUrl(draft.liveUrl) || "待填写直播间 URL"}`;
  return platformLabel;
}

function compactUrl(value: string) {
  const url = value.trim().replace(/[/?#]+$/, "");
  if (!url) return "";
  return url.length > 32 ? `${url.slice(0, 18)}...${url.slice(-10)}` : url;
}

function buildExpectedOutputs(policy?: ResearchPolicy) {
  if (!policy) return ["未配置"];
  const labels: Record<string, string> = {
    text: "标题/正文/评论",
    ocr: "图片/视频文字",
    asr: "语音转写",
    vision: "图片/视频画面",
    comment: "评论/弹幕"
  };
  const capabilities = policy.detectionConfigs.filter((item) => item.enabled).map((item) => labels[item.capability]).filter(Boolean);
  return ["风险内容", ...new Set(capabilities), "询证材料"];
}

function validateDraft(
  draft: CreateTaskDraft,
  policy: ResearchPolicy | undefined,
  account: CrawlerAccount | undefined,
  videoFile: File | null
) {
  if (!draft.name.trim()) return "请输入任务名称。";
  if (!policy) return "请选择一个研判方案。";
  if (draft.type === "video" && !videoFile) return "请选择需要分析的本地视频文件。";
  if (draft.type !== "video" && !draft.platforms.length) return "至少选择一个采集平台。";
  if (
    (draft.type === "platform" || draft.type === "user")
    && (!account || !draft.platforms.includes(account.platform) || !isAccountAvailable(account))
  ) {
    return "请选择一个当前可用的采集账号。";
  }
  if (draft.type === "user" && !draft.creatorUrl.trim()) return "重点用户监控需要填写完整的用户主页 URL。";
  if (draft.type === "live" && !draft.liveUrl.trim()) return "直播监控需要填写直播间 URL。";
  return "";
}

function normalizeApiError(error: unknown) {
  if (!(error instanceof Error)) return "任务创建失败，请稍后重试。";
  try {
    const payload = JSON.parse(error.message) as { detail?: string };
    return payload.detail || error.message;
  } catch {
    return error.message || "任务创建失败，请稍后重试。";
  }
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function readDraft(): CreateTaskDraft {
  try {
    const saved = JSON.parse(window.localStorage.getItem(draftStorageKey) || "null") as (
      Partial<CreateTaskDraft> & { platform?: PlatformCode }
    ) | null;
    if (!saved) return defaultDraft;
    const platforms = Array.isArray(saved.platforms)
      ? [...new Set(saved.platforms.filter(
          (item): item is PlatformCode => platformOptions.some((platform) => platform.code === item)
        ))]
      : platformOptions.some((item) => item.code === saved.platform)
        ? [saved.platform as PlatformCode]
        : defaultDraft.platforms;
    const maxItemsPerMinute = Number(saved.maxItemsPerMinute);
    return {
      ...defaultDraft,
      ...saved,
      type: taskTypes.some((item) => item.id === saved.type) ? saved.type as TaskType : defaultDraft.type,
      platforms: platforms.length ? platforms : defaultDraft.platforms,
      crawlerAccountId: typeof saved.crawlerAccountId === "string" ? saved.crawlerAccountId : "",
      maxItemsPerMinute: Number.isInteger(maxItemsPerMinute) && maxItemsPerMinute >= 1 && maxItemsPerMinute <= 5
        ? maxItemsPerMinute
        : defaultDraft.maxItemsPerMinute
    };
  } catch {
    return defaultDraft;
  }
}

function isAccountAvailable(account: CrawlerAccount) {
  return account.status === "active" && account.hasAuthState;
}

function accountStatusKey(account: CrawlerAccount) {
  if (isAccountAvailable(account)) return "active";
  if (account.status === "disabled") return "disabled";
  if (account.status === "expired") return "expired";
  return "login-required";
}

function accountStatusLabel(account: CrawlerAccount) {
  if (isAccountAvailable(account)) return "可用";
  if (account.status === "disabled") return "已停用";
  if (account.status === "expired") return "已失效";
  return "未登录";
}
