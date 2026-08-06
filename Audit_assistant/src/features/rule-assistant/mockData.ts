import {
  localCloseupAnchorRule,
  mockAuditRuleSets,
  mockRecallLibraries
} from "../../mocks/investigationMocks";
import type { AuditRuleSet, RiskRule } from "../../types/investigation";
import type {
  RuleAssistantCandidatePreview,
  RuleAssistantCandidateLexicon,
  RuleAssistantCandidateLexiconPreview,
  RuleAssistantConversation,
  RuleAssistantLexiconTerm,
  RuleAssistantMessage,
  RuleAssistantMessageKind,
  RuleAssistantLexicon,
  RuleAssistantRuleSet
} from "./types";

export const initialRuleAssistantRuleSets: RuleAssistantRuleSet[] = mockAuditRuleSets.map((ruleSet) => ({
  id: ruleSet.id,
  name: ruleSet.name,
  description: ruleSet.category
}));

export const initialRuleAssistantLexicons: RuleAssistantLexicon[] = mockRecallLibraries.map((library) => ({
  id: library.id,
  name: library.name,
  description: library.usageDescription
}));

export const modifiedLocalCloseupContent = "镜头刻意聚焦胸部、臀部、裆部或大腿根等敏感部位，并存在持续停留、反复展示或明显性化表达。";

export const mockRuleFileName = "平台内容审核规范（修订稿）.docx";
export const mockTerrorRuleFileName = "暴恐内容审核规范（修订稿）.docx";
export const mockTerrorLexiconFileName = "暴恐涉敏召回词表.xlsx";

export const gamblingLexiconTerms: RuleAssistantLexiconTerm[] = [
  { id: "gambling-1", primary: "外围盘口", queryType: "关键词", variants: "外围, 盘口, 滚球盘, 体育赔率", enabled: true },
  { id: "gambling-2", primary: "滚球下注", queryType: "关键词", variants: "滚球, 滚球盘, 滚球比分, 现场下注", enabled: true },
  { id: "gambling-3", primary: "比分预测带单", queryType: "标签", variants: "带单, 比分推荐, 稳单, 内部红单", enabled: true },
  { id: "gambling-4", primary: "澳门直营", queryType: "关键词", variants: "新葡京, 威尼斯人直营, 线上赌场", enabled: false },
  { id: "gambling-5", primary: "代理开户", queryType: "关键词", variants: "开户送彩金, 招一级代理, 佣金日结", enabled: true },
  { id: "gambling-6", primary: "棋牌体验金", queryType: "标签", variants: "注册送彩金, 首充送百分之百", enabled: true }
];

export const gamblingLexiconSuggestedTerms: RuleAssistantLexiconTerm[] = [
  {
    id: "gambling-world-cup-live",
    primary: "世界杯滚球",
    queryType: "关键词",
    variants: "世界杯走地, 世界杯滚球盘, 世界杯即时盘",
    enabled: true
  },
  {
    id: "gambling-world-cup-score",
    primary: "世界杯比分盘",
    queryType: "标签",
    variants: "世界杯比分投注, 正确比分盘, 比分玩法",
    enabled: true
  },
  {
    id: "gambling-world-cup-offshore",
    primary: "世界杯外围",
    queryType: "关键词",
    variants: "世界杯外盘, 世界杯境外盘, 世界杯地下盘",
    enabled: true
  }
];

export const originalLocalCloseupRule: RiskRule = {
  ...localCloseupAnchorRule,
  applicationStages: [...localCloseupAnchorRule.applicationStages]
};

export const addedTransparentClothingRule: RiskRule = {
  id: "candidate-rule-transparent-clothing",
  name: "透明衣物透视展示",
  content: "通过透明、半透明或湿透衣物，明显展示胸部、臀部、裆部等敏感区域轮廓或细节。",
  suggestedLevel: "中风险",
  exemptionConditions: "正常服装展示、新闻报道、艺术创作及合理医疗科普，且不存在刻意聚焦或性化表达。",
  applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
  notes: "应结合服装材质、镜头焦点、展示时长和整体表达意图综合判断。",
  enabled: true
};

export function createTerrorLexiconCandidate(id: string): RuleAssistantCandidateLexicon {
  const coreTerms = [
    "恐怖组织旗帜", "极端主义口号", "暴恐宣言", "恐怖分子招募", "圣战动员", "袭击预告",
    "爆炸威胁", "纵火威胁", "公共场所袭击", "自制爆炸物", "燃烧装置", "袭击教程",
    "恐怖组织徽记", "极端服饰", "极端主义手势", "变体旗帜", "暗号联络", "组织招募",
    "行动誓词", "暴力动员", "极端思想传播", "美化恐怖活动", "赞颂恐怖人员", "血腥震慑",
    "斩首视频", "爆炸现场", "袭击直播", "恐袭纪念", "极端音视频", "组织宣传片",
    "入群联络", "加密频道", "行动指令", "目标踩点", "袭击时间表", "武器改装",
    "极端主义教材", "暴恐行动手册"
  ];
  const variantSeeds = [
    "恐组旗, 组织旗, 黑旗标识",
    "极端口号, 动员口号, 宣誓口号",
    "暴恐檄文, 行动宣言, 极端宣言",
    "成员招募, 新人吸收, 组织纳新",
    "行动动员, 参战号召, 极端动员",
    "攻击预告, 行动预告, 目标预告",
    "爆破威胁, 炸弹威胁, 爆炸警告"
  ];

  return {
    id,
    name: "暴恐涉敏召回词库",
    category: "暴恐涉敏",
    description: "用于召回恐怖组织、极端主义宣传、暴力袭击威胁及相关符号变体内容。",
    terms: coreTerms.map((primary, index) => ({
      id: `${id}-term-${index + 1}`,
      primary,
      queryType: index % 5 === 2 ? "标签" : "关键词",
      variants: variantSeeds[index] || "",
      enabled: index !== 27
    }))
  };
}

export function createMockLexiconSuggestionPreview({
  id,
  conversationId,
  sourceLexiconId,
  sourceLibrary
}: {
  id: string;
  conversationId: string;
  sourceLexiconId: string;
  sourceLibrary?: RuleAssistantCandidateLexicon;
}): RuleAssistantCandidateLexiconPreview {
  const mockLibrary = mockRecallLibraries.find((library) => library.id === sourceLexiconId)
    || mockRecallLibraries.find((library) => library.id === "recall-gambling")
    || mockRecallLibraries[0];
  const baseTerms = sourceLibrary?.terms || (sourceLexiconId === "recall-gambling"
    ? gamblingLexiconTerms
    : mockLibrary.words.map((primary, index) => ({
        id: `${sourceLexiconId}-term-${index + 1}`,
        primary,
        queryType: "关键词" as const,
        variants: "",
        enabled: true
      })));
  const existingPrimaryTerms = new Set(baseTerms.map((term) => term.primary));
  const addedTerms = gamblingLexiconSuggestedTerms.filter((term) => !existingPrimaryTerms.has(term.primary));

  return {
    id,
    conversationId,
    sourceLexiconId,
    origin: "conversation",
    mode: "append",
    status: "pending",
    baselineTermIds: baseTerms.map((term) => term.id),
    addedTermIds: addedTerms.map((term) => term.id),
    library: {
      id: sourceLexiconId,
      name: sourceLibrary?.name || mockLibrary.name,
      category: sourceLibrary?.category || mockLibrary.category,
      description: sourceLibrary?.description || mockLibrary.usageDescription,
      terms: [...baseTerms, ...addedTerms].map((term) => ({ ...term }))
    }
  };
}

const assistantMessage = (
  id: string,
  kind: RuleAssistantMessageKind,
  content: string,
  sourceRuleSetIds?: string[],
  sourceLexiconIds?: string[]
): RuleAssistantMessage => ({
  id,
  role: "assistant",
  kind,
  content,
  sourceRuleSetIds,
  sourceLexiconIds
});

export const initialRuleAssistantConversations: RuleAssistantConversation[] = [
  {
    id: "global-private-domain",
    title: "私域引流资源评估",
    ruleSetId: null,
    updatedAt: "10:18",
    scenario: "global-resource",
    messages: [
      { id: "global-private-domain-u1", role: "user", content: "系统里有哪些与私域引流相关的规则和召回词？分别在哪些规则集和词库里？" },
      assistantMessage(
        "global-private-domain-a1",
        "resource-inventory",
        "目前找到 1 个相关规则集和 2 个相关召回词库。",
        ["ruleset-gambling"],
        ["recall-gambling", "recall-fraud"]
      ),
      { id: "global-private-domain-u2", role: "user", content: "如果我要专门做“二维码和联系方式引流”这一类内容，现有资源够用吗？" },
      assistantMessage(
        "global-private-domain-a2",
        "resource-coverage",
        "现有资源只能覆盖部分场景，还不足以独立支撑这一专项。",
        ["ruleset-gambling"],
        ["recall-gambling", "recall-fraud"]
      )
    ]
  },
  {
    id: "gambling-lexicon-direct-edit",
    title: "新增世界杯博彩召回词",
    ruleSetId: null,
    lexiconId: "recall-gambling",
    updatedAt: "11:18",
    scenario: "lexicon-direct-edit",
    messages: [
      {
        id: "gambling-lexicon-direct-edit-u1",
        role: "user",
        content: "请在涉赌博彩召回词库中补充世界杯相关召回词：世界杯滚球和世界杯外围按关键词，世界杯比分盘按标签，并生成常见表达变体。",
        resourceContext: {
          resourceId: "recall-gambling",
          resourceType: "lexicon",
          resourceName: "通用涉赌博彩召回词库"
        }
      },
      assistantMessage(
        "gambling-lexicon-direct-edit-a1",
        "lexicon-suggestion",
        "已根据你的要求整理出 3 个新增召回词，并分别补充了查询类型和表达变体。你可以进入词库结构预览确认或调整，确认前不会写入正式词库。",
        undefined,
        ["recall-gambling"]
      )
    ]
  },
  {
    id: "erotic-import",
    title: "导入新版审核规范",
    ruleSetId: "ruleset-erotic",
    updatedAt: "07-29",
    scenario: "import-edit",
    messages: []
  },
  {
    id: "erotic-direct-edit",
    title: "新增并调整风险规则",
    ruleSetId: "ruleset-erotic",
    updatedAt: "昨天",
    scenario: "direct-edit",
    messages: []
  },
  {
    id: "erotic-ruleset-query",
    title: "查看风险类别与高风险规则",
    ruleSetId: "ruleset-erotic",
    updatedAt: "07-28",
    scenario: "ruleset-query",
    messages: [
      {
        id: "erotic-ruleset-query-u1",
        role: "user",
        content: "请问该规则集下有几个风险类别？",
        resourceContext: {
          resourceId: "ruleset-erotic",
          resourceType: "rule-set",
          resourceName: "色情低俗风险规则集"
        }
      },
      assistantMessage(
        "erotic-ruleset-query-a1",
        "ruleset-summary",
        "当前“色情低俗风险规则集”包含 1 个风险类别，即“身体隐私部位暴露”，共 2 条风险规则：“明确暴露”和“局部露拍”。该类别主要用于识别直接暴露敏感隐私部位，或通过镜头聚焦、局部特写等方式突出敏感部位的内容。",
        ["ruleset-erotic"]
      ),
      {
        id: "erotic-ruleset-query-u2",
        role: "user",
        content: "高风险的规则有哪些？",
        resourceContext: {
          resourceId: "ruleset-erotic",
          resourceType: "rule-set",
          resourceName: "色情低俗风险规则集"
        }
      },
      assistantMessage(
        "erotic-ruleset-query-a2",
        "high-risk-rules",
        "当前共有 1 条高风险规则：“明确暴露”。该规则主要针对直接、清晰展示生殖器、女性乳头及乳晕等敏感隐私部位的内容；新闻报道、案件通报或合理医疗科普等具有明确合法语境的内容可以豁免。",
        ["ruleset-erotic"]
      )
    ]
  }
];

export function createEmptyRuleConversation(ruleSetId: string | null, index: number): RuleAssistantConversation {
  return {
    id: `rule-conversation-${Date.now()}-${index}`,
    title: ruleSetId ? "新规则对话" : "新对话",
    ruleSetId,
    updatedAt: "刚刚",
    messages: []
  };
}

export function createEmptyLexiconConversation(lexiconId: string, index: number): RuleAssistantConversation {
  return {
    id: `lexicon-conversation-${Date.now()}-${index}`,
    title: "新的词库对话",
    ruleSetId: null,
    lexiconId,
    updatedAt: "刚刚",
    messages: []
  };
}

export function createMockCandidatePreview({
  id,
  conversationId,
  sourceRuleSetId,
  createOnly,
  fileName
}: {
  id: string;
  conversationId: string;
  sourceRuleSetId: string | null;
  createOnly: boolean;
  fileName: string;
}): RuleAssistantCandidatePreview {
  if (createOnly) {
    return createTerrorCandidatePreview({ id, conversationId, sourceRuleSetId, fileName });
  }

  return {
    id,
    conversationId,
    sourceRuleSetId,
    origin: "file",
    mode: createOnly ? "create-only" : "replace-or-create",
    fileName,
    status: "pending",
    ruleChanges: {},
    ruleSet: {
      id: `candidate-${id}`,
      name: "平台内容审核规范（修订稿）",
      description: "依据上传文件独立整理的内容审核规则，覆盖身体暴露、性暗示、违规引流与未成年人保护场景。",
      category: "平台内容综合审核",
      version: "",
      status: "草稿",
      updatedAt: "刚刚",
      referencedTaskCount: 0,
      generalExemptions: [
        { id: `${id}-ex-1`, title: "新闻报道与案件通报", description: "正规媒体、主管部门发布的新闻报道、案件通报及公共事件说明。", enabled: true },
        { id: `${id}-ex-2`, title: "医疗健康科普", description: "正规医疗机构或专业人员开展的健康教育、疾病诊疗与人体知识科普。", enabled: true },
        { id: `${id}-ex-3`, title: "艺术与文化展示", description: "博物馆、美术展、影视作品评论及具有明确文化语境的艺术内容。", enabled: true },
        { id: `${id}-ex-4`, title: "体育赛事与训练", description: "正规体育比赛、专业训练及动作教学中的合理身体展示。", enabled: true },
        { id: `${id}-ex-5`, title: "反诈与安全教育", description: "以揭露风险、劝阻受骗或开展网络安全教育为目的的内容。", enabled: true },
        { id: `${id}-ex-6`, title: "学术研究与社会调查", description: "高校、研究机构或专业人员发布的客观研究和社会调查内容。", enabled: true }
      ],
      categories: [
        {
          id: `${id}-cat-body`,
          name: "身体隐私部位暴露",
          rules: [
            {
              id: `${id}-rule-body-1`,
              name: "明确暴露敏感部位",
              content: "画面直接、清晰展示生殖器、女性乳头及乳晕等身体隐私部位。",
              suggestedLevel: "高风险",
              exemptionConditions: "医疗科普、新闻报道或艺术作品展示等具有明确合理语境的内容。",
              applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
              notes: "需结合画面清晰度、展示时长和内容语境综合判断。",
              enabled: true
            },
            {
              ...localCloseupAnchorRule,
              applicationStages: [...localCloseupAnchorRule.applicationStages]
            },
            {
              id: `${id}-rule-body-3`,
              name: "透视或湿身展示",
              content: "通过透视材质、湿身效果或强光轮廓突出身体隐私部位。",
              suggestedLevel: "中风险",
              exemptionConditions: "专业服装测评、面料科普或正常水上运动记录。",
              applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
              notes: "需确认视觉重点落在敏感部位，而非服装功能本身。",
              enabled: true
            },
            {
              id: `${id}-rule-body-4`,
              name: "遮挡失效或疑似走光",
              content: "遮挡物明显失效并导致敏感部位持续可见，或以走光为主要传播看点。",
              suggestedLevel: "高风险",
              exemptionConditions: "新闻事件说明、权益维权或已充分打码的安全提醒。",
              applicationStages: ["视频关键帧提取", "融合研判"],
              notes: "注意区分无意瞬时画面与反复剪辑、慢放强化。",
              enabled: true
            }
          ]
        },
        {
          id: `${id}-cat-suggestive`,
          name: "性暗示与擦边展示",
          rules: [
            {
              id: `${id}-rule-suggestive-1`,
              name: "性暗示动作表演",
              content: "以模拟性行为、反复触碰敏感部位或明显性化姿态吸引观看。",
              suggestedLevel: "中风险",
              exemptionConditions: "专业舞蹈教学、影视评论或健康教育中的必要示范。",
              applicationStages: ["视频关键帧提取", "融合研判"],
              notes: "结合动作连续性、文案和镜头语言判断展示意图。",
              enabled: true
            },
            {
              id: `${id}-rule-suggestive-2`,
              name: "低俗挑逗文案",
              content: "使用露骨双关、性暗示问答或诱导性描述配合人物画面进行传播。",
              suggestedLevel: "中风险",
              exemptionConditions: "文学作品讨论、语言研究或反低俗倡议内容。",
              applicationStages: ["图片证据提取", "融合研判"],
              notes: "需同时关注字幕、标题、评论引导与画面之间的关联。",
              enabled: true
            },
            {
              id: `${id}-rule-suggestive-3`,
              name: "私密场景擦边展示",
              content: "在床铺、浴室或更衣环境中以暧昧构图持续展示身体和挑逗动作。",
              suggestedLevel: "中风险",
              exemptionConditions: "家居测评、装修记录或具有完整情节的影视片段。",
              applicationStages: ["视频关键帧提取", "融合研判"],
              notes: "场景本身不构成风险，应结合姿态、构图与表达目的判断。",
              enabled: true
            }
          ]
        },
        {
          id: `${id}-cat-traffic`,
          name: "违规引流与交易诱导",
          rules: [
            {
              id: `${id}-rule-traffic-1`,
              name: "私密内容交易引流",
              content: "以付费照片、私密视频或一对一服务为卖点，引导添加站外联系方式。",
              suggestedLevel: "高风险",
              exemptionConditions: "对相关黑产的调查报道、风险曝光与反诈教育。",
              applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
              notes: "应核对二维码、账号简介和评论区回复是否形成完整引流链路。",
              enabled: true
            },
            {
              id: `${id}-rule-traffic-2`,
              name: "隐晦联系方式导流",
              content: "通过谐音、拆字、主页暗示或口播暗号引导用户获取外部联系方式。",
              suggestedLevel: "中风险",
              exemptionConditions: "正常商务合作信息或公开机构的官方咨询渠道。",
              applicationStages: ["图片证据提取", "融合研判"],
              notes: "需结合账号历史内容和评论区互动确认引流目的。",
              enabled: true
            },
            {
              id: `${id}-rule-traffic-3`,
              name: "付费群组或订阅诱导",
              content: "承诺提供违规内容并诱导加入付费群、订阅频道或购买会员。",
              suggestedLevel: "高风险",
              exemptionConditions: "正规知识付费、公开课程或合规会员服务。",
              applicationStages: ["融合研判"],
              notes: "重点识别服务承诺是否直接指向色情低俗或其他违规内容。",
              enabled: true
            }
          ]
        },
        {
          id: `${id}-cat-minors`,
          name: "未成年人相关保护",
          rules: [
            {
              id: `${id}-rule-minor-1`,
              name: "疑似未成年人性化展示",
              content: "对疑似未成年人进行身体局部聚焦、性暗示动作编排或成人化性感包装。",
              suggestedLevel: "高风险",
              exemptionConditions: "正常校园活动、体育比赛或儿童服装展示中的自然记录。",
              applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
              notes: "年龄无法确认时应结合人物特征、场景和账号信息从严复核。",
              enabled: true
            },
            {
              id: `${id}-rule-minor-2`,
              name: "诱导未成年人参与低俗互动",
              content: "通过挑战、打赏或评论指令诱导未成年人完成带有性暗示的动作与拍摄。",
              suggestedLevel: "高风险",
              exemptionConditions: "经过安全设计的公益活动、正规节目或教育互动。",
              applicationStages: ["视频关键帧提取", "融合研判"],
              notes: "关注发起者意图、互动指令和受众年龄信息。",
              enabled: true
            },
            {
              id: `${id}-rule-minor-3`,
              name: "未成年人隐私信息暴露",
              content: "公开未成年人的联系方式、学校班级、家庭住址或可定位的日常行程。",
              suggestedLevel: "高风险",
              exemptionConditions: "监护人授权的正规公益寻人、主管部门通报或必要安全提醒。",
              applicationStages: ["图片证据提取", "融合研判"],
              notes: "应对画面文字、口播和评论区补充信息进行组合判断。",
              enabled: true
            }
          ]
        }
      ]
    }
  };
}

function createTerrorCandidatePreview({
  id,
  conversationId,
  sourceRuleSetId,
  fileName
}: {
  id: string;
  conversationId: string;
  sourceRuleSetId: string | null;
  fileName: string;
}): RuleAssistantCandidatePreview {
  const stages: RiskRule["applicationStages"] = ["图片证据提取", "视频关键帧提取", "融合研判"];
  const rule = (
    suffix: string,
    name: string,
    content: string,
    level: RiskRule["suggestedLevel"],
    exemptionConditions: string,
    notes: string
  ): RiskRule => ({
    id: `${id}-rule-${suffix}`,
    name,
    content,
    suggestedLevel: level,
    exemptionConditions,
    applicationStages: [...stages],
    notes,
    enabled: true
  });

  return {
    id,
    conversationId,
    sourceRuleSetId,
    origin: "file",
    mode: "create-only",
    fileName,
    status: "pending",
    ruleChanges: {},
    ruleSet: {
      id: `candidate-${id}`,
      name: "暴恐风险规则集",
      description: "用于识别恐怖主义宣传、极端组织符号、暴力恐吓和公共安全威胁内容。",
      category: "暴恐与公共安全",
      version: "",
      status: "草稿",
      updatedAt: "刚刚",
      referencedTaskCount: 0,
      generalExemptions: [
        { id: `${id}-ex-1`, title: "新闻报道与案件通报", description: "正规媒体或主管部门对暴恐案件、公共安全事件的客观报道与通报。", enabled: true },
        { id: `${id}-ex-2`, title: "反恐宣传与安全教育", description: "以识别风险、劝阻参与或普及公共安全知识为目的的反恐教育内容。", enabled: true },
        { id: `${id}-ex-3`, title: "学术研究与历史记录", description: "科研、教学及历史档案中对相关组织、事件和思想的客观分析。", enabled: true },
        { id: `${id}-ex-4`, title: "影视艺术与文学创作", description: "具有完整叙事语境且不以宣扬、煽动或招募为目的的文艺作品。", enabled: true },
        { id: `${id}-ex-5`, title: "公共安全演练", description: "由政府、学校、企业等组织开展的反恐防暴演练和应急处置培训。", enabled: true }
      ],
      categories: [
        {
          id: `${id}-cat-propaganda`,
          name: "恐怖主义与极端主义宣传",
          rules: [
            rule("propaganda-1", "宣扬恐怖主义思想", "传播、赞同或鼓吹以暴力手段制造社会恐慌、实现政治或宗教极端目标的思想主张。", "高风险", "新闻报道、学术研究或反恐教育中的必要引用。", "结合发布语境和账号历史判断是否存在明确宣扬意图。"),
            rule("propaganda-2", "美化恐怖组织或人员", "赞颂、美化或为恐怖组织、恐怖活动人员及其暴力行为辩护。", "高风险", "案件通报、历史记录或批判性评论中的客观介绍。", "关注称谓、评价用语及画面包装是否形成正向塑造。"),
            rule("propaganda-3", "传播极端主义口号", "集中展示或反复传播具有极端主义动员含义的口号、誓词和宣言。", "高风险", "安全教育、研究分析或新闻报道中的说明性呈现。", "需结合字幕、音频、配文与传播目的综合判断。")
          ]
        },
        {
          id: `${id}-cat-symbols`,
          name: "暴恐组织标识与符号",
          rules: [
            rule("symbols-1", "展示恐怖组织标识", "突出展示已认定恐怖组织的旗帜、徽记、制服或其他代表性标识。", "高风险", "新闻报道、博物馆展陈或历史教学中的客观展示。", "识别标识主体，并排除批判、揭露和教育语境。"),
            rule("symbols-2", "极端主义旗帜或服饰", "以佩戴、悬挂或表演方式传播带有极端主义含义的旗帜、服饰及配件。", "中风险", "影视道具、舞台演出或新闻画面中的合理使用。", "结合人物姿态、配文和互动内容确认表达倾向。"),
            rule("symbols-3", "变体符号暗示", "通过拆分、变形、遮挡或谐音方式规避识别并指代暴恐组织或极端主义符号。", "中风险", "识别教学、安全科普或算法研究中的样例展示。", "需核对变体与原始标识的稳定对应关系。")
          ]
        },
        {
          id: `${id}-cat-threats`,
          name: "暴力袭击与恐吓威胁",
          rules: [
            rule("threats-1", "具体暴力袭击威胁", "针对明确人物、机构或公共场所发布可执行的爆炸、纵火、砍杀等暴力威胁。", "高风险", "案件报道、影视剧情或应急演练中的非真实威胁。", "重点核查目标、时间、地点、手段及行动意图。"),
            rule("threats-2", "传播袭击制作教程", "提供爆炸物、燃烧装置或其他袭击工具的制作、改装和使用步骤。", "高风险", "合规实验、安全拆解或专业培训中经过风险控制的内容。", "区分原理科普与可直接复现的操作指导。"),
            rule("threats-3", "血腥暴力震慑", "以制造恐慌、威慑或招募为目的，集中展示暴恐活动造成的血腥伤亡画面。", "高风险", "新闻报道、案件调查或反恐教育中经过必要处理的内容。", "结合画面处理、标题文案和传播目的综合判断。")
          ]
        },
        {
          id: `${id}-cat-mobilization`,
          name: "煽动招募与行动组织",
          rules: [
            rule("mobilization-1", "煽动参与暴恐活动", "鼓动用户实施暴力袭击、加入极端行动或以暴力方式对抗社会秩序。", "高风险", "反恐劝阻、新闻转述或学术分析中的必要引用。", "关注命令式表达、行动号召和目标指向。"),
            rule("mobilization-2", "招募或组织联络", "发布加入方式、联络渠道、暗号或组织分工，用于吸收成员或协调暴恐活动。", "高风险", "案件侦破报道、安全教育或风险揭露中的信息说明。", "结合联系方式、群组入口和上下游内容判断组织意图。"),
            rule("mobilization-3", "公共场所袭击预告", "对交通枢纽、学校、商场等公共场所发布带有时间或行动线索的袭击预告。", "高风险", "官方预警、应急演练或虚构作品中的明确剧情内容。", "存在现实目标和可执行线索时应优先升级处置。")
          ]
        }
      ]
    }
  };
}

export function createMockConversationCandidatePreview({
  id,
  conversationId,
  sourceRuleSetId,
  sourceRuleSet
}: {
  id: string;
  conversationId: string;
  sourceRuleSetId: string;
  sourceRuleSet?: AuditRuleSet;
}): RuleAssistantCandidatePreview {
  const source = sourceRuleSet
    || mockAuditRuleSets.find((ruleSet) => ruleSet.id === sourceRuleSetId)
    || mockAuditRuleSets.find((ruleSet) => ruleSet.id === "ruleset-erotic")
    || mockAuditRuleSets[0];
  const ruleSet = {
    ...source,
    generalExemptions: source.generalExemptions.map((item) => ({ ...item })),
    categories: source.categories.map((category) => ({
      ...category,
      rules: category.rules.map((rule) => ({ ...rule, applicationStages: [...rule.applicationStages] }))
    })),
    description: `基于“${source.name}”当前结构生成，用于集中检查本轮对话中的规则调整。`
  };

  return {
    id,
    conversationId,
    sourceRuleSetId: source.id,
    origin: "conversation",
    mode: "replace-or-create",
    status: "pending",
    ruleSet,
    ruleChanges: {}
  };
}
