import { useState, useEffect, useRef, useCallback } from "react";
import { ArrowLeft, Menu } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import type {
  InvestigationSession,
  PlatformCode,
  AgentExecutionPhase,
  ReportSummary,
  GroundingItem
} from "../../types/investigation";
import { initialSessions, mockAuditRuleSets, platformOptionsList } from "../../mocks/investigationMocks";
import {
  createInvestigationSession,
  resumeInvestigationTurn,
  sendInvestigationTurn,
  waitForInvestigationTurn
} from "../../services/investigations";
import { fetchPublishedReportVersion, fetchPublishedReportVersions } from "../../services/reports";
import { InvestigationSidebar, type SubViewType } from "./InvestigationSidebar";
import { InvestigationCenterArea } from "./InvestigationCenterArea";
import { InvestigationContextDrawer, type DrawerType } from "./InvestigationContextDrawer";
import { FocusUsersPage } from "../focus-users/FocusUsersPage";
import { CrawlerAccountsPage } from "../crawler-accounts/CrawlerAccountsPage";
import { buildPublishedReportSummary } from "./publishedReportSession";

interface InvestigationPageProps {
  initialSubView?: SubViewType;
}

interface InvestigationRouteState {
  restoreAnalysisProgress?: {
    recordCount: number;
    investigationTitle?: string;
  };
  restoreInvestigationState?: {
    activeSessionId?: string;
    activeSubView?: SubViewType;
  };
  restoreScrollTop?: number;
}

const INVESTIGATION_SESSIONS_STORAGE_KEY = "xhs-audit:investigation-sessions:v1";
const GAMBLING_REPORT_DEMO_TASK_ID = "2272c3692807";

function createClientMessageId(sessionId: string) {
  const nonce = typeof window.crypto?.randomUUID === "function"
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `audit-assistant:${sessionId}:${nonce}`;
}

function createInitialSessions() {
  return [...initialSessions].sort((left, right) => (
    left.id === "session-ethnic-relations" ? -1 : right.id === "session-ethnic-relations" ? 1 : 0
  ));
}

function readStoredSessions(): InvestigationSession[] | null {
  try {
    const raw = window.sessionStorage.getItem(INVESTIGATION_SESSIONS_STORAGE_KEY);
    if (!raw) return null;
    const stored = JSON.parse(raw) as { version?: number; sessions?: InvestigationSession[] };
    if (
      stored.version !== 1
      || !Array.isArray(stored.sessions)
      || stored.sessions.length === 0
      || !stored.sessions.every((session) => (
        typeof session?.id === "string"
        && typeof session.title === "string"
        && typeof session.executionPhase === "string"
        && Array.isArray(session.messages)
        && typeof session.draft === "object"
        && session.draft !== null
      ))
    ) return null;
    const sessions = stored.sessions.filter((session) => !session.id.startsWith("session-report-"));
    return sessions.length ? sessions : null;
  } catch {
    return null;
  }
}

function storeSessions(sessions: InvestigationSession[]) {
  try {
    window.sessionStorage.setItem(
      INVESTIGATION_SESSIONS_STORAGE_KEY,
      JSON.stringify({ version: 1, sessions })
    );
  } catch {
    // The investigation still works when browser storage is unavailable.
  }
}

function isEthnicRelationsSession(session: InvestigationSession) {
  return session.id === "session-ethnic-relations"
    || session.title.includes("维汉民族关系")
    || session.draft.subject.includes("维汉民族关系");
}

function normalizeInquiry(value: string) {
  return value
    .toLowerCase()
    .replace(/[\s，。！？、；：,.!?;:'"“”‘’（）()《》【】\[\]]/g, "");
}

function includesAny(value: string, keywords: string[]) {
  return keywords.some((keyword) => value.includes(keyword));
}

const ethnicRelationsGrounding: Record<string, GroundingItem> = {
  dataset: {
    id: "dataset-overview",
    label: "统计口径",
    tone: "neutral",
    title: "四个重点对象的历史研判数据",
    meta: "抖音 · 已保存内容、评论及最终审核结果",
    summary: "统计基于四个重点对象的 667 条内容及其评论数据，所有数量均来自当前专项报告的数据表。",
    facts: [
      { label: "重点对象", value: "4 个" },
      { label: "内容", value: "667 条" },
      { label: "评论", value: "18,731 条" },
      { label: "互动账号", value: "7,450 个" }
    ],
    reportSectionId: "report-data"
  },
  riskSummary: {
    id: "risk-summary",
    label: "审核结果",
    tone: "risk",
    title: "风险等级分布",
    meta: "667 条最终审核结果 · 风险内容占比 3.9%",
    summary: "26 条风险内容主要由评论区触发，高中风险集中在人身攻击、低俗辱骂、性羞辱、民族刻板印象和地域攻击。",
    facts: [
      { label: "无风险", value: "641 条" },
      { label: "高风险", value: "4 条" },
      { label: "中风险", value: "7 条" },
      { label: "低风险", value: "15 条" }
    ],
    reportSectionId: "report-data"
  },
  sharedAudience: {
    id: "shared-audience",
    label: "关系统计",
    tone: "clue",
    title: "跨对象互动更符合共同受众特征",
    meta: "评论账号交叉统计 · 7,450 个去重互动账号",
    summary: "风险评论账号没有跨对象出现，因此共同评论行为本身不足以支撑关联网络或协同传播结论。",
    facts: [
      { label: "跨两个及以上对象", value: "131 个账号" },
      { label: "跨三个对象", value: "1 个账号" },
      { label: "跨全部四个对象", value: "0 个账号" },
      { label: "风险账号跨对象", value: "0 个账号" }
    ],
    reportEvidenceId: "shared-audience"
  },
  redFlowerComments: {
    id: "red-flower-comments",
    label: "代表性评论",
    tone: "clue",
    title: "账号“🌺🌹红花🌹🌺”的代表性评论",
    meta: "覆盖 3 个重点对象 · 共 56 条评论",
    summary: "风险评论账号没有跨对象出现，因此共同评论行为本身不足以支撑关联网络或协同传播结论。",
    hideOverviewInChat: true,
    commentSamples: [
      {
        subject: "我的心好累",
        time: "2026-05-22",
        content: "ئۈرۈك تۈكلۈك ئۈرۈك دەيمىز [呲牙][呲牙]",
        translation: "我们叫它毛杏 [呲牙][呲牙]",
        riskLabel: "无风险"
      },
      {
        subject: "麦热依姆古丽",
        time: "2026-02-03",
        content: "مەن تونۇيمەن ئوبدان قىز بالا ئۇ [呲牙][呲牙][呲牙][呲牙]",
        translation: "我认识她，是个好女孩 [呲牙][呲牙][呲牙][呲牙]",
        riskLabel: "无风险"
      },
      {
        subject: "麦热依姆古丽",
        time: "2026-02-03",
        content: "ئوخشايدىكەن سىلەر بىر جۈپ قوشماق لا 😊😊😊",
        translation: "你们真像一对璧人 😊😊😊",
        riskLabel: "无风险"
      },
      {
        subject: "石榴红缘",
        time: "2025-12-09",
        content: "[咖啡][咖啡][咖啡][赞][赞][赞][玫瑰][玫瑰][玫瑰]",
        riskLabel: "无风险"
      }
    ],
    reportEvidenceId: "shared-audience"
  },
  objectComparison: {
    id: "object-comparison",
    label: "对象对比",
    tone: "warning",
    title: "四个重点对象的风险内容分布",
    meta: "内容、评论及最终风险结果交叉统计",
    summary: "“我的心好累”风险内容数量最多，但两个高风险对象的主要风险均来自评论区，不能直接等同于博主本人立场。",
    facts: [
      { label: "我的心好累", value: "201 条内容 · 13 条风险" },
      { label: "麦热依姆古丽", value: "304 条内容 · 12 条风险" },
      { label: "石榴红缘", value: "105 条内容 · 0 条风险" },
      { label: "萨娅", value: "57 条内容 · 1 条低风险" }
    ],
    reportSectionId: "report-data"
  },
  commentAttacks: {
    id: "comment-attacks",
    label: "代表性高风险证据",
    tone: "risk",
    title: "日常内容下聚集人身攻击与性羞辱评论",
    meta: "我的心好累 · 228 条评论中命中 8 条",
    summary: "风险集中在评论区，原帖发布者正文未表达相应风险。",
    quote: "ساراڭ ساراڭ ساراڭ ساراڭ ساراڭ ساراڭ",
    translation: "疯子、疯子、疯子、疯子、疯子、疯子。",
    reportEvidenceId: "comment-attacks"
  },
  ethnicStereotype: {
    id: "ethnic-stereotype",
    label: "代表性中风险证据",
    tone: "warning",
    title: "生育话题评论泛化至民族群体",
    meta: "麦热依姆古丽 · 维吾尔语评论及译文 · 47 条评论",
    summary: "评论从个人生育经历推及其他跨民族婚姻女性，形成以民族身份解释生育结果的刻板关联。",
    quote: "بۇ خەنزۇلارغا تەگكەن باشقا ئاياللارنىمۇ ئاڭلاۋاتىمەن كۆرۈۋاتىمەن ...",
    translation: "译文节选：其他嫁给汉族的女人也生不出孩子，我在网上看到、听到。",
    reportEvidenceId: "ethnic-stereotype"
  },
  regionalAttack: {
    id: "regional-attack",
    label: "代表性中风险证据",
    tone: "warning",
    title: "婚介服务视频下出现地域攻击",
    meta: "麦热依姆古丽 · 维吾尔语评论及译文 · 18 条评论",
    summary: "评论以发布者的地域流动身份为攻击基础，使用污染城市等贬损性表达。",
    quote: "نېمىشقا كەلگەنسىز بۇ خوتەنگە شەھىرىنى بۇلغىغىلى كەلدىڭىزمۇ",
    translation: "你来和田干嘛？是来把这座城市搞得乌烟瘴气的吗？",
    reportEvidenceId: "regional-attack"
  },
  normalWedding: {
    id: "normal-wedding",
    label: "无风险样本",
    tone: "safe",
    title: "河南小伙与新疆古丽婚礼记录",
    meta: "石榴红缘 · 视频及评论 · 380 条评论",
    summary: "内容记录婚礼现场，评论以祝福和婚俗讨论为主，未出现排斥通婚或民族贬损。",
    quote: "河南小伙子跟新疆阿克苏古丽结婚了。",
    reportEvidenceId: "normal-wedding"
  },
  charityContent: {
    id: "charity-content",
    label: "无风险样本",
    tone: "safe",
    title: "为暴雨受灾家庭征集资助线索",
    meta: "麦热依姆古丽 · 视频及评论 · 335 条评论",
    summary: "内容目的为灾后互助，评论区整体为祝福、点赞和求助反馈，未发现民族排斥或人身攻击。",
    reportEvidenceId: "charity-content"
  },
  suspectedRelation: {
    id: "suspected-relation",
    label: "待核验关系线索",
    tone: "clue",
    title: "“维汉胡胡~招红娘”高频互动线索",
    meta: "麦热依姆古丽相关数据 · 1 条发布记录 · 187 条评论活动",
    summary: "账号资料与互动内容呈现一定关系线索，但不足以确认现实身份关系，只能列为疑似关联、待核验。",
    reportEvidenceId: "shared-audience"
  }
};

function groundedReply(
  content: string,
  evidenceKeys: string[],
  groundingMode: "compact" | "expanded" = "expanded"
) {
  return {
    content,
    groundingItems: evidenceKeys.map((key) => ethnicRelationsGrounding[key]).filter(Boolean),
    groundingMode
  };
}

function buildEthnicRelationsReply(inquiry: string) {
  const normalized = normalizeInquiry(inquiry);

  if (includesAny(normalized, [
    "三句话", "三句", "总结", "概括", "核心结论", "主要结论", "重要结论",
    "最重要", "简要说", "简单说", "领导汇报"
  ])) {
    return groundedReply(
      "1. 本次共研判 667 条内容，其中 641 条未发现风险，正常婚恋、家庭生活与互助内容是样本主体。\n2. 发现 26 条风险内容，包括 4 条高风险、7 条中风险和 15 条低风险，风险主要由评论区的人身攻击、低俗辱骂、民族刻板印象和地域攻击触发。\n3. 7,450 个互动账号中有 131 个跨对象评论，但现有风险评论账号均未跨对象出现，当前证据不足以认定存在组织化关联或协同传播。",
      ["dataset", "riskSummary", "sharedAudience"],
      "compact"
    );
  }

  if (includesAny(normalized, [
    "共同评论", "共同账号", "跨对象账号", "跨对象互动", "同时互动", "同时评论",
    "都评论过", "多个重点对象", "交叉互动", "共同受众", "评论过几个人",
    "跨用户", "跨账号", "跨博主", "几个重点对象", "几个博主"
  ])) {
    return groundedReply(
      "四个重点对象下共有 7,450 个去重互动账号，其中 131 个账号曾跨至少两个对象评论。账号“🌺🌹红花🌹🌺”的互动范围较广，涉及 3 个重点对象，共留下 56 条评论；当前未发现其评论包含明确风险内容，因此应识别为高频共同受众，不能直接认定为关联或协同账号。",
      ["sharedAudience"]
    );
  }

  const asksForAccountPenetration = includesAny(normalized, [
    "红花", "穿透这个账号", "穿透该账号", "穿透这个用户", "穿透该用户",
    "这个账号还在哪", "该账号还在哪", "这个用户还在哪", "该用户还在哪",
    "互动时间线", "所有评论", "全部评论", "用户穿透", "账号穿透", "往下查这个人"
  ]) || (
    normalized.includes("穿透")
    && includesAny(normalized, ["账号", "用户", "这个", "该", "他", "她"])
  );

  if (asksForAccountPenetration) {
    return groundedReply(
      "已穿透账号“🌺🌹红花🌹🌺”：现有记录覆盖 3 个重点对象、共 56 条评论，互动表现为持续关注同类婚恋与家庭内容。当前评论中未发现民族攻击、侮辱歧视或煽动对立表达，也未发现其与风险评论账号形成稳定互动链路。综合判断为“高频共同受众”，不标记为风险或关联账号。",
      ["redFlowerComments"]
    );
  }

  if (includesAny(normalized, [
    "为什么没有把他判", "为什么没把他判", "为什么没有把它判", "为什么没把它判",
    "为什么没有判为风险", "为什么没判为风险",
    "为什么不算风险账号", "为什么不是风险账号", "为何不是风险账号", "为什么未判风险"
  ])) {
    return groundedReply(
      "没有将该账号判为风险账号，主要因为其 56 条评论中未发现明确的民族攻击、侮辱歧视或煽动对立表达，也未与风险评论账号形成稳定互动链路。跨 3 个对象高频互动只能说明共同兴趣和受众重叠，不能单独作为风险或关联认定依据。",
      ["sharedAudience"],
      "compact"
    );
  }

  if (
    normalized.includes("招红娘")
    || (normalized.includes("麦热依姆古丽") && includesAny(normalized, ["关联", "周边", "关系", "穿透"]))
  ) {
    return groundedReply(
      "麦热依姆古丽相关数据中，“维汉胡胡~招红娘”存在 1 条独立发布记录及 187 条评论活动，账号资料和互动内容呈现一定关系线索。但当前证据不足以确认现实身份关系，建议标记为“疑似关联、待核验”，不能直接认定为关联账号。",
      ["suspectedRelation"]
    );
  }

  if (includesAny(normalized, [
    "协同", "组织化", "关联网络", "关联关系", "是否关联", "有关系吗", "串联", "抱团"
  ])) {
    return groundedReply(
      "当前不能认定存在组织化关联。虽然发现 131 个跨对象互动账号，但现有风险评论账号均未跨对象出现，共同账号的发言也未呈现稳定的相似话术、同步时间或统一指向。现阶段更符合共同受众特征，关联结论需要更多账号身份、时间序列和传播路径证据支持。",
      ["sharedAudience"],
      "compact"
    );
  }

  if (includesAny(normalized, [
    "哪个对象", "哪个重点对象", "那个对象", "哪一个对象", "谁风险最高", "风险最高",
    "风险最突出", "风险突出", "四个对象", "重点对象对比", "对象对比", "分别怎么样"
  ])) {
    return groundedReply(
      "按风险内容数量看，“我的心好累”共 13 条风险内容，为四个对象中最多；麦热依姆古丽为 12 条，萨娅为 1 条低风险内容，石榴红缘暂未发现风险内容。“我的心好累”和麦热依姆古丽各有 2 条高风险内容，但相关高风险主要来自评论区，不能直接等同于博主本人存在高风险。",
      ["objectComparison", "commentAttacks"]
    );
  }

  if (includesAny(normalized, [
    "人工复核", "需要复核", "复核内容", "风险内容", "高风险", "中风险", "风险线索", "危险内容"
  ])) {
    return groundedReply(
      "建议优先复核 11 条高中风险内容，其中高风险 4 条、中风险 7 条；其余 15 条低风险内容可作为补充核验。复核重点应放在评论原文与译文、上下文是否完整、攻击对象是否明确，以及风险表达是否属于单次情绪宣泄。",
      ["commentAttacks", "ethnicStereotype", "regionalAttack"]
    );
  }

  if (includesAny(normalized, [
    "依据", "证据", "为什么判", "怎么判断", "原文", "译文", "上下文", "判定理由"
  ])) {
    return groundedReply(
      "相关结论同时参考原帖内容、维吾尔语原文与译文、评论上下文和已保存的审核结果。高风险判断主要由持续性人身攻击、低俗辱骂或性羞辱触发；涉及民族刻板印象和地域攻击的内容多判为中风险。",
      ["commentAttacks", "ethnicStereotype", "regionalAttack"]
    );
  }

  if (includesAny(normalized, ["正常内容", "无风险", "没有风险", "为什么没风险", "正常讨论"])) {
    return groundedReply(
      "641 条无风险内容主要涉及婚恋记录、家庭生活、日常交流、公益互助等主题。石榴红缘的 105 条内容目前均未发现风险线索；高频互动或涉及跨民族婚恋本身不构成风险，只有出现明确攻击、歧视、排斥或煽动表达时才进入风险研判。",
      ["normalWedding", "charityContent"]
    );
  }

  return groundedReply(
    "当前问题没有命中已准备的报告查询口径。你可以继续询问总体结论、重点对象对比、风险证据、人工复核内容或跨对象账号穿透。",
    [],
    "compact"
  );
}

function buildReportSummary(session: InvestigationSession): ReportSummary {
  if (isEthnicRelationsSession(session)) {
    return {
      id: "report-ethnic-relations-20260729",
      title: "维汉民族关系专项调查报告",
      totalCollected: 667,
      suspectedRisks: 26,
      suggestedReview: 26,
      keyAuthorCandidates: 4,
      findings: [
        "641 条内容未发现风险，正常婚恋、家庭生活与互助内容占样本主体；",
        "26 条风险内容主要由评论区触发，高中风险集中在人身攻击、低俗辱骂和性羞辱；",
        "发现少量民族刻板印象、地域攻击及排斥通婚表达，当前未见跨对象协同传播证据；",
        "识别 131 个跨对象互动账号，现有数据更支持共同受众特征，不宜直接认定为关联网络。"
      ],
      riskDistribution: [
        { name: "高风险", count: 4, percentage: 15.4 },
        { name: "中风险", count: 7, percentage: 26.9 },
        { name: "低风险", count: 15, percentage: 57.7 }
      ]
    };
  }

  return {
    id: `report-${Date.now()}`,
    title: `${session.draft.subject}研判报告`,
    totalCollected: 428,
    suspectedRisks: 42,
    suggestedReview: 11,
    keyAuthorCandidates: 8,
    findings: [
      `围绕‘${session.draft.subject}’话题开展全网巡查与特征聚类；`,
      "识别 42 条疑似违规线索，主要包含引流联系方式及盘口宣传；",
      "依据通用豁免规则自动排除 85% 正常学术、新闻与科普讨论；",
      "推荐对 8 个重点高频引流作者开展账号及关联图谱穿透。"
    ],
    riskDistribution: [
      { name: "外部联系方式引流", count: 21, percentage: 50 },
      { name: "盘口宣传与包赢诱导", count: 14, percentage: 33 },
      { name: "黑彩代理开户", count: 7, percentage: 17 }
    ]
  };
}

function restoreSessionFromAnalysisProgress(
  sessions: InvestigationSession[],
  investigationId: string | undefined,
  progress: InvestigationRouteState["restoreAnalysisProgress"]
) {
  if (!investigationId || !progress || progress.recordCount <= 0) return sessions;

  return sessions.map((session) => {
    if (
      session.id !== investigationId
      || session.executionPhase !== "idle"
      || session.status === "报告已生成"
    ) return session;

    const targetRecordCount = isEthnicRelationsSession(session) ? 10 : 4;
    const isCompleted = progress.recordCount >= targetRecordCount;
    const messages: InvestigationSession["messages"] = session.messages.map((message) => (
      message.type === "task_proposal" && message.proposalData
        ? {
            ...message,
            proposalData: {
              ...message.proposalData,
              platformsSelected: session.draft.platforms.length > 0 ? session.draft.platforms : ["dy"],
              platformsConfirmed: true
            }
          }
        : message
    ));

    if (!messages.some((message) => message.type === "agent_collaboration")) {
      messages.push({
        id: `msg-agent-restored-${Date.now()}`,
        sender: "assistant",
        timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
        type: "agent_collaboration"
      });
    }

    const restoredSession: InvestigationSession = {
      ...session,
      title: progress.investigationTitle || session.title,
      status: isCompleted ? "报告已生成" : "研判中",
      draft: {
        ...session.draft,
        platforms: session.draft.platforms.length > 0 ? session.draft.platforms : ["dy"],
        status: "已创建",
        confirmed: true
      },
      executionPhase: isCompleted ? "completed" : "audit_working",
      executionProgress: isCompleted
        ? 100
        : Math.min(95, Math.max(session.executionProgress, Math.round(progress.recordCount / targetRecordCount * 80))),
      messages
    };

    if (isCompleted && !messages.some((message) => message.type === "report_card")) {
      restoredSession.messages = [
        ...messages,
        {
          id: `msg-report-restored-${Date.now()}`,
          sender: "assistant",
          timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
          type: "report_card",
          reportData: buildReportSummary(restoredSession)
        }
      ];
    }

    return restoredSession;
  });
}

export function InvestigationPage({ initialSubView = null }: InvestigationPageProps) {
  const { investigationId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const routeState = (location.state || {}) as InvestigationRouteState;
  const [sessions, setSessions] = useState<InvestigationSession[]>(() => (
    restoreSessionFromAnalysisProgress(
      readStoredSessions() || createInitialSessions(),
      investigationId,
      routeState.restoreAnalysisProgress
    )
  ));
  const [activeSessionId, setActiveSessionId] = useState<string>(() => (
    routeState.restoreInvestigationState?.activeSessionId
    && sessions.some((session) => session.id === routeState.restoreInvestigationState?.activeSessionId)
      ? routeState.restoreInvestigationState.activeSessionId
      : investigationId && sessions.some((session) => session.id === investigationId)
        ? investigationId
        : sessions[0].id
  ));
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => (
    window.matchMedia("(max-width: 760px)").matches
  ));
  const [activeDrawer, setActiveDrawer] = useState<DrawerType>(null);
  const [activeSubView, setActiveSubView] = useState<SubViewType>(
    routeState.restoreInvestigationState?.activeSubView ?? initialSubView
  );
  const [sendingMessageSessionId, setSendingMessageSessionId] = useState("");
  const subViewScrollRef = useRef<HTMLDivElement>(null);
  const loadingPublishedReportsRef = useRef(new Set<string>());
  const pendingTurnControllersRef = useRef(new Map<string, AbortController>());

  useEffect(() => {
    const prevTitle = document.title;
    document.title = "智能调查工作区 · 内容巡查研判平台";
    return () => {
      document.title = prevTitle;
    };
  }, []);

  useEffect(() => {
    storeSessions(sessions);
  }, [sessions]);

  useEffect(() => {
    if (!activeSubView || !subViewScrollRef.current || routeState.restoreScrollTop === undefined) return;
    subViewScrollRef.current.scrollTop = routeState.restoreScrollTop;
  }, [activeSubView, routeState.restoreScrollTop]);

  useEffect(() => {
    if (
      !investigationId
      || activeSessionId === investigationId
      || !sessions.some((session) => session.id === investigationId)
    ) return;
    setActiveSessionId(investigationId);
    setActiveDrawer(null);
  }, [activeSessionId, investigationId, sessions]);

  useEffect(() => {
    if (!investigationId || sessions.some((session) => session.id === investigationId)) return;
    navigate(`/investigation/${encodeURIComponent(activeSessionId)}`, { replace: true });
  }, [activeSessionId, investigationId, navigate, sessions]);

  const activeSession = sessions.find((s) => s.id === activeSessionId) || sessions[0];
  const activeRuleSet = mockAuditRuleSets.find((ruleSet) => ruleSet.name === activeSession.draft.matchedRuleSet)
    || mockAuditRuleSets[0];

  // Helper to update state of active session
  const updateActiveSession = (updater: (prev: InvestigationSession) => InvestigationSession) => {
    setSessions((prevSessions) =>
      prevSessions.map((s) => (s.id === activeSessionId ? updater(s) : s))
    );
  };

  const continuePublishedReportTurn = useCallback(async (
    uiSessionId: string,
    turnId: string,
    resumeAttempted = false
  ) => {
    if (pendingTurnControllersRef.current.has(turnId)) return;
    const controller = new AbortController();
    pendingTurnControllersRef.current.set(turnId, controller);
    setSendingMessageSessionId(uiSessionId);

    const waitForTerminal = (afterSequence = 0, resumeReplay = false) => waitForInvestigationTurn(turnId, {
      signal: controller.signal,
      afterSequence,
      resumeReplay,
      onEvent: (event) => {
        setSessions((current) => current.map((item) => (
          item.id === uiSessionId
          && item.reportBinding?.pendingTurn?.turnId === turnId
            ? {
                ...item,
                reportBinding: {
                  ...item.reportBinding,
                  pendingTurn: {
                    ...item.reportBinding.pendingTurn,
                    stage: event.stage
                  }
                }
              }
            : item
        )));
      }
    });

    try {
      let result = await waitForTerminal();
      if (result.status === "interrupted" && result.retryable && !resumeAttempted) {
        resumeAttempted = true;
        setSessions((current) => current.map((item) => (
          item.id === uiSessionId
          && item.reportBinding?.pendingTurn?.turnId === turnId
            ? {
                ...item,
                reportBinding: {
                  ...item.reportBinding,
                  pendingTurn: {
                    ...item.reportBinding.pendingTurn,
                    stage: "accepted",
                    resumeAttempted: true
                  }
                }
              }
            : item
        )));
        await resumeInvestigationTurn(turnId);
        result = await waitForTerminal(
          result.event_sequence || 0,
          !result.event_sequence
        );
      }

      const answer = result.status === "completed"
        ? result.answer.trim()
        : result.safe_message.trim();
      if (!answer) throw new Error("Investigation Turn returned no public answer");
      setSessions((current) => current.map((item) => {
        if (item.id !== uiSessionId || !item.reportBinding) return item;
        const answerId = `msg-answer-${turnId}`;
        return {
          ...item,
          updatedAt: "刚刚",
          reportBinding: {
            ...item.reportBinding,
            pendingTurn: undefined
          },
          messages: item.messages.some((message) => message.id === answerId)
            ? item.messages
            : [
                ...item.messages,
                {
                  id: answerId,
                  sender: "assistant",
                  timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
                  content: answer,
                  type: result.status === "completed" ? "grounded_answer" : "text"
                }
              ]
        };
      }));
    } catch (error) {
      if (!controller.signal.aborted) {
        console.error("Failed to finish published report question", error);
        setSessions((current) => current.map((item) => {
          if (item.id !== uiSessionId || !item.reportBinding) return item;
          const answerId = `msg-answer-error-${turnId}`;
          return {
            ...item,
            reportBinding: {
              ...item.reportBinding,
              pendingTurn: undefined
            },
            messages: item.messages.some((message) => message.id === answerId)
              ? item.messages
              : [
                  ...item.messages,
                  {
                    id: answerId,
                    sender: "assistant",
                    timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
                    content: "这次报告问答暂时无法完成，请稍后重试。",
                    type: "text"
                  }
                ]
          };
        }));
      }
    } finally {
      pendingTurnControllersRef.current.delete(turnId);
      setSendingMessageSessionId((current) => current === uiSessionId ? "" : current);
    }
  }, []);

  const sendPublishedReportQuestion = async (
    session: InvestigationSession,
    text: string,
    clientMessageId: string
  ) => {
    const binding = session.reportBinding;
    if (!binding) return;

    try {
      let investigationSessionId = binding.investigationSessionId;
      if (!investigationSessionId) {
        const created = await createInvestigationSession(binding.reportVersionId);
        investigationSessionId = created.session_id;
        setSessions((current) => current.map((item) => (
          item.id === session.id && item.reportBinding
            ? {
                ...item,
                reportBinding: {
                  ...item.reportBinding,
                  investigationSessionId: created.session_id
                }
              }
            : item
        )));
      }

      const result = await sendInvestigationTurn(investigationSessionId, {
        clientMessageId,
        content: text
      });
      setSessions((current) => current.map((item) => (
        item.id === session.id && item.reportBinding
          ? {
              ...item,
              updatedAt: "刚刚",
              reportBinding: {
                ...item.reportBinding,
                investigationSessionId,
                pendingTurn: {
                  turnId: result.turn_id,
                  clientMessageId,
                  question: text,
                  stage: "accepted"
                }
              }
            }
          : item
      )));
    } catch (error) {
      console.error("Failed to answer published report question", error);
      setSessions((current) => current.map((item) => (
        item.id === session.id
          ? {
              ...item,
              messages: [
                ...item.messages,
                {
                  id: `msg-answer-error-${Date.now()}`,
                  sender: "assistant",
                  timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
                  content: "这次报告问答暂时无法完成，请稍后重试。",
                  type: "text"
                }
              ]
            }
          : item
      )));
      setSendingMessageSessionId((current) => current === session.id ? "" : current);
    }
  };

  useEffect(() => {
    sessions.forEach((session) => {
      const pending = session.reportBinding?.pendingTurn;
      if (!pending || pendingTurnControllersRef.current.has(pending.turnId)) return;
      void continuePublishedReportTurn(
        session.id,
        pending.turnId,
        Boolean(pending.resumeAttempted)
      );
    });
  }, [continuePublishedReportTurn, sessions]);

  useEffect(() => () => {
    pendingTurnControllersRef.current.forEach((controller) => controller.abort());
    pendingTurnControllersRef.current.clear();
  }, []);

  // Session switching
  const handleSelectSession = (id: string) => {
    setActiveSessionId(id);
    setActiveDrawer(null);
    navigate(`/investigation/${encodeURIComponent(id)}`);
  };

  // Create new blank investigation session
  const handleNewInvestigation = () => {
    const newId = `session-new-${Date.now()}`;
    const newSession: InvestigationSession = {
      id: newId,
      title: "新调查需求",
      status: "配置中",
      updatedAt: "刚刚",
      draft: {
        taskName: "自定义巡查任务",
        taskType: "平台话题采集",
        subject: "待确定",
        platforms: [],
        keywords: [],
        matchedRuleSet: "赌博博彩风险规则集",
        ruleSetDescription: "分析采集内容中是否存在相关违规风险要素。",
        status: "配置中",
        confirmed: false
      },
      executionPhase: "idle",
      executionProgress: 0,
      messages: []
    };

    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newId);
    setActiveDrawer(null);
    navigate(`/investigation/${encodeURIComponent(newId)}`);
  };

  // Rename session
  const handleRenameSession = (id: string, newTitle: string) => {
    setSessions((prev) =>
      prev.map((s) => (s.id === id ? { ...s, title: newTitle } : s))
    );
  };

  // Delete session
  const handleDeleteSession = (id: string) => {
    const filtered = sessions.filter((session) => session.id !== id);
    setSessions(filtered);

    if (activeSessionId === id) {
      const nextSession = filtered[0];
      if (nextSession) {
        setActiveSessionId(nextSession.id);
        navigate(`/investigation/${encodeURIComponent(nextSession.id)}`);
      } else {
        navigate("/investigation");
      }
    }
  };

  // Update draft keywords
  const handleUpdateDraftKeywords = (keywords: string[]) => {
    updateActiveSession((session) => ({
      ...session,
      draft: {
        ...session.draft,
        keywords
      }
    }));
  };

  // Update draft platforms
  const handleUpdateDraftPlatforms = (platforms: PlatformCode[]) => {
    updateActiveSession((session) => ({
      ...session,
      draft: {
        ...session.draft,
        platforms
      }
    }));
  };

  const handleGenerateTaskConfig = (proposalMessageId: string) => {
    updateActiveSession((session) => {
      if (session.draft.platforms.length === 0) return session;

      const timestamp = Date.now();
      const nowTime = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      const platformNames = session.draft.platforms.map(
        (code) => platformOptionsList.find((platform) => platform.code === code)?.label || code
      );
      const platformSentence = platformNames.length <= 2
        ? platformNames.join("和")
        : `${platformNames.slice(0, -1).join("、")}和${platformNames[platformNames.length - 1]}`;

      return {
        ...session,
        status: "等待确认",
        draft: {
          ...session.draft,
          status: "等待确认",
          confirmed: false
        },
        messages: [
          ...session.messages.map((message) => (
            message.id === proposalMessageId && message.proposalData
              ? {
                  ...message,
                  proposalData: {
                    ...message.proposalData,
                    platformsSelected: session.draft.platforms,
                    platformsConfirmed: true
                  }
                }
              : message
          )),
          {
            id: `msg-u-platforms-${timestamp}`,
            sender: "user",
            timestamp: nowTime,
            content: `采集${platformSentence}。`
          },
          {
            id: `msg-a-confirm-${timestamp}`,
            sender: "assistant",
            timestamp: nowTime,
            content: "已根据你的调查目标和平台选择生成任务配置，请确认。"
          },
          {
            id: `msg-task-confirm-${timestamp}`,
            sender: "assistant",
            timestamp: nowTime,
            type: "task_confirmation"
          }
        ]
      };
    });
  };

  // Start 4-Agent Execution
  const handleStartAgentExecution = () => {
    updateActiveSession((session) => {
      const hasExecutionMessage = session.messages.some((message) => message.type === "agent_collaboration");
      return {
        ...session,
        status: "研判中",
        draft: {
          ...session.draft,
          status: "已创建",
          confirmed: true
        },
        executionPhase: "collection_waking",
        messages: hasExecutionMessage
          ? session.messages
          : [
              ...session.messages,
              {
                id: `msg-agent-${Date.now()}`,
                sender: "assistant",
                timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
                type: "agent_collaboration"
              }
            ]
      };
    });
  };

  const attachPublishedReport = async (sessionId: string, taskId: string) => {
    if (loadingPublishedReportsRef.current.has(sessionId)) return;
    loadingPublishedReportsRef.current.add(sessionId);

    try {
      const versions = await fetchPublishedReportVersions(taskId);
      if (!versions.latest_report_version_id) {
        throw new Error("当前调查任务尚无已发布报告");
      }
      const report = await fetchPublishedReportVersion(versions.latest_report_version_id);
      if (report.task_id !== taskId) {
        throw new Error("发布报告与调查任务不匹配");
      }

      setSessions((current) => current.map((session) => {
        if (session.id !== sessionId || session.reportBinding) return session;
        return {
          ...session,
          title: report.presentation.title,
          status: "报告已生成",
          updatedAt: "刚刚",
          draft: {
            ...session.draft,
            status: "报告已生成",
            confirmed: true
          },
          executionPhase: "completed",
          executionProgress: 100,
          messages: [
            ...session.messages,
            {
              id: `msg-report-${report.report_version_id}`,
              sender: "assistant",
              timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
              type: "report_card",
              reportData: buildPublishedReportSummary(report)
            }
          ],
          reportBinding: {
            reportVersionId: report.report_version_id,
            reportId: report.report_id,
            taskId: report.task_id,
            versionNumber: report.version_number,
            publishedAt: report.published_at
          }
        };
      }));
    } catch (error) {
      console.error("Failed to attach published report to investigation", error);
      setSessions((current) => current.map((session) => (
        session.id === sessionId
          ? {
              ...session,
              messages: [
                ...session.messages,
                {
                  id: `msg-report-error-${Date.now()}`,
                  sender: "assistant",
                  timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
                  content: "调查任务已经完成，但发布报告暂时无法载入。请稍后重新打开本会话。"
                }
              ]
            }
          : session
      )));
    } finally {
      loadingPublishedReportsRef.current.delete(sessionId);
    }
  };

  // Handle phase changes during animation
  const handlePhaseChange = (phase: AgentExecutionPhase) => {
    if (
      phase === "completed"
      && activeSession.status !== "报告已生成"
      && activeSession.draft.reportSourceTaskId
    ) {
      const sessionId = activeSession.id;
      const taskId = activeSession.draft.reportSourceTaskId;
      updateActiveSession((session) => ({
        ...session,
        executionPhase: "completed",
        executionProgress: 100
      }));
      void attachPublishedReport(sessionId, taskId);
      return;
    }

    updateActiveSession((session) => {
      if (phase === "completed" && session.status !== "报告已生成") {
        return {
          ...session,
          status: "报告已生成",
          executionPhase: "completed",
          messages: [
            ...session.messages,
            {
              id: `msg-report-${Date.now()}`,
              sender: "assistant",
              timestamp: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
              type: "report_card",
              reportData: buildReportSummary(session)
            }
          ]
        };
      }
      return {
        ...session,
        executionPhase: phase
      };
    });
  };

  // User sends text in input box
  const handleSendMessage = (text: string) => {
    const nowTime = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

    if (activeSession.reportBinding) {
      if (sendingMessageSessionId) return;
      const clientMessageId = createClientMessageId(activeSession.id);
      const session = activeSession;
      setSendingMessageSessionId(session.id);
      setSessions((current) => current.map((item) => (
        item.id === session.id
          ? {
              ...item,
              updatedAt: "刚刚",
              messages: [
                ...item.messages,
                {
                  id: `msg-question-${clientMessageId}`,
                  sender: "user",
                  timestamp: nowTime,
                  content: text
                }
              ]
            }
          : item
      )));
      void sendPublishedReportQuestion(session, text, clientMessageId);
      return;
    }

    // Check if in blank state
    if (activeSession.messages.length === 0) {
      if (
        !text.includes("世界杯")
        && (text.includes("博彩赌博") || text.includes("赌博类") || (text.includes("抖音") && text.includes("博彩")))
      ) {
        updateActiveSession((session) => ({
          ...session,
          title: "抖音平台博彩赌博类内容风险调查",
          draft: {
            ...session.draft,
            taskName: "博彩赌博类帖子分析任务",
            subject: "抖音平台博彩赌博类内容风险",
            platforms: ["dy"],
            matchedRuleSet: "赌博博彩风险规则集",
            analysisPlanName: "博彩引流综合研判方案",
            ruleSetDescription: "结合正文、OCR、语音转写、画面与评论证据，识别博彩招募、盘口推广、资金结算和外部导流风险。",
            scopeDescription: "围绕抖音平台博彩赌博类内容开展采集、证据分析与风险研判。",
            reportSourceTaskId: GAMBLING_REPORT_DEMO_TASK_ID,
            status: "配置中",
            confirmed: false,
            keywords: [
              "跑分",
              "BC 上分 下分",
              "首充 彩金 返水",
              "导师 带单 计划",
              "盘口 赔率 下注",
              "棋牌 真人 娱乐城",
              "代理 招商"
            ]
          },
          messages: [
            { id: `msg-u-${Date.now()}`, sender: "user", timestamp: nowTime, content: text },
            {
              id: `msg-a-${Date.now()}`,
              sender: "assistant",
              timestamp: nowTime,
              type: "task_proposal",
              content: "已识别为‘抖音平台博彩赌博类内容风险调查任务’。系统已根据调查主题生成本次搜索词，并匹配‘博彩引流综合研判方案’。抖音平台已选中，你可以继续调整后生成任务配置。",
              proposalData: {
                taskName: "博彩赌博类帖子分析任务",
                taskType: "平台话题采集",
                subject: "抖音平台博彩赌博类内容风险",
                matchedRuleSet: "赌博博彩风险规则集",
                ruleSetDescription: "结合正文、OCR、语音转写、画面与评论证据，识别博彩招募、盘口推广、资金结算和外部导流风险。",
                keywordsNotice: "本次使用面向博彩招募、盘口和资金导流场景的专题搜索词。",
                platformsSelected: ["dy"],
                platformsConfirmed: false,
                interactionMode: "platform-selection"
              }
            }
          ]
        }));
        return;
      }

      if (text.includes("世界杯") || text.includes("博彩") || text.includes("赌球")) {
        // Trigger World Cup flow in this session
        updateActiveSession((s) => ({
          ...s,
          title: "世界杯博彩专题调查",
          draft: {
            ...s.draft,
            taskName: "世界杯博彩专题采集与分析",
            subject: "世界杯博彩",
            platforms: [],
            matchedRuleSet: "赌博博彩风险规则集",
            ruleSetDescription: "用于抓取后的博彩黑话、引流行为和风险线索识别，不会扩大本次采集范围。",
            analysisPlanName: "博彩引流综合研判方案",
            scopeDescription: "采集范围使用系统默认值；如需调整时间范围或采集数量，可进入高级配置。",
            status: "配置中",
            confirmed: false,
            keywords: ["世界杯赌球", "世界杯博彩", "世界杯下注", "世界杯外围", "世界杯足球盘", "世界杯比分盘", "世界杯滚球"]
          },
          messages: [
            { id: `msg-u-${Date.now()}`, sender: "user", timestamp: nowTime, content: text },
            {
              id: `msg-a-${Date.now()}`,
              sender: "assistant",
              timestamp: nowTime,
              type: "task_proposal",
              content: "已识别为‘世界杯博彩专题采集与分析任务’。现有博彩词库主要用于通用博彩黑话识别，缺少世界杯赛事场景的精准检索词，因此为本次任务生成了一组专题搜索词。\n\n这些词只用于本次采集，不会写入现有词库；抓取后推荐使用‘博彩引流综合研判方案’进行分析。请选择采集平台。",
              proposalData: {
                taskName: "世界杯博彩专题采集与分析",
                taskType: "平台话题采集",
                subject: "世界杯博彩",
                matchedRuleSet: "赌博博彩风险规则集",
                ruleSetDescription: "用于抓取后的博彩黑话、引流行为和风险线索识别，不会扩大本次采集范围。",
                platformsSelected: [],
                platformsConfirmed: false,
                interactionMode: "platform-selection"
              }
            }
          ]
        }));
        return;
      }

      if (text.includes("维汉") || text.includes("通婚") || text.includes("民族")) {
        // Trigger the ethnic-relations demo flow. No backend request is issued.
        updateActiveSession((s) => ({
          ...s,
          title: "维汉民族关系专项调查",
          draft: {
            ...s.draft,
            taskName: "维汉民族关系专项调查",
            subject: "维汉民族关系",
            platforms: [],
            matchedRuleSet: "民族意识形态风险规则集",
            analysisPlanName: "维汉民族关系专题研判方案",
            ruleSetDescription: "结合原帖、维吾尔语译文与评论上下文，识别民族刻板印象、侮辱歧视、排斥通婚和煽动对立等风险表达。",
            scopeDescription: "围绕维汉婚恋、家庭互动与相关评论争议开展专题采集。",
            status: "配置中",
            confirmed: false,
            keywords: ["维汉通婚", "维汉夫妻", "新疆姑娘汉族小伙", "跨民族婚姻", "民族婚恋", "维汉家庭", "通婚争议", "民族刻板印象"]
          },
          messages: [
            { id: `msg-u-${Date.now()}`, sender: "user", timestamp: nowTime, content: text },
            {
              id: `msg-a-${Date.now()}`,
              sender: "assistant",
              timestamp: nowTime,
              type: "task_proposal",
              content: "已识别为‘维汉民族关系专项调查任务’。现有民族宗教涉敏词库主要用于通用民族关系风险识别，缺少维汉婚恋、家庭互动与评论争议场景的精准检索词，因此为本次任务生成了一组专题搜索词。\n\n这些词仅用于本次采集；抓取后推荐使用‘维汉民族关系专题研判方案’。请选择采集平台。",
              proposalData: {
                taskName: "维汉民族关系专项调查",
                taskType: "平台话题采集",
                subject: "维汉民族关系",
                matchedRuleSet: "民族意识形态风险规则集",
                ruleSetDescription: "结合原帖、维吾尔语译文与评论上下文，识别民族刻板印象、侮辱歧视、排斥通婚和煽动对立等风险表达。",
                keywordsNotice: "现有民族宗教涉敏词库缺少维汉婚恋与家庭互动场景的精准检索词，建议本次使用 8 个专题搜索词。",
                platformsSelected: [],
                platformsConfirmed: false,
                interactionMode: "platform-selection"
              }
            }
          ]
        }));
        return;
      }

      // Default response
      updateActiveSession((s) => ({
        ...s,
        messages: [
          { id: `msg-u-${Date.now()}`, sender: "user", timestamp: nowTime, content: text },
          {
            id: `msg-a-${Date.now()}`,
            sender: "assistant",
            timestamp: nowTime,
            content: "已收到您的调查需求。您可以点击上方推荐场景示例（如「抖音博彩赌博内容风险调查」或「维汉通婚讨论调查」），快速开启完整的 4-Agent 协同研判与数据穿透体验。"
          }
        ]
      }));
      return;
    }

    // Default response for continuing QA after report or in existing session
    let responseContent = "已收到您的进一步追问。系统已完成数据检索与证据固化。";
    let groundingItems: GroundingItem[] | undefined;
    let groundingMode: "compact" | "expanded" | undefined;
    if (isEthnicRelationsSession(activeSession) && activeSession.status === "报告已生成") {
      const reply = buildEthnicRelationsReply(text);
      responseContent = reply.content;
      groundingItems = reply.groundingItems;
      groundingMode = reply.groundingMode;
    } else if (text.includes("重点作者") || text.includes("账号")) {
      responseContent = isEthnicRelationsSession(activeSession)
        ? "四个重点对象下共有 7,450 个去重互动账号，其中 131 个账号跨至少两个对象评论。现有风险评论账号均未跨对象出现，因此这些交叉互动更符合共同受众特征，暂不认定为关联网络。"
        : "这 8 个重点作者候选主要集中在抖音与小红书平台，其中 3 个账号存在跨平台同名引流现象。建议点击‘查看重点作者’或发起进一步主页穿透。";
    }

    updateActiveSession((s) => ({
      ...s,
      messages: [
        ...s.messages,
        { id: `msg-u-${Date.now()}`, sender: "user", timestamp: nowTime, content: text },
        {
          id: `msg-a-${Date.now()}`,
          sender: "assistant",
          timestamp: nowTime,
          content: responseContent,
          type: groundingItems?.length ? "grounded_answer" : "text",
          groundingItems,
          groundingMode
        }
      ]
    }));
  };

  return (
    <div className="inv-workspace-root">
      {/* Column 1: Left Investigation Sidebar */}
      <InvestigationSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={handleSelectSession}
        onNewInvestigation={handleNewInvestigation}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed((prev) => !prev)}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        activeSubView={activeSubView}
        onSelectSubView={(subView) => {
          if (subView === "knowledge-center") {
            navigate("/rule-assistant");
            return;
          }
          setActiveSubView(subView);
        }}
      />

      {/* Column 2: Center Content (Either Investigation Chat or Embedded SubView) */}
      {activeSubView ? (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", background: "#f8fafc", minWidth: 0 }}>
          {/* SubView Top Header Bar */}
          <div style={{ height: "52px", background: "#ffffff", borderBottom: "1px solid #e2e8f0", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 20px", flexShrink: 0, zIndex: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              {isSidebarCollapsed ? (
                <button
                  type="button"
                  className="inv-expand-toggle"
                  onClick={() => setIsSidebarCollapsed(false)}
                  title="展开侧栏"
                >
                  <Menu size={16} />
                </button>
              ) : null}
              <button
                type="button"
                className="inv-head-btn"
                onClick={() => setActiveSubView(null)}
                style={{ background: "#f1f5f9", color: "#334155" }}
              >
                <ArrowLeft size={14} />
                <span>返回调查对话</span>
              </button>
              <span style={{ color: "#cbd5e1", fontSize: "14px" }}>|</span>
              <span style={{ fontSize: "15px", fontWeight: "800", color: "#0f172a" }}>
                {activeSubView === "users" && "重点用户管理"}
                {activeSubView === "crawler-accounts" && "采集账号管理"}
                {activeSubView === "knowledge-center" && "知识库资源中心"}
              </span>
            </div>
          </div>

          {/* SubView Content Container */}
          <div ref={subViewScrollRef} style={{ flex: 1, overflowY: "auto" }}>
            {activeSubView === "users" && <FocusUsersPage />}
            {activeSubView === "crawler-accounts" && <CrawlerAccountsPage />}
          </div>
        </div>
      ) : (
        <InvestigationCenterArea
          session={activeSession}
          isSendingMessage={sendingMessageSessionId === activeSession.id}
          sendingMessageStage={activeSession.reportBinding?.pendingTurn?.stage}
          isSidebarCollapsed={isSidebarCollapsed}
          onToggleSidebar={() => setIsSidebarCollapsed(false)}
          onUpdateDraftKeywords={handleUpdateDraftKeywords}
          onUpdateDraftPlatforms={handleUpdateDraftPlatforms}
          onGenerateTaskConfig={handleGenerateTaskConfig}
          onStartAgentExecution={handleStartAgentExecution}
          onPhaseChange={handlePhaseChange}
          onSendMessage={handleSendMessage}
          onOpenDrawer={(type) => {
            if (type === "report" && isEthnicRelationsSession(activeSession)) {
              navigate(`/investigation/${encodeURIComponent(activeSession.id)}/report`);
              return;
            }
            setActiveDrawer(type);
          }}
          onOpenReportSupport={(target) => {
            const search = new URLSearchParams();
            if (target.evidenceId) search.set("evidence", target.evidenceId);
            if (target.sectionId) search.set("section", target.sectionId);
            const query = search.toString();
            navigate(
              `/investigation/${encodeURIComponent(activeSession.id)}/report${query ? `?${query}` : ""}`
            );
          }}
          onExamplePromptSelect={handleSendMessage}
        />
      )}

      {/* Column 3: Right Context Drawer (Overlay/Sliding) */}
      <InvestigationContextDrawer
        isOpen={activeDrawer !== null}
        type={activeDrawer}
        onClose={() => setActiveDrawer(null)}
        draft={activeSession.draft}
        report={activeSession.messages.find((m) => m.type === "report_card")?.reportData}
        evidenceItems={activeSession.messages.find((m) => m.type === "evidence_list")?.evidenceItems}
        activeRuleSet={activeRuleSet}
      />
    </div>
  );
}
