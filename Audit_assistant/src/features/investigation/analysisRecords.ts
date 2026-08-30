export type AnalysisRisk = "safe" | "low" | "medium" | "high";
export type AnalysisScenario = "default" | "ethnic-relations";
export type AnalysisEvidenceType = "text" | "ocr" | "asr" | "comment" | "vision";

export interface AnalysisKeyEvidence {
  id: string;
  type: AnalysisEvidenceType;
  content: string;
  translation?: string;
  explanation: string;
}

export interface AnalysisRecord {
  catalogVersion: number;
  itemNumber: number;
  risk: AnalysisRisk;
  riskLabel: string;
  summary: string;
  conclusion: string;
  contentTitle: string;
  author: string;
  platform: string;
  analyzedAt: string;
  evidenceCounts: Record<AnalysisEvidenceType, number>;
  keyEvidence: AnalysisKeyEvidence[];
  taskId: string;
  outputId: string;
}

export const analysisRiskOrder: Record<AnalysisRisk, number> = {
  high: 4,
  medium: 3,
  low: 2,
  safe: 1
};

export const analysisEvidenceTypes: Array<{
  type: AnalysisEvidenceType;
  label: string;
}> = [
  { type: "text", label: "文本" },
  { type: "ocr", label: "画面文字" },
  { type: "asr", label: "音频" },
  { type: "comment", label: "评论" },
  { type: "vision", label: "视觉" }
];

const ANALYSIS_RECORD_CATALOG_VERSION = 4;

const DEFAULT_ANALYSIS_RECORD_TEMPLATES: Array<Omit<AnalysisRecord, "catalogVersion" | "itemNumber" | "analyzedAt">> = [
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "检测到博彩资金结算与代理招募特征",
    conclusion: "正文黑产标签与评论区求带互动共同指向博彩资金结算及代理招募，建议进入人工复核队列。",
    contentTitle: "跑分与博彩料招募帖",
    author: "王者工作室",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 0, asr: 0, comment: 1, vision: 0 },
    keyEvidence: [
      {
        id: "settlement-text",
        type: "text",
        content: "#跑分 #二道料 #bc料",
        explanation: "连续命中博彩信息交易和非法资金结算黑话。"
      },
      {
        id: "settlement-comment",
        type: "comment",
        content: "评论区多人留言求带，并询问卡、码和车队资源。",
        explanation: "互动呈现博彩代理招募和资金通道对接特征。"
      }
    ],
    taskId: "1e46793a00f8",
    outputId: "235"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "发现博彩资金结算通道招募特征",
    conclusion: "画面文案招募博彩资源与 USDT 结算通道，评论区进一步询问私域入口，建议重点核验关联账号。",
    contentTitle: "招募博彩资金结算通道合作",
    author: "81350805175",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 1, asr: 0, comment: 1, vision: 0 },
    keyEvidence: [
      {
        id: "channel-ocr",
        type: "ocr",
        content: "BC料 / U接U / 回U下浮 / 平台下分。",
        explanation: "画面文字明确指向博彩资源与 USDT 资金结算。"
      },
      {
        id: "channel-comment",
        type: "comment",
        content: "评论集中询问飞机号、公群和合作平台。",
        explanation: "评论互动进一步形成私域导流线索。"
      }
    ],
    taskId: "1e46793a00f8",
    outputId: "234"
  },
  {
    risk: "low",
    riskLabel: "低风险",
    summary: "存在低强度异常线索",
    conclusion: "正文与画面命中跑分、BC 料等风险词，但整体语境为反诈警示，建议人工确认豁免边界。",
    contentTitle: "揭秘跑分与BC料骗局警示",
    author: "大暮-刑案陪跑",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 1, asr: 0, comment: 0, vision: 0 },
    keyEvidence: [
      {
        id: "review-text",
        type: "text",
        content: "跑分、BC料内幕都是局中局，参与非法资金结算将承担刑事责任。",
        explanation: "风险词命中明确，但上下文具有反诈劝诫特征。"
      },
      {
        id: "review-ocr",
        type: "ocr",
        content: "画面识别：警惕跑分陷阱，远离BC料骗局。",
        explanation: "画面文字支持普法警示语境，需确认是否适用豁免。"
      }
    ],
    taskId: "1e46793a00f8",
    outputId: "228"
  },
  {
    risk: "safe",
    riskLabel: "无风险",
    summary: "内容属于正常赛事讨论",
    conclusion: "视频为母子观看比赛的生活记录，评论区均为表情或正向互动，未发现博彩推广和异常导流。",
    contentTitle: "母子观看比赛温馨互动记录",
    author: "麦热依姆古丽",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "safe-vision",
        type: "vision",
        content: "画面记录母子在家观看比赛并进行日常互动。",
        explanation: "属于正常生活及赛事观看场景，未识别到风险元素。"
      },
      {
        id: "safe-comment",
        type: "comment",
        content: "评论区为表情符号和正向评价。",
        explanation: "未发现下注、联系方式或异常互动聚集。"
      }
    ],
    taskId: "8bc179209e1e",
    outputId: "405"
  },
  {
    risk: "medium",
    riskLabel: "中风险",
    summary: "发现疑似盘口推广特征",
    conclusion: "画面招募 BC 料车队，评论区聚集求带及询问私域账号，建议重点核验账号主页及关联内容。",
    contentTitle: "BC料车队招募引流帖",
    author: "影·",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 1, asr: 0, comment: 1, vision: 0 },
    keyEvidence: [
      {
        id: "recruit-ocr",
        type: "ocr",
        content: "BC料找实力车队！滴滴滴滴。",
        explanation: "画面文字使用博彩和代理招募黑话。"
      },
      {
        id: "recruit-comment",
        type: "comment",
        content: "等料、有纯BC吗、飞机号多少。",
        explanation: "评论互动集中求带并询问站外私域账号。"
      }
    ],
    taskId: "1e46793a00f8",
    outputId: "232"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "发现博彩资金结算与代理招募行为",
    conclusion: "正文黑产标签与评论区资源对接共同呈现赌博资金结算及代理招募行为，建议进入人工复核。",
    contentTitle: "跑分与博彩料招募帖",
    author: "王者工作室",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 0, asr: 0, comment: 1, vision: 0 },
    keyEvidence: [
      {
        id: "recruit-text",
        type: "text",
        content: "#跑分 #bc料",
        explanation: "命中博彩代理及非法资金结算黑话。"
      },
      {
        id: "recruit-comment-2",
        type: "comment",
        content: "评论区集中寻找银行卡、支付账户与车队资源。",
        explanation: "互动呈现有组织的资金通道对接特征。"
      }
    ],
    taskId: "1e46793a00f8",
    outputId: "229"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "已识别出较明确的违规特征",
    conclusion: "正文博彩黑话与评论区求带互动共同指向代理招募，建议重点核验账号主页及关联内容。",
    contentTitle: "跑分与博彩料招募帖",
    author: "王者工作室",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 0, asr: 0, comment: 1, vision: 0 },
    keyEvidence: [
      {
        id: "high-text",
        type: "text",
        content: "#跑分 #二道料 #bc料",
        explanation: "连续命中博彩信息交易和资金结算黑话。"
      },
      {
        id: "high-comment",
        type: "comment",
        content: "评论区多人留言求带参与。",
        explanation: "互动形成代理招募和资源对接线索。"
      }
    ],
    taskId: "1e46793a00f8",
    outputId: "235"
  }
];

const ETHNIC_RELATIONS_RECORD_TEMPLATES: Array<Omit<AnalysisRecord, "catalogVersion" | "itemNumber" | "analyzedAt">> = [
  {
    risk: "safe",
    riskLabel: "无风险",
    summary: "婚礼记录及评论互动整体为正常祝福",
    conclusion: "内容记录河南男子与新疆阿克苏女子的婚礼，380 条评论以祝福和婚俗讨论为主，未发现民族攻击或异常引导。",
    contentTitle: "河南小伙与新疆古丽婚礼记录",
    author: "石榴红缘",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 0, asr: 0, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "wedding-text",
        type: "text",
        content: "河南小伙子跟新疆阿克苏古丽结婚了。",
        explanation: "原帖为跨民族婚礼生活记录，未出现排斥或贬损表达。"
      },
      {
        id: "wedding-comments",
        type: "comment",
        content: "评论区以对新人的祝福、外貌评价和地域婚俗讨论为主。",
        explanation: "380 条评论未形成风险线索。"
      }
    ],
    taskId: "eafef54edea2",
    outputId: "91"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "评论区出现低俗辱骂与恶毒人身攻击",
    conclusion: "原帖为足浴店日常经营展示，风险来自评论区两条维吾尔语辱骂，包含低俗性暗示和对当事人家属的攻击。",
    contentTitle: "新疆足浴店评论区争议",
    author: "麦热依姆古丽",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 2, vision: 1 },
    keyEvidence: [
      {
        id: "footbath-comment-original",
        type: "comment",
        content: "isil. agzingdin. tuxurmigan. iring. xuma",
        explanation: "维吾尔语评论包含下流性暗示，并辱骂当事人及其家人。"
      },
      {
        id: "footbath-comment-context",
        type: "comment",
        content: "译文：评论要求当事人闭嘴，并使用恶毒女性侮辱词持续谩骂。",
        explanation: "两条独立评论共同构成高强度人身攻击证据。"
      }
    ],
    taskId: "8bc179209e1e",
    outputId: "450"
  },
  {
    risk: "medium",
    riskLabel: "中风险",
    summary: "婚介服务视频评论区出现地域攻击",
    conclusion: "原帖介绍和田婚介登记服务，评论将发布者描述为污染城市的外来者，构成针对地域身份的攻击。",
    contentTitle: "婚介视频引地域攻击评论",
    author: "麦热依姆古丽",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "regional-comment-original",
        type: "comment",
        content: "نېمىشقا كەلگەنسىز بۇ خوتەنگە شەھىرىنى بۇلغىغىلى كەلدىڭىزمۇ",
        explanation: "原文针对发布者来到和田进行质问。"
      },
      {
        id: "regional-comment-translation",
        type: "comment",
        content: "译文：你来和田干嘛？是来把这座城市搞得乌烟瘴气的吗？",
        explanation: "以外来者身份贬损当事人，构成地域攻击。"
      }
    ],
    taskId: "8bc179209e1e",
    outputId: "366"
  },
  {
    risk: "safe",
    riskLabel: "无风险",
    summary: "日常情感表达获得友好互动与鼓励",
    conclusion: "视频为女性日常情感表达，118 条评论整体友好，多为点赞和鼓励，未发现聚集性攻击或民族关系风险。",
    contentTitle: "女子出镜表达情感获网友鼓励",
    author: "我的心好累",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 0, asr: 1, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "emotion-content",
        type: "vision",
        content: "女性出镜进行日常情感表达，画面及语音未出现风险信息。",
        explanation: "原帖属于正常个人生活记录。"
      },
      {
        id: "emotion-comments",
        type: "comment",
        content: "评论以点赞、安慰与鼓励为主。",
        explanation: "118 条互动未见聚集性风险。"
      }
    ],
    taskId: "3ad102e072f6",
    outputId: "720"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "正常生活视频下出现明确性侮辱评论",
    conclusion: "原帖记录驾考通过后的家庭互动，风险由单条维吾尔语评论触发，评论对发布者及其丈夫使用明确性侮辱。",
    contentTitle: "婚介视频评论区争议言论",
    author: "麦热依姆古丽",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "license-comment-original",
        type: "comment",
        content: "يوغان نەرسە ئاپسەن دەللال خوتۇن. كەسمىگەن چوچاقنىڭ دۆلىتى.",
        explanation: "评论使用针对女性的性侮辱词，并进一步辱骂其丈夫。"
      },
      {
        id: "license-post-context",
        type: "vision",
        content: "原帖为驾照考试通过后的庆祝、买车和接送安排讨论。",
        explanation: "风险来自评论而非发布者正文。"
      }
    ],
    taskId: "8bc179209e1e",
    outputId: "456"
  },
  {
    risk: "medium",
    riskLabel: "中风险",
    summary: "生育话题评论出现民族刻板印象",
    conclusion: "评论将嫁给汉族与生育困难进行泛化关联，把个体经历扩展为民族群体判断，构成民族刻板印象。",
    contentTitle: "生育话题引民族刻板印象评论",
    author: "麦热依姆古丽",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "stereotype-comment-original",
        type: "comment",
        content: "بۇ خەنزۇلارغا تەگكەن باشقا ئاياللارنىمۇ ئاڭلاۋاتىمەن كۆرۈۋاتىمەن...",
        explanation: "评论从个体生育经历推及其他嫁给汉族的女性。"
      },
      {
        id: "stereotype-comment-translation",
        type: "comment",
        content: "译文节选：其他嫁给汉族的女人也生不出孩子，我在网上看到、听到。",
        explanation: "将民族身份与不孕泛化关联，构成民族刻板印象。"
      }
    ],
    taskId: "8bc179209e1e",
    outputId: "372"
  },
  {
    risk: "safe",
    riskLabel: "无风险",
    summary: "灾后资助求助内容获得正向评论反馈",
    conclusion: "发布者为和田暴雨受灾家庭征集线索并计划资助十户困难家庭，335 条评论均为祝福、点赞和线索反馈。",
    contentTitle: "女子欲资助十户困难家庭求助",
    author: "麦热依姆古丽",
    platform: "抖音",
    evidenceCounts: { text: 1, ocr: 1, asr: 1, comment: 1, vision: 1 },
    keyEvidence: [
      {
        id: "charity-content",
        type: "text",
        content: "因和田暴雨受灾，计划资助十户困难家庭并向网友征集线索。",
        explanation: "内容目的为灾后互助。"
      },
      {
        id: "charity-comments",
        type: "comment",
        content: "评论区均为正面祝福、点赞与求助线索。",
        explanation: "335 条评论未发现风险表达。"
      }
    ],
    taskId: "8bc179209e1e",
    outputId: "362"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "评论区聚集人身攻击、外貌羞辱与性羞辱",
    conclusion: "原帖为女子日常内容，228 条评论中识别到 8 条风险评论，包含非人化辱骂、外貌年龄羞辱和低俗性羞辱。",
    contentTitle: "评论区出现多条人身攻击与性羞辱言论",
    author: "我的心好累",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 8, vision: 0 },
    keyEvidence: [
      {
        id: "attack-comment-original",
        type: "comment",
        content: "ساراڭ ساراڭ ساراڭ ساراڭ ساراڭ ساراڭ",
        translation: "疯子，疯子，疯子，疯子，疯子，疯子。",
        explanation: "连续使用侮辱性称呼，构成针对具体人物的人身攻击。"
      },
      {
        id: "appearance-comment",
        type: "comment",
        content: "قاندا قىلاسىز بۇ سەت قېرى ئادەمنى ياخشى كۆرۈپ",
        translation: "你怎么会喜欢这个又丑又老的人？",
        explanation: "以外貌和年龄贬损具体人物，属于外貌年龄羞辱。"
      }
    ],
    taskId: "3ad102e072f6",
    outputId: "758"
  },
  {
    risk: "low",
    riskLabel: "低风险",
    summary: "视频画面识别到跨平台账号信息",
    conclusion: "户外舞蹈内容本身正常，画面叠加快手和小红书账号信息，形成低强度跨平台引流线索。",
    contentTitle: "户外舞蹈展示视频",
    author: "萨娅",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 2, asr: 0, comment: 0, vision: 3 },
    keyEvidence: [
      {
        id: "saya-ocr-ks",
        type: "ocr",
        content: "00:07 画面文字：快手 @2458744520",
        explanation: "画面出现其他平台账号。"
      },
      {
        id: "saya-ocr-xhs",
        type: "ocr",
        content: "00:09 画面文字：小红书号 514089680",
        explanation: "两处账号信息共同形成跨平台引流线索。"
      }
    ],
    taskId: "80faaba76135",
    outputId: "21"
  },
  {
    risk: "high",
    riskLabel: "高风险",
    summary: "评论区出现粗俗攻击、性骚扰及泛化责骂",
    conclusion: "原帖为女子卧床日常，238 条评论中识别到 7 条风险评论，包含极度粗俗攻击、性骚扰、非人化辱骂和泛化责骂。",
    contentTitle: "女子卧床视频评论区出现粗俗攻击",
    author: "我的心好累",
    platform: "抖音",
    evidenceCounts: { text: 0, ocr: 0, asr: 0, comment: 7, vision: 0 },
    keyEvidence: [
      {
        id: "bed-comment-original",
        type: "comment",
        content: "منا قاتىننىڭ قاسىندا ۇيقتاپ جاتىقان مال",
        translation: "那个睡在这个女人旁边的畜生。",
        explanation: "将具体人物称作“畜生”，构成非人化辱骂。"
      },
      {
        id: "bed-comment-context",
        type: "comment",
        content: "同一评论区另有多条明确性骚扰和粗俗人身攻击表达。",
        explanation: "多种风险类型集中出现，综合判定为高风险。"
      }
    ],
    taskId: "3ad102e072f6",
    outputId: "759"
  }
];

export function createAnalysisRecord(
  itemNumber: number,
  scenario: AnalysisScenario = "default"
): AnalysisRecord {
  const templates = scenario === "ethnic-relations"
    ? ETHNIC_RELATIONS_RECORD_TEMPLATES
    : DEFAULT_ANALYSIS_RECORD_TEMPLATES;
  const template = templates[(itemNumber - 1) % templates.length];
  const minutes = Math.max(0, itemNumber - 1);
  return {
    catalogVersion: ANALYSIS_RECORD_CATALOG_VERSION,
    itemNumber,
    ...template,
    analyzedAt: `2026-07-29 10:${String(3 + minutes).padStart(2, "0")}`
  };
}

export function createCompletedAnalysisRecords(
  count = 4,
  scenario: AnalysisScenario = "default"
): AnalysisRecord[] {
  return Array.from({ length: count }, (_, index) => createAnalysisRecord(index + 1, scenario)).reverse();
}

export function normalizeAnalysisRecords(records: readonly AnalysisRecord[]): AnalysisRecord[] {
  return records.map((record) => {
    const legacyRisk = record.risk as string;
    const normalizedRecord = legacyRisk === "review" || record.risk === "low" ? {
      ...record,
      risk: "low",
      riskLabel: "低风险"
    } as AnalysisRecord : record;

    const correctedEvidenceCounts: Record<string, AnalysisRecord["evidenceCounts"]> = {
      "758": { text: 0, ocr: 0, asr: 0, comment: 8, vision: 0 },
      "21": { text: 0, ocr: 2, asr: 0, comment: 0, vision: 3 },
      "759": { text: 0, ocr: 0, asr: 0, comment: 7, vision: 0 }
    };
    const evidenceCounts = correctedEvidenceCounts[normalizedRecord.outputId];
    return evidenceCounts ? { ...normalizedRecord, evidenceCounts } : normalizedRecord;
  });
}

const analysisRecordCache = new Map<string, AnalysisRecord[]>();

export function readStoredAnalysisRecords(investigationId: string): AnalysisRecord[] | null {
  const records = analysisRecordCache.get(investigationId);
  if (
    !records
    || !records.every((record) => (
      record.catalogVersion === ANALYSIS_RECORD_CATALOG_VERSION
      && Number.isInteger(record.itemNumber)
    ))
  ) {
    return null;
  }
  return normalizeAnalysisRecords(records);
}

export function storeAnalysisRecords(investigationId: string, records: AnalysisRecord[]) {
  analysisRecordCache.set(investigationId, records.map((record) => ({ ...record })));
}
