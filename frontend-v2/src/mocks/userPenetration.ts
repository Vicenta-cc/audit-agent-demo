import type {
  CommentRiskLevel,
  CommentRiskProfile,
  FocusUserPlatform,
  RiskCommentRecord,
  RiskDistributionItem
} from "../types/focusUsers";

const DAY_MS = 24 * 60 * 60 * 1000;
const WINDOW_DAYS = 30;

const riskOrder: Record<CommentRiskLevel, number> = {
  high: 3,
  medium: 2,
  review: 1
};

const riskMeta: Record<CommentRiskLevel, { label: string; color: string }> = {
  high: { label: "高危", color: "#d63b32" },
  medium: { label: "中危", color: "#efa52f" },
  review: { label: "待复核", color: "#3b82d9" }
};

const libraryColors = ["#3768df", "#327f73", "#8b5ab5", "#ca6b32", "#64748b"];

export function fetchMockCommentRiskProfiles(now = new Date()): Promise<CommentRiskProfile[]> {
  return Promise.resolve(buildCommentRiskProfiles(createMockComments(now), now));
}

export function buildCommentRiskProfiles(records: RiskCommentRecord[], now = new Date()): CommentRiskProfile[] {
  const windowStart = now.getTime() - WINDOW_DAYS * DAY_MS;
  const recentRecords = records.filter((record) => {
    const occurredAt = Date.parse(record.occurredAt);
    return Number.isFinite(occurredAt) && occurredAt >= windowStart && occurredAt <= now.getTime();
  });
  const deduplicated = new Map<string, RiskCommentRecord>();

  recentRecords.forEach((record) => {
    const key = `${record.platform}:${record.id}`;
    const current = deduplicated.get(key);
    if (!current || Date.parse(record.occurredAt) > Date.parse(current.occurredAt)) {
      deduplicated.set(key, record);
    }
  });

  const grouped = new Map<string, RiskCommentRecord[]>();
  deduplicated.forEach((record) => {
    const stableId = getStableIdentity(record);
    if (!stableId) return;
    const key = `${record.platform}:${stableId}`;
    grouped.set(key, [...(grouped.get(key) ?? []), record]);
  });

  return [...grouped.entries()]
    .map(([id, comments]) => buildProfile(id, comments))
    .filter((profile) => profile.highRiskCount >= 1 || profile.mediumRiskCount + profile.pendingReviewCount >= 2)
    .sort((left, right) => {
      const severityDifference = maxSeverity(right.comments) - maxSeverity(left.comments);
      if (severityDifference) return severityDifference;
      const countDifference = right.violationCount - left.violationCount;
      if (countDifference) return countDifference;
      return Date.parse(right.latestAt) - Date.parse(left.latestAt);
    });
}

export function formatCommentRiskTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date).replace(/\//g, "-");
}

function buildProfile(id: string, sourceComments: RiskCommentRecord[]): CommentRiskProfile {
  const comments = [...sourceComments].sort((left, right) => Date.parse(right.occurredAt) - Date.parse(left.occurredAt));
  const highRiskCount = comments.filter((comment) => comment.riskLevel === "high").length;
  const mediumRiskCount = comments.filter((comment) => comment.riskLevel === "medium").length;
  const pendingReviewCount = comments.filter((comment) => comment.riskLevel === "review").length;

  return {
    id,
    name: comments[0].authorName,
    platform: comments[0].platform,
    avatarUrl: comments[0].avatarUrl,
    violationCount: comments.length,
    highRiskCount,
    mediumRiskCount,
    pendingReviewCount,
    commentAreaCount: new Set(comments.map((comment) => comment.sourceId)).size,
    latestAt: comments[0].occurredAt,
    comments,
    riskDistribution: buildRiskDistribution({ high: highRiskCount, medium: mediumRiskCount, review: pendingReviewCount }),
    libraryDistribution: buildLibraryDistribution(comments)
  };
}

function buildRiskDistribution(counts: Record<CommentRiskLevel, number>): RiskDistributionItem[] {
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  return (["high", "medium", "review"] as const)
    .filter((key) => counts[key] > 0)
    .map((key) => ({
      key,
      label: riskMeta[key].label,
      count: counts[key],
      percentage: percentage(counts[key], total),
      color: riskMeta[key].color
    }));
}

function buildLibraryDistribution(comments: RiskCommentRecord[]): RiskDistributionItem[] {
  const counts = new Map<string, { label: string; count: number }>();
  comments.forEach((comment) => {
    const key = comment.primaryLibrary?.id || "unclassified";
    const label = comment.primaryLibrary?.label || "未归类";
    const current = counts.get(key);
    counts.set(key, { label, count: (current?.count ?? 0) + 1 });
  });

  return [...counts.entries()]
    .sort((left, right) => right[1].count - left[1].count || left[1].label.localeCompare(right[1].label, "zh-CN"))
    .map(([key, item], index) => ({
      key,
      label: item.label,
      count: item.count,
      percentage: percentage(item.count, comments.length),
      color: libraryColors[index % libraryColors.length]
    }));
}

function getStableIdentity(record: RiskCommentRecord): string {
  return record.identity.sec_uid
    || record.identity.user_id
    || record.identity.user_unique_id
    || record.identity.short_user_id
    || "";
}

function maxSeverity(comments: RiskCommentRecord[]): number {
  return Math.max(...comments.map((comment) => riskOrder[comment.riskLevel]));
}

function percentage(count: number, total: number): number {
  return total ? Math.round((count / total) * 100) : 0;
}

function createMockComments(now: Date): RiskCommentRecord[] {
  const at = (daysAgo: number, hoursAgo = 0) => new Date(now.getTime() - daysAgo * DAY_MS - hoursAgo * 60 * 60 * 1000).toISOString();
  const douyinUser = { sec_uid: "MS4wLjABAAAANG4XJEGYWrJEzfWeUhgwPXCYhoh2l7vh3KeatXqcWMg" };

  return [
    mockComment("dy-comment-1001", "抖音", douyinUser, "100siz", "想进群的看主页，暗号还是老规矩。", "high", 86, "source-dy-1", "赛事技巧交流", at(1, 2), { id: "gambling", label: "赌博黑话词库" }),
    mockComment("dy-comment-1002", "抖音", douyinUser, "100siz", "主页有联系方式，报口令就能安排。", "high", 82, "source-dy-2", "今晚比赛讨论", at(4, 5), { id: "traffic", label: "违规引流知识包" }),
    mockComment("dy-comment-1003", "抖音", douyinUser, "100siz", "稳单和回血都可以聊，懂的私信。", "medium", 68, "source-dy-3", "热门话题直播切片", at(8, 1), { id: "gambling", label: "赌博黑话词库" }),
    mockComment("dy-comment-1004", "抖音", douyinUser, "100siz", "这个口令是不是还有效，需要再确认。", "review", 51, "source-dy-2", "今晚比赛讨论", at(10, 4)),
    mockComment("dy-comment-1002", "抖音", douyinUser, "100siz", "重复抓取的旧版本评论。", "medium", 61, "source-dy-2", "今晚比赛讨论", at(5), { id: "traffic", label: "违规引流知识包" }),
    mockComment("dy-comment-2001", "抖音", { user_id: "douyin-user-2001" }, "刚满18岁", "看主页置顶，按步骤操作就行。", "high", 79, "source-dy-4", "新手交流日记", at(2, 3), { id: "traffic", label: "违规引流知识包" }),
    mockComment("xhs-comment-3001", "小红书", { user_unique_id: "xhs-user-3001" }, "初学者", "有稳定渠道的可以互换资源。", "medium", 65, "source-xhs-1", "副业交流避坑", at(3, 2), { id: "traffic", label: "违规引流知识包" }),
    mockComment("xhs-comment-3002", "小红书", { user_unique_id: "xhs-user-3001" }, "初学者", "评论里的说法看起来不太对，先留个记录。", "review", 54, "source-xhs-2", "经验分享合集", at(6, 6)),
    mockComment("dy-comment-4001", "抖音", { short_user_id: "dy-short-4001" }, "路过看看", "可以私信了解。", "medium", 60, "source-dy-5", "日常记录", at(4)),
    mockComment("dy-comment-5001", "抖音", { user_id: "dy-expired-5001" }, "历史用户", "过期高危评论。", "high", 90, "source-dy-6", "历史内容", at(34), { id: "gambling", label: "赌博黑话词库" })
  ];
}

function mockComment(
  id: string,
  platform: FocusUserPlatform,
  identity: RiskCommentRecord["identity"],
  authorName: string,
  content: string,
  riskLevel: CommentRiskLevel,
  riskScore: number,
  sourceId: string,
  sourceTitle: string,
  occurredAt: string,
  primaryLibrary?: RiskCommentRecord["primaryLibrary"]
): RiskCommentRecord {
  return {
    id,
    platform,
    identity,
    authorName,
    content,
    riskLevel,
    riskScore,
    primaryLibrary,
    sourceId,
    sourceTitle,
    occurredAt
  };
}
