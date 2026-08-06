import { apiRequest } from "./apiClient";
import { fetchAuditResults, fetchJob, getResultTime, isRiskAuditResult } from "./jobs";
import type {
  AccountStatus,
  MonitoredAccount,
  RelatedAccount,
  RiskLevel,
  RiskOutput,
  TimelineEvent
} from "../types/focusUsers";
import type { AuditResult, RawJob } from "../types/jobs";

interface ApiMonitoredUser {
  id?: unknown;
  platform?: unknown;
  stable_key?: unknown;
  display_name?: unknown;
  profile_url?: unknown;
  raw_identity?: unknown;
  source_audit_result_id?: unknown;
  source_job_id?: unknown;
  author?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
  suspected_related_accounts?: unknown;
}

interface ApiRelatedAccount {
  id?: unknown;
  platform?: unknown;
  display_name?: unknown;
  avatar_url?: unknown;
  profile_url?: unknown;
  raw_identity?: unknown;
  source_comment_id?: unknown;
  source_comment_text?: unknown;
  source_risk_content?: unknown;
  source_audit_result_id?: unknown;
  analysis_job_id?: unknown;
  analysis_status?: unknown;
  analysis_job?: unknown;
  updated_at?: unknown;
}

type PlatformLabel = MonitoredAccount["platform"];

const platformLabels: Record<string, PlatformLabel> = {
  dy: "抖音",
  douyin: "抖音",
  xhs: "小红书",
  xiaohongshu: "小红书",
  ks: "快手",
  kuaishou: "快手"
};

const DEFAULT_MOCK_MONITORED_ACCOUNTS: MonitoredAccount[] = [
  {
    id: "focus-user-101",
    sourceJobId: "job-gambling-01",
    sourceAuditResultId: 1001,
    riskDataLoaded: true,
    updatedAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    name: "世界杯盘口小助手",
    platform: "抖音",
    platformAccountId: "DY_88920194",
    avatarUrl: "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=120&h=120&fit=crop",
    monitorScope: ["账号主页", "发布内容", "评论区"],
    status: "watching",
    metrics: {
      recentRiskCount: 14,
      riskRangeLabel: "近 30 天",
      highRiskCount: 6,
      mediumRiskCount: 5,
      pendingReviewCount: 3,
      relatedAccountCount: 3,
      lastSyncTime: "07-28 06:10",
      lastSyncAgo: "10 分钟前",
      currentTaskName: "世界杯博彩专题调查",
      currentTaskStatus: "running",
      currentTaskStatusLabel: "监控中"
    },
    latestRisk: {
      id: "risk-101-1",
      jobId: "job-gambling-01",
      title: "发布“内幕波胆私推”变体外链图片",
      level: "high",
      riskScore: 92,
      summary: "在视频画面及评论区中频繁植入“加威聊盘口”、“稳单回血”等赌博引流暗语",
      sourceType: "抖音图文",
      discoveredAt: "07-28 06:05",
      hitRules: ["赌博黑话词库", "外链暗语引流", "高危涉彩"]
    },
    riskOutputs: [
      {
        id: "risk-101-1",
        jobId: "job-gambling-01",
        title: "发布“内幕波胆私推”变体外链图片",
        level: "high",
        riskScore: 92,
        summary: "在视频画面及评论区中频繁植入“加威聊盘口”、“稳单回血”等赌博引流暗语",
        sourceType: "抖音图文",
        discoveredAt: "07-28 06:05",
        hitRules: ["赌博黑话词库", "外链暗语引流", "高危涉彩"]
      },
      {
        id: "risk-101-2",
        jobId: "job-gambling-01",
        title: "评论区批量回复“进主页看置顶口令”",
        level: "high",
        riskScore: 88,
        summary: "利用多个从属小号在热门足球视频评论区盖楼引导私聊",
        sourceType: "抖音评论",
        discoveredAt: "07-28 04:30",
        hitRules: ["违规引流知识包", "黑灰产控评"]
      },
      {
        id: "risk-101-3",
        jobId: "job-gambling-01",
        title: "短视频音频 ASR 提取到海外博彩平台推荐",
        level: "medium",
        riskScore: 74,
        summary: "视频背景音提到某境外下注网站代号",
        sourceType: "抖音视频",
        discoveredAt: "07-27 18:20",
        hitRules: ["ASR 语音涉彩识别"]
      }
    ],
    relatedAccounts: [
      {
        id: "rel-101-a",
        name: "球赛情报局",
        platform: "快手",
        avatarUrl: "https://picsum.photos/seed/rel-101-a/100/100",
        profileUrl: "",
        sourceCommentId: "comment-kuaishou-881",
        sourceCommentText: "看主页置顶，按步骤操作即可领取试玩金",
        sourceRiskContent: "快手引流评论",
        sourceAuditResultId: 2001,
        analysisJobId: "job-gambling-01",
        analysisJobName: "世界杯博彩专题调查",
        analysisStatus: "running",
        updatedAt: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
        updatedAtLabel: "07-28 05:45"
      },
      {
        id: "rel-101-b",
        name: "绿荫足彩分析",
        platform: "小红书",
        avatarUrl: "https://picsum.photos/seed/rel-101-b/100/100",
        profileUrl: "",
        sourceCommentId: "comment-xhs-992",
        sourceCommentText: "同源网址跳转，私信获取暗号",
        sourceRiskContent: "小红书涉彩笔记评论",
        sourceAuditResultId: 2002,
        analysisJobId: "job-gambling-01",
        analysisJobName: "世界杯博彩专题调查",
        analysisStatus: "completed",
        updatedAt: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
        updatedAtLabel: "07-28 04:10"
      }
    ],
    monitoringPlan: {
      lexiconLibraries: ["赌博黑话词库", "涉彩变体字包", "违规引流知识包"],
      frequency: "持续实时采集（1,280 条）",
      strategy: "文本 + OCR + ASR + 视觉",
      referencePlan: "博彩专项防范策略 v2.4",
      taskStatus: "监控中",
      taskStatusTone: "green",
      scope: ["账号主页", "发布内容", "评论区"],
      riskTypes: ["涉彩导流", "黑话暗语", "违规外链"],
      autoBackfillRule: "已分析 1,280 / 已采集 1,280 条"
    },
    timeline: [
      {
        id: "evt-101-1",
        timestamp: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
        time: "07-28 06:05",
        title: "发现高危内容 · 发布“内幕波胆私推”变体外链图片",
        description: "在视频画面及评论区中频繁植入“加威聊盘口”、“稳单回血”等赌博引流暗语",
        type: "risk"
      },
      {
        id: "evt-101-2",
        timestamp: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
        time: "07-28 05:45",
        title: "关联评论用户 · 球赛情报局 (快手)",
        description: "看主页置顶，按步骤操作即可领取试玩金",
        type: "relation"
      },
      {
        id: "evt-101-3",
        timestamp: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
        time: "07-28 04:10",
        title: "启动专项监控计划 · 博彩专项防范策略 v2.4",
        description: "已绑定 3 个词库并开启多模态审核",
        type: "plan"
      }
    ]
  },
  {
    id: "focus-user-102",
    sourceJobId: "job-marriage-02",
    sourceAuditResultId: 1002,
    riskDataLoaded: true,
    updatedAt: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
    name: "维汉情感交流驿站",
    platform: "小红书",
    platformAccountId: "RED_6728190",
    avatarUrl: "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=120&h=120&fit=crop",
    monitorScope: ["发布内容", "评论区"],
    status: "watching",
    metrics: {
      recentRiskCount: 8,
      riskRangeLabel: "近 30 天",
      highRiskCount: 3,
      mediumRiskCount: 4,
      pendingReviewCount: 1,
      relatedAccountCount: 2,
      lastSyncTime: "07-28 05:30",
      lastSyncAgo: "45 分钟前",
      currentTaskName: "维汉通婚讨论调查",
      currentTaskStatus: "running",
      currentTaskStatusLabel: "监控中"
    },
    latestRisk: {
      id: "risk-102-1",
      jobId: "job-marriage-02",
      title: "发布具有煽动性的婚俗文化对比笔记",
      level: "high",
      riskScore: 86,
      summary: "文中含有挑动民族情绪和对立言论，并引导评论区进行攻击性讨论",
      sourceType: "小红书图文",
      discoveredAt: "07-28 05:20",
      hitRules: ["民族宗教敏感词", "挑动对立词库"]
    },
    riskOutputs: [
      {
        id: "risk-102-1",
        jobId: "job-marriage-02",
        title: "发布具有煽动性的婚俗文化对比笔记",
        level: "high",
        riskScore: 86,
        summary: "文中含有挑动民族情绪和对立言论，并引导评论区进行攻击性讨论",
        sourceType: "小红书图文",
        discoveredAt: "07-28 05:20",
        hitRules: ["民族宗教敏感词", "挑动对立词库"]
      },
      {
        id: "risk-102-2",
        jobId: "job-marriage-02",
        title: "评论区高频提及敏感地名与政治黑号",
        level: "medium",
        riskScore: 71,
        summary: "多名评论者提及境外不实报道来源",
        sourceType: "小红书评论",
        discoveredAt: "07-27 21:15",
        hitRules: ["政治敏感词库"]
      }
    ],
    relatedAccounts: [
      {
        id: "rel-102-a",
        name: "西域文化视角",
        platform: "抖音",
        avatarUrl: "https://picsum.photos/seed/rel-102-a/100/100",
        profileUrl: "",
        sourceCommentId: "comment-dy-772",
        sourceCommentText: "同话题视频搬运与评论引导",
        sourceRiskContent: "抖音相关评论",
        sourceAuditResultId: 2003,
        analysisJobId: "job-marriage-02",
        analysisJobName: "维汉通婚讨论调查",
        analysisStatus: "running",
        updatedAt: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
        updatedAtLabel: "07-28 04:15"
      }
    ],
    monitoringPlan: {
      lexiconLibraries: ["民族宗教敏感词", "挑动对立词库"],
      frequency: "实时采集（850 条）",
      strategy: "文本 + 视觉",
      referencePlan: "意识形态风控方案 v1.8",
      taskStatus: "监控中",
      taskStatusTone: "green",
      scope: ["发布内容", "评论区"],
      riskTypes: ["挑动对立", "敏感言论"],
      autoBackfillRule: "已分析 850 / 已采集 850 条"
    },
    timeline: [
      {
        id: "evt-102-1",
        timestamp: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
        time: "07-28 05:20",
        title: "发现高危内容 · 煽动性婚俗笔记",
        description: "文中含有挑动民族情绪和对立言论",
        type: "risk"
      }
    ]
  },
  {
    id: "focus-user-103",
    sourceJobId: "job-contraband-03",
    sourceAuditResultId: 1003,
    riskDataLoaded: true,
    updatedAt: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
    name: "极客违禁线索观察",
    platform: "快手",
    platformAccountId: "KS_1092831",
    avatarUrl: "https://images.unsplash.com/photo-1570295999919-56ceb5ecca61?w=120&h=120&fit=crop",
    monitorScope: ["账号主页", "发布内容"],
    status: "continuous",
    metrics: {
      recentRiskCount: 5,
      riskRangeLabel: "近 30 天",
      highRiskCount: 1,
      mediumRiskCount: 3,
      pendingReviewCount: 1,
      relatedAccountCount: 1,
      lastSyncTime: "07-28 03:00",
      lastSyncAgo: "3 小时前",
      currentTaskName: "违禁物品暗语采集",
      currentTaskStatus: "completed",
      currentTaskStatusLabel: "分析完成"
    },
    latestRisk: {
      id: "risk-103-1",
      jobId: "job-contraband-03",
      title: "暗语出售违规管制器具",
      level: "high",
      riskScore: 89,
      summary: "展示变体商品名称及隐蔽联系 Telegram 账号",
      sourceType: "快手视频",
      discoveredAt: "07-28 02:45",
      hitRules: ["违禁品管制词库", "境外社交平台引流"]
    },
    riskOutputs: [
      {
        id: "risk-103-1",
        jobId: "job-contraband-03",
        title: "暗语出售违规管制器具",
        level: "high",
        riskScore: 89,
        summary: "展示变体商品名称及隐蔽联系 Telegram 账号",
        sourceType: "快手视频",
        discoveredAt: "07-28 02:45",
        hitRules: ["违禁品管制词库", "境外社交平台引流"]
      }
    ],
    relatedAccounts: [],
    monitoringPlan: {
      lexiconLibraries: ["违禁品管制词库"],
      frequency: "单次采集（500 条）",
      strategy: "文本 + OCR",
      referencePlan: "通用安全管控方案",
      taskStatus: "已完成",
      taskStatusTone: "blue",
      scope: ["账号主页", "发布内容"],
      riskTypes: ["违禁品"],
      autoBackfillRule: "已分析 500 / 已采集 500 条"
    },
    timeline: [
      {
        id: "evt-103-1",
        timestamp: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
        time: "07-28 03:00",
        title: "任务分析完成 · 违禁物品暗语采集",
        description: "采集共计 500 条内容，命中 1 条高危",
        type: "analysis"
      }
    ]
  }
];

export async function fetchMonitoredAccounts(): Promise<MonitoredAccount[]> {
  try {
    const payload = await apiRequest<{ items?: ApiMonitoredUser[] }>("/api/monitored-users");
    const items = payload.items ?? [];
    if (items.length > 0) {
      const accounts = items.map((item) => mapMonitoredAccount(item, undefined, []));
      if (accounts[0]?.sourceJobId) {
        accounts[0] = await fetchMonitoredAccountDetail(accounts[0]);
      }
      return accounts;
    }
  } catch (err) {
    console.warn("API fetch failed, falling back to local monitored accounts:", err);
  }
  return DEFAULT_MOCK_MONITORED_ACCOUNTS;
}

export async function fetchMonitoredAccountDetail(account: MonitoredAccount): Promise<MonitoredAccount> {
  if (!account.sourceJobId) return { ...account, riskDataLoaded: true };
  try {
    const [sourceJob, response] = await Promise.all([
      fetchJob(account.sourceJobId),
      fetchAuditResults({ jobId: account.sourceJobId, limit: 1000, sort: "latest", compact: true })
    ]);
    const auditResults = matchAccountResults(account, response.items ?? []);
    const recentResults = auditResults.filter((result) => isWithinDays(getResultTime(result), 30));
    const riskOutputs = recentResults
      .filter(isRiskAuditResult)
      .sort((left, right) => Date.parse(getResultTime(right)) - Date.parse(getResultTime(left)))
      .map((result) => mapRiskOutput(result, account.platform));
    const taskState = mapTaskState(sourceJob.status);
    const monitoringPlan = mapMonitoringPlan(sourceJob);
    const latestUpdatedAt = latestTimestamp([
      account.updatedAt,
      toText(sourceJob.updated_at),
      ...account.relatedAccounts.map((relation) => relation.updatedAt)
    ]);

    return {
      ...account,
      riskDataLoaded: true,
      updatedAt: latestUpdatedAt,
      status: mapAccountStatus(taskState.status),
      monitorScope: monitoringPlan.scope,
      metrics: {
        ...account.metrics,
        recentRiskCount: riskOutputs.length,
        highRiskCount: riskOutputs.filter((risk) => risk.level === "high").length,
        mediumRiskCount: riskOutputs.filter((risk) => risk.level === "medium").length,
        pendingReviewCount: recentResults.filter(isPendingReview).length,
        lastSyncTime: formatDateTime(latestUpdatedAt),
        lastSyncAgo: formatRelativeTime(latestUpdatedAt),
        currentTaskName: sourceJob.display_name || sourceJob.creator_nickname || sourceJob.id,
        currentTaskStatus: taskState.status,
        currentTaskStatusLabel: taskState.label
      },
      latestRisk: riskOutputs[0] ?? null,
      riskOutputs,
      monitoringPlan,
      timeline: buildTimeline(sourceJob, auditResults, account.relatedAccounts)
    };
  } catch (err) {
    console.warn("Detail fetch fallback to current account data:", err);
    return { ...account, riskDataLoaded: true };
  }
}

function mapMonitoredAccount(item: ApiMonitoredUser, sourceJob: RawJob | undefined, auditResults: AuditResult[]): MonitoredAccount {
  const author = asRecord(item.author);
  const relatedAccounts = toRecordArray(item.suspected_related_accounts).map(mapRelatedAccount);
  const taskState = mapTaskState(sourceJob?.status ?? "linked");
  const sourceJobId = toText(item.source_job_id);
  const sourceAuditResultId = toPositiveNumber(item.source_audit_result_id);
  const platform = mapPlatform(item.platform);
  const name = toText(item.display_name, author.nickname, author.user_unique_id, "未知账号");
  const platformAccountId = toText(
    author.user_unique_id,
    author.short_user_id,
    author.user_id,
    item.raw_identity,
    item.stable_key,
    item.id
  );
  const status = mapAccountStatus(taskState.status);
  const riskOutputs = auditResults
    .filter((result) => isRiskAuditResult(result) && isWithinDays(getResultTime(result), 30))
    .sort((left, right) => Date.parse(getResultTime(right)) - Date.parse(getResultTime(left)))
    .map((result) => mapRiskOutput(result, platform));
  const latestUpdatedAt = latestTimestamp([
    toText(item.updated_at),
    toText(sourceJob?.updated_at),
    ...relatedAccounts.map((relation) => relation.updatedAt)
  ]);
  const monitoringPlan = mapMonitoringPlan(sourceJob);

  return {
    id: toText(item.id, item.stable_key, platformAccountId),
    sourceJobId,
    sourceAuditResultId,
    riskDataLoaded: !sourceJobId,
    updatedAt: latestUpdatedAt,
    name,
    platform,
    platformAccountId,
    avatarUrl: toText(author.avatar, author.avatar_url),
    monitorScope: monitoringPlan.scope,
    status,
    metrics: {
      recentRiskCount: riskOutputs.length,
      riskRangeLabel: "近 30 天",
      highRiskCount: riskOutputs.filter((risk) => risk.level === "high").length,
      mediumRiskCount: riskOutputs.filter((risk) => risk.level === "medium").length,
      pendingReviewCount: auditResults.filter(isPendingReview).length,
      relatedAccountCount: relatedAccounts.length,
      lastSyncTime: formatDateTime(latestUpdatedAt),
      lastSyncAgo: formatRelativeTime(latestUpdatedAt),
      currentTaskName: sourceJob?.display_name || sourceJob?.creator_nickname || sourceJobId || "未关联来源任务",
      currentTaskStatus: taskState.status,
      currentTaskStatusLabel: taskState.label
    },
    latestRisk: riskOutputs[0] ?? null,
    riskOutputs,
    relatedAccounts,
    monitoringPlan,
    timeline: buildTimeline(sourceJob, auditResults, relatedAccounts)
  };
}

function matchAccountResults(account: MonitoredAccount, results: AuditResult[]): AuditResult[] {
  const identities = new Set([
    account.platformAccountId,
    account.name
  ].map((value) => toText(value).toLowerCase()).filter(Boolean));

  return results.filter((result) => {
    const resultId = toPositiveNumber(result.audit_result_id ?? result.id);
    if (account.sourceAuditResultId && resultId === account.sourceAuditResultId) return true;
    const resultAuthor = result.author ?? {};
    return [
      result.author_key,
      resultAuthor.sec_uid,
      resultAuthor.user_id,
      resultAuthor.user_unique_id,
      resultAuthor.short_user_id,
      resultAuthor.nickname
    ].some((value) => identities.has(toText(value).toLowerCase()));
  });
}

function mapRiskOutput(result: AuditResult, platform: PlatformLabel): RiskOutput {
  const id = toText(result.audit_result_id, result.id, result.content_key, result.note_id);
  const jobId = toText(result.job_id);
  const hitRules = uniqueTexts([
    ...(result.score_breakdown ?? []).map((item) => toText(item.rule, item.label, item.rule_name)),
    ...(result.categories ?? []),
    result.primary_risk
  ]).slice(0, 4);

  return {
    id,
    jobId,
    title: toText(result.content_title, result.title_zh, result.title, result.desc_zh, result.desc, "未命名风险内容"),
    level: mapRiskLevel(result.risk_level, result.decision),
    riskScore: Number.isFinite(Number(result.risk_score)) ? Number(result.risk_score) : 0,
    summary: toText(result.summary, result.risk_basis, result.desc_zh, result.desc, "该内容命中风险审核规则"),
    sourceType: `${platform}${result.duration_seconds || result.video_results?.length ? "视频" : result.image_analyses?.length ? "图文" : "内容"}`,
    discoveredAt: formatDateTime(getResultTime(result)),
    hitRules,
    coverUrl: resolveCoverUrl(result)
  };
}

function resolveCoverUrl(result: AuditResult): string | undefined {
  const candidate = result.thumbnail_asset ?? result.image_analyses?.[0] ?? result.evidence_index?.image_units?.[0];
  let url: string | undefined = undefined;

  if (candidate) {
    const remoteUrl = toText(candidate.url);
    if (remoteUrl.startsWith("http://") || remoteUrl.startsWith("https://")) {
      url = remoteUrl;
    } else {
      const jobId = toText(result.job_id);
      const path = normalizeAssetPath(toText(candidate.asset_rel, candidate.local_path), jobId);
      if (path && jobId) {
        url = `/api/jobs/${encodeURIComponent(jobId)}/assets?path=${encodeURIComponent(path)}`;
      }
    }
  }

  if (!url) {
    const seed = encodeURIComponent(toText(result.id, result.audit_result_id, "local"));
    url = `https://picsum.photos/seed/${seed}/144/256`;
  }

  return url;
}

function normalizeAssetPath(value: string, jobId: string): string {
  if (!value) return "";
  const marker = `/outputs/${jobId}/`;
  return value.includes(marker) ? value.split(marker, 2)[1] : value.replace(/^\/+/, "");
}

function mapMonitoringPlan(job: RawJob | undefined): MonitoredAccount["monitoringPlan"] {
  const revision = job?.current_audit_config_revision;
  const config = revision?.audit_config;
  const libraryIds = config?.library_ids ?? revision?.library_ids ?? job?.library_ids ?? [];
  const capabilities = config?.capabilities ?? revision?.capabilities ?? job?.capabilities ?? [];
  const scoringRules = config?.scoring_rules ?? [];
  const taskState = mapTaskState(job?.status ?? "linked");
  const ingestedCount = Number(job?.task_stats?.ingested_count ?? job?.task_stats?.batch_item_count ?? 0);
  const analyzedCount = Number(job?.task_stats?.completed_analysis_count ?? 0);
  const planName = [revision?.source_policy_name, revision?.source_policy_version].filter(Boolean).join(" ");

  return {
    lexiconLibraries: libraryIds.length ? libraryIds.map(formatLibraryName) : ["未绑定词库"],
    frequency: job ? `单次采集（${ingestedCount} 条）` : "未配置",
    strategy: capabilities.length ? capabilities.map(formatCapability).join(" + ") : "未配置",
    referencePlan: planName || revision?.id || "未引用审核方案",
    taskStatus: taskState.label,
    taskStatusTone: taskState.status === "completed" ? "blue" : taskState.status === "running" ? "green" : "orange",
    scope: buildMonitorScope(job, capabilities),
    riskTypes: uniqueTexts(scoringRules.map((rule) => toText(rule.category, rule.library_label))).length
      ? uniqueTexts(scoringRules.map((rule) => toText(rule.category, rule.library_label)))
      : libraryIds.map(formatLibraryName),
    autoBackfillRule: job ? `已分析 ${analyzedCount} / 已采集 ${ingestedCount} 条` : "未关联来源任务"
  };
}

function buildMonitorScope(job: RawJob | undefined, capabilities: string[]): string[] {
  if (!job) return ["未配置"];
  const scope = [job.crawl_mode === "creator" ? "账号主页" : "平台内容", "发布内容"];
  if (capabilities.includes("comment")) scope.push("评论区");
  return scope;
}

function buildTimeline(job: RawJob | undefined, results: AuditResult[], relatedAccounts: RelatedAccount[]): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  results.filter(isRiskAuditResult).forEach((result) => {
    const timestamp = getResultTime(result);
    const level = mapRiskLevel(result.risk_level, result.decision);
    events.push({
      id: `risk-${toText(result.audit_result_id, result.id, result.content_key)}`,
      timestamp,
      time: formatDateTime(timestamp),
      title: `发现${riskLevelLabel(level)}内容 · ${toText(result.content_title, result.title, "未命名内容")}`,
      description: toText(result.summary, result.risk_basis, result.desc, "命中风险审核规则"),
      type: "risk"
    });
  });
  (job?.logs ?? [])
    .filter((log) => /生成任务审核配置|开始任务|ingestion 完成|crawler output loaded|任务完成/.test(toText(log.message)))
    .forEach((log, index) => {
      const timestamp = toText(log.time, job?.updated_at, job?.created_at);
      events.push({
        id: `job-${index}-${timestamp}`,
        timestamp,
        time: formatDateTime(timestamp),
        title: "来源任务事件",
        description: toText(log.message),
        type: /配置/.test(toText(log.message)) ? "plan" : /分析|完成/.test(toText(log.message)) ? "analysis" : "crawl"
      });
    });
  relatedAccounts.forEach((relation) => {
    events.push({
      id: `relation-${relation.id}`,
      timestamp: relation.updatedAt,
      time: relation.updatedAtLabel,
      title: `关联评论用户 · ${relation.name}`,
      description: relation.sourceCommentText || "该评论无文本内容",
      type: "relation"
    });
  });
  return events
    .filter((event) => event.timestamp)
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
}

function mapRiskLevel(level: unknown, decision: unknown): RiskLevel {
  const normalized = toText(level).toLowerCase();
  if (["high", "高", "高危"].includes(normalized)) return "high";
  if (["medium", "mid", "中", "中危"].includes(normalized)) return "medium";
  if (["review", "pending", "待复核"].includes(normalized) || toText(decision).toLowerCase() === "review") return "review";
  return "low";
}

function isPendingReview(result: AuditResult): boolean {
  return toText(result.decision).toLowerCase() === "review" || ["review", "pending"].includes(toText(result.risk_level).toLowerCase());
}

function riskLevelLabel(level: RiskLevel): string {
  return { high: "高危", medium: "中危", review: "待复核", low: "低危" }[level];
}

function formatLibraryName(id: string): string {
  return ({ gambling: "赌博博彩" } as Record<string, string>)[id] ?? id;
}

function formatCapability(value: string): string {
  return ({ text: "文本", ocr: "OCR", asr: "ASR", vision: "视觉", comment: "评论" } as Record<string, string>)[value] ?? value;
}

function mapRelatedAccount(raw: Record<string, unknown>): RelatedAccount {
  const item = raw as ApiRelatedAccount;
  const analysisJob = asRecord(item.analysis_job);
  const updatedAt = toText(item.updated_at);
  const sourceAuditResultId = Number(item.source_audit_result_id);
  const id = toText(item.id, item.raw_identity, item.display_name);

  return {
    id,
    name: toText(item.display_name, item.raw_identity, "未知评论用户"),
    platform: mapPlatform(item.platform),
    avatarUrl: toText(item.avatar_url, `https://picsum.photos/seed/${encodeURIComponent(id)}/100/100`),
    profileUrl: toText(item.profile_url),
    sourceCommentId: toText(item.source_comment_id),
    sourceCommentText: toText(item.source_comment_text),
    sourceRiskContent: toText(item.source_risk_content),
    sourceAuditResultId: Number.isFinite(sourceAuditResultId) && sourceAuditResultId > 0 ? sourceAuditResultId : null,
    analysisJobId: toText(item.analysis_job_id, analysisJob.id),
    analysisJobName: toText(analysisJob.display_name),
    analysisStatus: toText(analysisJob.status, item.analysis_status, "linked"),
    updatedAt,
    updatedAtLabel: formatDateTime(updatedAt)
  };
}

function mapPlatform(value: unknown): PlatformLabel {
  const key = toText(value).toLowerCase();
  return platformLabels[key] ?? "抖音";
}

function mapTaskState(status: string): {
  status: MonitoredAccount["metrics"]["currentTaskStatus"];
  label: string;
} {
  const normalized = status.toLowerCase();
  if (["completed", "done", "success"].includes(normalized)) {
    return { status: "completed", label: "分析完成" };
  }
  if (["running", "analyzing", "crawling", "queued", "pending"].includes(normalized)) {
    return { status: "running", label: normalized === "queued" || normalized === "pending" ? "排队中" : "运行中" };
  }
  if (["stopped", "failed", "error"].includes(normalized)) {
    return { status: "paused", label: normalized === "stopped" ? "已停止" : "任务异常" };
  }
  return { status: "paused", label: "已关联" };
}

function mapAccountStatus(status: MonitoredAccount["metrics"]["currentTaskStatus"]): AccountStatus {
  if (status === "completed") return "completed";
  if (status === "running") return "watching";
  return "continuous";
}

function isWithinDays(value: string, days: number): boolean {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && timestamp >= Date.now() - days * 24 * 60 * 60 * 1000;
}

function latestTimestamp(values: string[]): string {
  return values.filter(Boolean).sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? "";
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "--";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hours}:${minutes}`;
}

function formatRelativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!value || Number.isNaN(timestamp)) return "暂无同步时间";
  const diff = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return formatDateTime(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function toRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

function toText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function toPositiveNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function uniqueTexts(values: unknown[]): string[] {
  return [...new Set(values.map((value) => toText(value)).filter(Boolean))];
}
