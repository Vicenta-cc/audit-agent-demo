import type { ConfigCenterSnapshot, ResearchPolicy, RiskLexicon } from "../types/configCenter";

export const mockPolicies: ResearchPolicy[] = [
  {
    id: "policy_gambling",
    name: "赌博博彩研判方案",
    description: "识别投注平台、盘口赔率、上分提现、代理推广和群聊导流风险。",
    category: "赌博博彩",
    status: "published",
    scenarioTags: ["平台内容", "直播", "本地视频", "重点用户"],
    lexiconIds: ["gambling"],
    lexiconNames: ["赌博黑话词库"],
    contentScopes: ["标题正文", "评论弹幕", "图片视频", "语音内容"],
    recognitionCapabilities: ["文本语义", "OCR", "视觉识别", "ASR", "评论聚集"],
    references: [
      { id: "job-0821", name: "博彩搜索词巡检", status: "运行中" },
      { id: "job-0928", name: "直播间引流监控", status: "运行中" },
      { id: "job-1030", name: "重点账号复查", status: "已暂停" },
      { id: "job-1136", name: "本地视频抽检", status: "已完成" }
    ],
    version: { version: "v1.0", updatedAt: "2026-07-09T17:06:00", updatedBy: "张警官" },
    createdAt: "2026-05-20T10:18:00",
    updatedAt: "2026-07-09T17:06:00",
    updatedBy: "张警官"
  },
  {
    id: "policy_fraud",
    name: "涉诈研判方案",
    description: "覆盖刷单返利、虚假投资、认证金、仿冒客服与私域收割。",
    category: "诈骗",
    status: "published",
    scenarioTags: ["平台内容", "重点用户", "评论"],
    lexiconIds: ["fraud"],
    lexiconNames: ["涉诈话术词库"],
    contentScopes: ["标题正文", "评论弹幕", "图片视频", "账号资料"],
    recognitionCapabilities: ["文本语义", "OCR", "视觉识别", "评论聚集"],
    references: [
      { id: "job-2101", name: "返利话术巡检", status: "运行中" },
      { id: "job-2102", name: "投资理财线索池", status: "运行中" }
    ],
    version: { version: "v1.3", updatedAt: "2026-07-09T16:30:00", updatedBy: "陈警官" },
    createdAt: "2026-05-28T11:40:00",
    updatedAt: "2026-07-09T16:30:00",
    updatedBy: "陈警官"
  },
  {
    id: "policy_prohibited",
    name: "违规引流研判方案",
    description: "识别站外导流、私域口令、二维码、联系方式和评论区聚集。",
    category: "违规引流",
    status: "reviewing",
    scenarioTags: ["平台内容", "评论", "重点用户"],
    lexiconIds: ["prohibited"],
    lexiconNames: ["违规引流知识包"],
    contentScopes: ["标题正文", "评论弹幕", "图片视频", "账号资料"],
    recognitionCapabilities: ["文本语义", "OCR", "视觉识别", "评论聚集"],
    references: [{ id: "job-3108", name: "私域导流监控", status: "运行中" }],
    version: { version: "draft", updatedAt: "2026-07-09T15:12:00", updatedBy: "李警官" },
    createdAt: "2026-06-01T09:14:00",
    updatedAt: "2026-07-09T15:12:00",
    updatedBy: "李警官"
  },
  {
    id: "policy_soft",
    name: "软色情研判方案",
    description: "识别擦边、性暗示、低俗交易导流和评论区求资源。",
    category: "软色情",
    status: "published",
    scenarioTags: ["平台内容", "直播", "本地视频"],
    lexiconIds: ["soft"],
    lexiconNames: ["软色情词库"],
    contentScopes: ["标题正文", "评论弹幕", "图片视频", "语音内容"],
    recognitionCapabilities: ["文本语义", "ASR", "视觉识别", "评论聚集"],
    references: [
      { id: "job-4050", name: "擦边内容巡检", status: "运行中" },
      { id: "job-4051", name: "短视频低俗识别", status: "已完成" }
    ],
    version: { version: "v2.1", updatedAt: "2026-07-09T14:45:00", updatedBy: "王警官" },
    createdAt: "2026-04-22T14:20:00",
    updatedAt: "2026-07-09T14:45:00",
    updatedBy: "王警官"
  },
  {
    id: "policy_terror",
    name: "暴恐研判方案",
    description: "识别暴恐宣传、极端主义、武器展示、组织招募和行动号召。",
    category: "暴恐",
    status: "published",
    scenarioTags: ["平台内容", "本地视频", "重点用户"],
    lexiconIds: ["terror"],
    lexiconNames: ["暴恐风险知识包"],
    contentScopes: ["标题正文", "图片视频", "语音内容", "账号资料"],
    recognitionCapabilities: ["文本语义", "OCR", "视觉识别", "ASR"],
    references: [{ id: "job-5012", name: "极端内容巡检", status: "运行中" }],
    version: { version: "v1.2", updatedAt: "2026-07-08T19:22:00", updatedBy: "张警官" },
    createdAt: "2026-04-26T15:50:00",
    updatedAt: "2026-07-08T19:22:00",
    updatedBy: "张警官"
  },
  {
    id: "policy_drug",
    name: "涉毒研判方案",
    description: "识别涉毒交易、吸贩毒暗号、同城邀约、违禁药物导流和接头互动。",
    category: "涉毒",
    status: "published",
    scenarioTags: ["平台内容", "重点用户"],
    lexiconIds: ["drug"],
    lexiconNames: ["涉毒风险知识包"],
    contentScopes: ["标题正文", "评论弹幕", "图片视频"],
    recognitionCapabilities: ["文本语义", "OCR", "视觉识别"],
    references: [{ id: "job-6102", name: "同城交易线索", status: "运行中" }],
    version: { version: "v1.0", updatedAt: "2026-07-08T18:03:00", updatedBy: "赵警官" },
    createdAt: "2026-05-10T12:15:00",
    updatedAt: "2026-07-08T18:03:00",
    updatedBy: "赵警官"
  },
  {
    id: "policy_hate",
    name: "仇恨歧视研判方案",
    description: "识别针对群体身份的侮辱、排斥、煽动攻击和组织性网暴。",
    category: "仇恨歧视",
    status: "published",
    scenarioTags: ["平台内容", "评论", "重点用户"],
    lexiconIds: ["hate", "minority"],
    lexiconNames: ["民族宗教仇恨风险知识包", "民族语言词库"],
    contentScopes: ["标题正文", "评论弹幕", "账号资料"],
    recognitionCapabilities: ["文本语义", "评论聚集", "OCR"],
    references: [],
    version: { version: "v1.0", updatedAt: "2026-07-07T16:45:00", updatedBy: "陈警官" },
    createdAt: "2026-05-13T16:10:00",
    updatedAt: "2026-07-07T16:45:00",
    updatedBy: "陈警官"
  },
  {
    id: "policy_custom_live",
    name: "直播互动综合方案",
    description: "面向直播间口播、弹幕互动和站外引导的综合检测策略。",
    category: "综合",
    status: "draft",
    scenarioTags: ["直播", "评论"],
    lexiconIds: ["gambling", "fraud", "prohibited"],
    lexiconNames: ["赌博黑话词库", "涉诈话术词库", "违规引流知识包"],
    contentScopes: ["评论弹幕", "语音内容", "图片视频"],
    recognitionCapabilities: ["ASR", "文本语义", "评论聚集"],
    references: [],
    version: { version: "draft", updatedAt: "2026-07-06T11:36:00", updatedBy: "张警官" },
    createdAt: "2026-07-05T10:00:00",
    updatedAt: "2026-07-06T11:36:00",
    updatedBy: "张警官"
  },
  {
    id: "policy_minor_review",
    name: "少数语种风险初筛方案",
    description: "用于少数语种内容的文本、字幕和评论线索初筛。",
    category: "其他",
    status: "disabled",
    scenarioTags: ["平台内容", "本地视频"],
    lexiconIds: ["minority"],
    lexiconNames: ["民族语言词库"],
    contentScopes: ["标题正文", "评论弹幕", "图片视频"],
    recognitionCapabilities: ["文本语义", "OCR", "ASR"],
    references: [],
    version: { version: "v0.8", updatedAt: "2026-07-04T10:25:00", updatedBy: "系统" },
    createdAt: "2026-05-05T09:30:00",
    updatedAt: "2026-07-04T10:25:00",
    updatedBy: "系统"
  }
];

export const mockLexicons: RiskLexicon[] = [
  {
    id: "gambling",
    name: "赌博黑话词库",
    category: "赌博博彩",
    entryCount: 1268,
    platformSearchWordCount: 42,
    platformTagCount: 18,
    keywords: ["上分", "盘口", "回血", "百家", "包赔"],
    platformSearchWords: ["百家乐", "下注", "包赔"],
    platformTags: ["博彩", "盘口", "资金盘"],
    references: [],
    updatedAt: "2026-07-09T14:32:00",
    updatedBy: "张警官"
  },
  {
    id: "fraud",
    name: "涉诈话术词库",
    category: "诈骗",
    entryCount: 842,
    platformSearchWordCount: 31,
    platformTagCount: 12,
    keywords: ["刷流水", "认证金", "返利", "稳赚", "带单"],
    platformSearchWords: ["返利", "兼职", "认证金"],
    platformTags: ["副业", "理财", "返现"],
    references: [],
    updatedAt: "2026-07-09T11:28:00",
    updatedBy: "陈警官"
  },
  {
    id: "prohibited",
    name: "违规引流知识包",
    category: "违规引流",
    entryCount: 536,
    platformSearchWordCount: 28,
    platformTagCount: 16,
    keywords: ["私信", "看主页", "加群", "留号", "谐音联系"],
    platformSearchWords: ["加群了解", "私信领取", "扫码进群"],
    platformTags: ["引流", "私域", "联系方式"],
    references: [],
    updatedAt: "2026-07-08T18:26:00",
    updatedBy: "李警官"
  },
  {
    id: "soft",
    name: "软色情词库",
    category: "软色情",
    entryCount: 621,
    platformSearchWordCount: 24,
    platformTagCount: 14,
    keywords: ["擦边", "私拍", "福利", "写真", "约拍"],
    platformSearchWords: ["私拍", "写真", "福利"],
    platformTags: ["低俗", "擦边", "交易"],
    references: [],
    updatedAt: "2026-07-08T16:08:00",
    updatedBy: "王警官"
  },
  {
    id: "terror",
    name: "暴恐风险知识包",
    category: "暴恐",
    entryCount: 412,
    platformSearchWordCount: 15,
    platformTagCount: 8,
    keywords: ["武器", "爆炸", "行动号召", "极端标识"],
    platformSearchWords: ["武器教程", "极端组织"],
    platformTags: ["暴力", "极端", "危险物品"],
    references: [],
    updatedAt: "2026-07-07T20:42:00",
    updatedBy: "张警官"
  },
  {
    id: "drug",
    name: "涉毒风险知识包",
    category: "涉毒",
    entryCount: 368,
    platformSearchWordCount: 19,
    platformTagCount: 7,
    keywords: ["同城", "邮寄", "货", "飞行", "接头"],
    platformSearchWords: ["同城货", "邮寄到付"],
    platformTags: ["违禁", "交易", "同城"],
    references: [],
    updatedAt: "2026-07-07T18:12:00",
    updatedBy: "赵警官"
  },
  {
    id: "hate",
    name: "民族宗教仇恨风险知识包",
    category: "仇恨歧视",
    entryCount: 391,
    platformSearchWordCount: 20,
    platformTagCount: 9,
    keywords: ["群体攻击", "驱逐", "排斥", "煽动"],
    platformSearchWords: ["群体攻击", "地域攻击"],
    platformTags: ["仇恨", "歧视", "煽动"],
    references: [],
    updatedAt: "2026-07-06T15:30:00",
    updatedBy: "陈警官"
  }
];

export function buildMockConfigSnapshot(): ConfigCenterSnapshot {
  const policies = mockPolicies.map((policy) => ({ ...policy, references: [...policy.references] }));
  const lexicons = mockLexicons.map((lexicon) => ({
    ...lexicon,
    references: policies
      .filter((policy) => policy.lexiconIds.includes(lexicon.id))
      .map((policy) => ({ id: policy.id, name: policy.name, status: policy.status }))
  }));

  return {
    policies,
    lexicons,
    policySummary: {
      total: policies.length,
      published: policies.filter((policy) => policy.status === "published").length,
      draft: policies.filter((policy) => policy.status === "draft").length,
      referencedTaskCount: policies.reduce((sum, policy) => sum + policy.references.length, 0)
    },
    lexiconSummary: {
      total: lexicons.length,
      entryCount: lexicons.reduce((sum, lexicon) => sum + lexicon.entryCount, 0),
      policyReferenceCount: lexicons.reduce((sum, lexicon) => sum + lexicon.references.length, 0),
      latestUpdatedAt: lexicons
        .map((lexicon) => lexicon.updatedAt)
        .sort((a, b) => Date.parse(b) - Date.parse(a))[0] || ""
    }
  };
}
