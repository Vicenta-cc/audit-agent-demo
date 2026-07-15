import type { MonitoredAccount } from "../types/focusUsers";

const svgData = (svg: string) => `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;

const avatar = (top: string, bottom: string, accent: string) =>
  svgData(`
    <svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${top}"/>
          <stop offset="100%" stop-color="${bottom}"/>
        </linearGradient>
      </defs>
      <rect width="120" height="120" rx="60" fill="url(#sky)"/>
      <circle cx="60" cy="52" r="18" fill="#111827" opacity=".82"/>
      <path d="M30 102c6-24 20-36 30-36s24 12 30 36" fill="#111827" opacity=".84"/>
      <path d="M0 78c25-8 53-9 120-2v44H0z" fill="${accent}" opacity=".55"/>
    </svg>
  `);

const simpleAvatar = (background: string, foreground: string, label: string) =>
  svgData(`
    <svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">
      <rect width="120" height="120" rx="60" fill="${background}"/>
      <circle cx="60" cy="46" r="20" fill="${foreground}" opacity=".9"/>
      <path d="M28 102c7-25 22-38 32-38s25 13 32 38" fill="${foreground}" opacity=".9"/>
      <text x="60" y="111" text-anchor="middle" fill="#64748b" font-size="12" font-family="Arial">${label}</text>
    </svg>
  `);

const riskCover = (left: string, right: string, caption: string) =>
  svgData(`
    <svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240">
      <defs>
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="${left}"/>
          <stop offset="100%" stop-color="${right}"/>
        </linearGradient>
      </defs>
      <rect width="320" height="240" rx="18" fill="url(#bg)"/>
      <rect x="30" y="34" width="260" height="150" rx="12" fill="#ffffff" opacity=".22"/>
      <circle cx="110" cy="105" r="30" fill="#111827" opacity=".45"/>
      <circle cx="206" cy="105" r="30" fill="#111827" opacity=".38"/>
      <rect x="68" y="156" width="184" height="12" rx="6" fill="#ffffff" opacity=".5"/>
      <text x="160" y="212" text-anchor="middle" fill="#ffffff" font-size="24" font-weight="700" font-family="Arial">${caption}</text>
    </svg>
  `);

export const focusUserAccounts: MonitoredAccount[] = [
  {
    id: "acct-menlaoshi-2024",
    name: "门老师",
    platform: "抖音",
    platformAccountId: "menlaoshi_2024",
    avatarUrl: avatar("#bfdbfe", "#1e293b", "#f59e0b"),
    monitorScope: ["主页", "发布", "评论互动"],
    status: "continuous",
    metrics: {
      recentRiskCount: 0,
      highRiskCount: 0,
      mediumRiskCount: 0,
      pendingReviewCount: 0,
      relatedAccountCount: 3,
      lastSyncTime: "07-09 17:06",
      lastSyncAgo: "2 分钟前",
      currentTaskName: "主页分析",
      currentTaskStatus: "paused",
      currentTaskStatusLabel: "已暂停"
    },
    latestRisk: null,
    riskOutputs: [],
    relatedAccounts: [
      {
        id: "rel-yuda",
        name: "@@渔夫",
        avatarUrl: avatar("#93c5fd", "#0f172a", "#f97316"),
        reason: "多次评论互动",
        tags: ["空评论疑似表情占位"],
        lastSeenAt: "07-09"
      },
      {
        id: "rel-66666666",
        name: "66666666",
        avatarUrl: simpleAvatar("#e2e8f0", "#ffffff", "66"),
        reason: "同一风险评论区出现",
        tags: ["黑话命中相似"],
        lastSeenAt: "07-09"
      },
      {
        id: "rel-zhenhaokun",
        name: "真的好困",
        avatarUrl: simpleAvatar("#fee2e2", "#9f1239", "困"),
        reason: "同黑话命中",
        tags: ["引流方式相似"],
        lastSeenAt: "07-09"
      }
    ],
    monitoringPlan: {
      lexiconLibraries: ["按关联任务审核配置"],
      frequency: "任务创建后同步",
      strategy: "评论用户主页分析",
      referencePlan: "赌博博彩研判方案 v1.1",
      taskStatus: "运行中",
      scope: ["主页", "发布内容", "评论区"],
      riskTypes: ["涉赌", "涉黑话", "敏感话题"],
      autoBackfillRule: "高危内容自动补抓，低危仅记录"
    },
    timeline: [
      {
        id: "tl-1",
        time: "07-09 17:06",
        title: "完成主页分析",
        description: "分析耗时 2 分 18 秒，未发现高危内容。",
        type: "analysis"
      },
      {
        id: "tl-2",
        time: "07-09 16:54",
        title: "发现疑似关联账号 3 个",
        description: "关联来源为评论互动与黑话命中。",
        type: "relation"
      }
    ]
  },
  {
    id: "acct-shiliu",
    name: "新疆石榴红绿",
    platform: "抖音",
    platformAccountId: "xj_shiliu_0710",
    avatarUrl: avatar("#334155", "#020617", "#f97316"),
    monitorScope: ["主页", "发布", "评论互动"],
    status: "watching",
    metrics: {
      recentRiskCount: 20,
      highRiskCount: 3,
      mediumRiskCount: 17,
      pendingReviewCount: 0,
      relatedAccountCount: 5,
      lastSyncTime: "07-09 16:54",
      lastSyncAgo: "14 分钟前",
      currentTaskName: "风险复核",
      currentTaskStatus: "running",
      currentTaskStatusLabel: "运行中"
    },
    latestRisk: {
      id: "risk-shiliu-1",
      level: "high",
      discoveredAt: "07-09 16:54",
      sourceType: "直播画面",
      summary: "疑似诱导关注与站外私聊引流，评论区出现多条同义黑话。",
      hitRules: ["涉赌内容识别", "命中规则"],
      coverUrl: riskCover("#f97316", "#2563eb", "直播画面")
    },
    riskOutputs: [
      {
        id: "risk-shiliu-1",
        level: "high",
        discoveredAt: "07-09 16:54",
        sourceType: "直播画面",
        summary: "疑似诱导关注与站外私聊引流",
        hitRules: ["涉赌内容识别"],
        coverUrl: riskCover("#f97316", "#2563eb", "直播画面")
      },
      {
        id: "risk-shiliu-2",
        level: "medium",
        discoveredAt: "07-09 15:20",
        sourceType: "评论互动",
        summary: "连续出现暗语回复与引导性话术",
        hitRules: ["违禁词命中"]
      },
      {
        id: "risk-shiliu-3",
        level: "review",
        discoveredAt: "07-09 14:10",
        sourceType: "主页发布",
        summary: "疑似共同号召，等待人工复核",
        hitRules: ["共同导流"]
      }
    ],
    relatedAccounts: [
      {
        id: "rel-maizi",
        name: "麦麦提江",
        avatarUrl: avatar("#64748b", "#111827", "#fb923c"),
        reason: "同评论区频繁出现",
        tags: ["高频互动"],
        lastSeenAt: "07-09 16:30"
      },
      {
        id: "rel-hongniang",
        name: "维汉红娘",
        avatarUrl: simpleAvatar("#dbeafe", "#2563eb", "红"),
        reason: "相似黑话命中",
        tags: ["话术相似"],
        lastSeenAt: "07-09 15:45"
      },
      {
        id: "rel-maolan",
        name: "MNL0320",
        avatarUrl: simpleAvatar("#fee2e2", "#ef4444", "M"),
        reason: "共同转评链路",
        tags: ["共同路径"],
        lastSeenAt: "07-09 15:18"
      }
    ],
    monitoringPlan: {
      lexiconLibraries: ["涉赌词库", "连赞词库"],
      frequency: "每 30 分钟",
      strategy: "综合研判策略 v1.1",
      referencePlan: "赌博博彩研判方案 v1.1",
      taskStatus: "运行中",
      scope: ["主页", "发布内容", "评论区", "直播间"],
      riskTypes: ["涉赌", "站外引流", "黑话暗语"],
      autoBackfillRule: "高危命中自动补抓，每日深度补抓"
    },
    timeline: [
      {
        id: "tl-shiliu-1",
        time: "07-09 16:54",
        title: "发现疑似风险内容",
        description: "命中涉赌内容识别规则。",
        type: "risk"
      },
      {
        id: "tl-shiliu-2",
        time: "07-09 16:30",
        title: "新增关联账号",
        description: "新增 2 个疑似共同评论账号。",
        type: "relation"
      }
    ]
  },
  {
    id: "acct-66666666",
    name: "66666666",
    platform: "抖音",
    platformAccountId: "user_66666666",
    avatarUrl: simpleAvatar("#e5e7eb", "#ffffff", "66"),
    monitorScope: ["主页", "发布", "评论互动"],
    status: "completed",
    metrics: {
      recentRiskCount: 4,
      highRiskCount: 0,
      mediumRiskCount: 2,
      pendingReviewCount: 2,
      relatedAccountCount: 2,
      lastSyncTime: "07-09 15:45",
      lastSyncAgo: "1 小时前",
      currentTaskName: "评论复核",
      currentTaskStatus: "completed",
      currentTaskStatusLabel: "分析完成"
    },
    latestRisk: {
      id: "risk-66-1",
      level: "medium",
      discoveredAt: "07-09 15:45",
      sourceType: "评论互动",
      summary: "评论区存在同质化回复，疑似参与风险话题扩散。",
      hitRules: ["黑话命中相似"],
      coverUrl: riskCover("#cbd5e1", "#475569", "评论互动")
    },
    riskOutputs: [
      {
        id: "risk-66-1",
        level: "medium",
        discoveredAt: "07-09 15:45",
        sourceType: "评论互动",
        summary: "同质化回复触发中危策略",
        hitRules: ["黑话命中相似"]
      }
    ],
    relatedAccounts: [],
    monitoringPlan: {
      lexiconLibraries: ["默认评论风险库"],
      frequency: "每日同步",
      strategy: "评论风险轻量策略",
      referencePlan: "评论风险研判方案 v0.9",
      taskStatus: "已完成",
      scope: ["评论区"],
      riskTypes: ["黑话暗语", "异常互动"],
      autoBackfillRule: "仅记录，不自动补抓"
    },
    timeline: [
      {
        id: "tl-66-1",
        time: "07-09 15:45",
        title: "完成评论复核",
        description: "1 条中危内容进入预览列表。",
        type: "analysis"
      }
    ]
  },
  {
    id: "acct-kun",
    name: "真的好困",
    platform: "抖音",
    platformAccountId: "sleepy_review",
    avatarUrl: simpleAvatar("#fee2e2", "#9f1239", "困"),
    monitorScope: ["主页", "发布", "评论互动"],
    status: "completed",
    metrics: {
      recentRiskCount: 2,
      highRiskCount: 0,
      mediumRiskCount: 1,
      pendingReviewCount: 1,
      relatedAccountCount: 4,
      lastSyncTime: "07-09 15:20",
      lastSyncAgo: "1 小时前",
      currentTaskName: "主页分析",
      currentTaskStatus: "completed",
      currentTaskStatusLabel: "分析完成"
    },
    latestRisk: null,
    riskOutputs: [
      {
        id: "risk-kun-1",
        level: "review",
        discoveredAt: "07-09 15:20",
        sourceType: "主页发布",
        summary: "引流方式相似，等待人工确认",
        hitRules: ["引流方式相似"]
      }
    ],
    relatedAccounts: [
      {
        id: "rel-kun-xiao",
        name: "小小旅行家",
        avatarUrl: avatar("#bfdbfe", "#1e293b", "#38bdf8"),
        reason: "共同路径频繁",
        tags: ["共同链路"],
        lastSeenAt: "07-09 14:30"
      }
    ],
    monitoringPlan: {
      lexiconLibraries: ["引流词库", "敏感互动库"],
      frequency: "每 2 小时",
      strategy: "用户关系轻量分析",
      referencePlan: "关联账号识别方案 v1.0",
      taskStatus: "已完成",
      scope: ["主页", "评论区"],
      riskTypes: ["站外引流", "共同互动"],
      autoBackfillRule: "中危以上补抓 1 次"
    },
    timeline: [
      {
        id: "tl-kun-1",
        time: "07-09 15:20",
        title: "完成主页分析",
        description: "识别 1 条待复核风险产出。",
        type: "analysis"
      }
    ]
  }
];
