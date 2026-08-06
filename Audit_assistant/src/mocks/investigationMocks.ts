import type {
  InvestigationSession,
  PlatformOption,
  RecallLibraryItem,
  AuditRuleSet,
  RiskRule
} from "../types/investigation";

export const localCloseupAnchorRule: RiskRule = {
  id: "rule-ero-2",
  name: "局部露拍",
  content: "镜头刻意聚焦胸部、臀部、裆部或大腿根等敏感部位，并明显弱化人物整体、服装或场景。",
  suggestedLevel: "中风险",
  exemptionConditions: "正常体育运动、舞蹈表演、健身赛事或服装展示中短暂出现的合理镜头。",
  applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
  notes: "仅出现相关身体部位不构成命中，必须能够确认存在明显的镜头聚焦或性化展示意图。",
  enabled: true
};

export const platformOptionsList: PlatformOption[] = [
  { code: "dy", label: "抖音" },
  { code: "xhs", label: "小红书" },
  { code: "ks", label: "快手" },
  { code: "wb", label: "微博" }
];

export const mockRecallLibraries: RecallLibraryItem[] = [
  {
    id: "recall-gambling",
    name: "通用涉赌博彩召回词库",
    category: "网络赌博与黑产",
    usageDescription: "用于监测各类网络赌博、盘口引流、外围下注及带单派单相关信息。",
    words: [
      "外围盘口", "滚球下注", "比分预测带单", "澳门直营", "视讯百家乐",
      "代理开户", "棋牌体验金", "彩票走势图", "私彩套利", "体育投注站"
    ],
    applicablePlatforms: ["抖音", "小红书", "微博", "快手"],
    status: "启用",
    updatedAt: "2026-07-25 14:30",
    wordCount: 128
  },
  {
    id: "recall-ethnicity",
    name: "民族宗教涉敏召回词库",
    category: "意识形态与民族关系",
    usageDescription: "用于检索涉及民族关系、宗教话题、跨民族婚姻及地域歧视的公开讨论。",
    words: [
      "民族通婚", "维汉通婚", "跨民族婚姻", "民族风俗冲突", "清真饮食争议",
      "地域偏见", "民族身份认同", "民族语言教学"
    ],
    applicablePlatforms: ["抖音", "小红书", "微博", "知乎"],
    status: "启用",
    updatedAt: "2026-07-20 09:15",
    wordCount: 94
  },
  {
    id: "recall-fraud",
    name: "诈骗黑色产业链召回词库",
    category: "电信网络诈骗",
    usageDescription: "用于检索虚假投资理财、兼职刷单、虚假兼职招聘和冒充客服引流。",
    words: [
      "日赚百元兼职", "高额返利刷单", "内幕炒股群", "零风险套利", "代办高额信用卡"
    ],
    applicablePlatforms: ["抖音", "小红书", "微博"],
    status: "启用",
    updatedAt: "2026-07-18 16:45",
    wordCount: 156
  }
];

export const mockAuditRuleSets: AuditRuleSet[] = [
  {
    id: "ruleset-gambling",
    name: "赌博博彩风险规则集",
    category: "网络赌博",
    version: "v2.1",
    status: "已发布",
    updatedAt: "2026-07-26 11:20",
    referencedTaskCount: 14,
    generalExemptions: [
      {
        id: "gen-ex-1",
        title: "新闻报道与案件通报",
        description: "主流媒体对警方破获赌博大案的正常新闻报道、新闻发布会或案情通报。",
        enabled: true
      },
      {
        id: "gen-ex-2",
        title: "法律科普与反诈宣传",
        description: "公安机关或反诈账号对网络赌博陷阱的揭露、法律条文讲解及劝诫内容。",
        enabled: true
      },
      {
        id: "gen-ex-3",
        title: "正规体育彩票讨论",
        description: "国家中国体育彩票、福利彩票正规网点的线下购彩经历与合规新闻。",
        enabled: true
      }
    ],
    categories: [
      {
        id: "cat-gambling-lead",
        name: "下注入口引流",
        rules: [
          {
            id: "rule-gambling-1",
            name: "明确展示下注入口或二维码",
            content: "在视频画面、图片背景或文案中直接展示非法的外部赌博网站网址、App下载二维码或第三方联系方式（如 Telegram、微信号、QQ群）。",
            suggestedLevel: "高风险",
            exemptionConditions: "反诈警示案例中对非法二维码打马赛克并标注‘骗局警告’的语境。",
            applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
            notes: "若二维码属于警方防诈通报、法律宣传或打码示众示例，不予判定为风险。",
            enabled: true
          },
          {
            id: "rule-gambling-2",
            name: "评论区诱导外部私聊",
            content: "在评论区频繁发布‘看主页联系方式’‘带单稳赢私’‘有内部渠道看盘’等暗语引导用户离开平台。",
            suggestedLevel: "高风险",
            exemptionConditions: "普通用户对冒充账号的举报或讽刺揭露评论。",
            applicationStages: ["融合研判"],
            notes: "需结合上下文判断，若为曝光黑产账号的不构成违规。",
            enabled: true
          }
        ]
      },
      {
        id: "rule-gambling-odds",
        name: "盘口宣传与包赢诱导",
        rules: [
          {
            id: "rule-gambling-3",
            name: "宣扬赌球套利稳赚",
            content: "通过发布比分预测算账、外围盘口赔率对比，渲染‘跟着带单老师月入数万’‘稳赚不赔’等诱导言论。",
            suggestedLevel: "中风险",
            exemptionConditions: "纯体育爱好者基于公开球赛战绩的学术讨论或娱乐性预测且不涉及资金交易。",
            applicationStages: ["融合研判"],
            enabled: true
          }
        ]
      }
    ]
  },
  {
    id: "ruleset-ethnicity",
    name: "民族意识形态风险规则集",
    category: "意识形态与民族团结",
    version: "v1.8",
    status: "已发布",
    updatedAt: "2026-07-24 16:10",
    referencedTaskCount: 9,
    generalExemptions: [
      {
        id: "gen-ex-eth-1",
        title: "学术探讨与社会调查",
        description: "社会学、历史学或人口学研究者针对民族风俗演变、婚姻状况发表的客观研究学术观点。",
        enabled: true
      },
      {
        id: "gen-ex-eth-2",
        title: "个人感情遭遇倾诉",
        description: "个体用户因性格不合、生活习惯异同对具体某一段恋爱婚姻关系的抱怨倾诉，未上升至民族群体攻击。",
        enabled: true
      }
    ],
    categories: [
      {
        id: "cat-eth-exclusion",
        name: "排斥与煽动对立",
        rules: [
          {
            id: "rule-eth-1",
            name: "公开放话排斥通婚并侮辱特定群体",
            content: "明确宣称特定民族绝不能与另一民族通婚，并附带贬损、侮辱、仇恨性词汇攻击整个民族群体。",
            suggestedLevel: "高风险",
            exemptionConditions: "对仇恨言论的批评曝光、法律科普或辟谣说明。",
            applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
            enabled: true
          },
          {
            id: "rule-eth-2",
            name: "煽动区域性民族对立情绪",
            content: "将个别社会矛盾撕裂为民族冲突，呼吁抵制、孤立或对抗特定民族群体。",
            suggestedLevel: "高风险",
            exemptionConditions: "官方对违法犯罪案件的通报及辟谣说明。",
            applicationStages: ["融合研判"],
            enabled: true
          }
        ]
      }
    ]
  },
  {
    id: "ruleset-erotic",
    name: "色情低俗风险规则集",
    category: "网络生态净化",
    version: "v3.0",
    status: "已发布",
    updatedAt: "2026-07-22 10:05",
    referencedTaskCount: 22,
    generalExemptions: [
      {
        id: "gen-ex-ero-1",
        title: "医疗健康与人体解剖科普",
        description: "正规医疗机构或公立医院医生发表的妇科、男科、皮肤科医学知识宣教。",
        enabled: true
      },
      {
        id: "gen-ex-ero-2",
        title: "艺术绘画与经典雕塑展示",
        description: "博物馆、美术展或世界名画、经典人体雕塑作品的文化传播。",
        enabled: true
      }
    ],
    categories: [
      {
        id: "cat-ero-body",
        name: "身体隐私部位暴露",
        rules: [
          {
            id: "rule-ero-1",
            name: "明确暴露",
            content: "直接暴露生殖器、女性乳头及乳晕等敏感隐私部位。",
            suggestedLevel: "高风险",
            exemptionConditions: "新闻报道、案件通报或合理医疗科普等合法语境。",
            applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
            enabled: true
          },
          {
            ...localCloseupAnchorRule,
            applicationStages: [...localCloseupAnchorRule.applicationStages]
          }
        ]
      }
    ]
  }
];

export const initialSessions: InvestigationSession[] = [
  {
    id: "session-wc-gambling",
    title: "世界杯博彩专题调查",
    status: "报告已生成",
    updatedAt: "10:24",
    draft: {
      taskName: "世界杯博彩专题采集",
      taskType: "平台话题采集",
      subject: "世界杯博彩",
      platforms: ["dy", "xhs"],
      keywords: [
        "世界杯赌球",
        "世界杯下注",
        "世界杯外围",
        "世界杯盘口",
        "世界杯滚球",
        "世界杯比分盘",
        "世界杯带单"
      ],
      matchedRuleSet: "赌博博彩风险规则集",
      ruleSetDescription: "分析采集内容中是否存在赌球推广、下注诱导、盘口宣传、外围引流或外部联系方式导流等风险。",
      status: "已创建",
      confirmed: true
    },
    executionPhase: "completed",
    executionProgress: 100,
    messages: [
      {
        id: "msg-wc-1",
        sender: "user",
        timestamp: "10:00",
        content: "对最近关于世界杯博彩这一话题相关的帖子做一下抓取和分析。"
      },
      {
        id: "msg-wc-2",
        sender: "assistant",
        timestamp: "10:01",
        type: "task_proposal",
        proposalData: {
          taskName: "世界杯博彩专题采集",
          taskType: "平台话题采集",
          subject: "世界杯博彩",
          matchedRuleSet: "赌博博彩风险规则集",
          ruleSetDescription: "分析采集内容中是否存在赌球推广、下注诱导、盘口宣传、外围引流或外部联系方式导流等风险。",
          keywordsNotice: "当前没有专门的‘世界杯博彩专题召回词库’，通用赌博博彩召回词覆盖范围较大，不适合直接完整用于本次专题搜索。建议本次使用 7 个临时召回词。",
          platformsSelected: ["dy", "xhs"],
          platformsConfirmed: true
        }
      },
      {
        id: "msg-wc-3",
        sender: "user",
        timestamp: "10:02",
        content: "采集抖音和小红书。"
      },
      {
        id: "msg-wc-4",
        sender: "assistant",
        timestamp: "10:03",
        type: "agent_collaboration"
      },
      {
        id: "msg-wc-5",
        sender: "assistant",
        timestamp: "10:08",
        type: "report_card",
        reportData: {
          id: "report-wc-01",
          title: "世界杯博彩专题研判报告",
          totalCollected: 428,
          suspectedRisks: 42,
          suggestedReview: 11,
          keyAuthorCandidates: 8,
          findings: [
            "部分内容使用世界杯赛事热门话题包装赌球推广及盘口招商；",
            "部分短视频通过比分预测、滚球下注走势图引导粉丝互动；",
            "评论区高频出现‘进群看盘’‘私信获取稳单’等外部导流线索；",
            "约 85% 的正规赛事讨论与反诈劝诫内容已依据通用豁免规则自动排除。"
          ],
          riskDistribution: [
            { name: "外部联系方式引流", count: 21, percentage: 50 },
            { name: "盘口宣传与包赢诱导", count: 14, percentage: 33 },
            { name: "黑彩代理开户", count: 7, percentage: 17 }
          ]
        }
      },
      {
        id: "msg-wc-6",
        sender: "user",
        timestamp: "10:15",
        content: "42 条疑似风险中，哪些明确留下了下注渠道？"
      },
      {
        id: "msg-wc-7",
        sender: "assistant",
        timestamp: "10:16",
        type: "evidence_list",
        content: "在 42 条疑似风险内容中，有 9 条出现了较明确的下注或外部渠道引导。\n\n其中：\n- 4 条在视频画面中展示二维码或联系方式\n- 3 条在评论区回复中引导查看主页\n- 2 条使用‘进群看盘’‘私聊安排’等表达\n\n以上判断同时引用了原文、OCR、评论上下文和关键帧证据。",
        evidenceItems: [
          {
            id: "ev-wc-101",
            title: "今晚阿根廷赔率封神！内幕带单盘口解析",
            author: "球赛情报局长",
            platform: "抖音",
            riskType: "视频画面展示引流二维码",
            riskLevel: "高风险",
            snippet: "懂的自然懂，扫码进备用群，今晚最后一场红单拉满！",
            ocrText: "扫码添加助理V: bet_2026_wc 领高倍赔率图",
            asrText: "评论区别乱扣字，想看稳单的直接截图扫画面右下角。",
            videoTimestamp: "00:18 - 00:24",
            source: "视频第 18 秒画面右下角浮现二维码",
            publishTime: "2026-07-27 08:30"
          },
          {
            id: "ev-wc-102",
            title: "世界杯半决赛比分预测，跟着老马不踩坑",
            author: "老马盘口日记",
            platform: "小红书",
            riskType: "评论区诱导外部私聊",
            riskLevel: "高风险",
            snippet: "看我主页简介，私信‘看盘’自动发群链接，每天免费两场推荐。",
            ocrText: "主页无门槛进群",
            source: "评论区置顶回复",
            publishTime: "2026-07-26 22:15"
          },
          {
            id: "ev-wc-103",
            title: "外围滚球走势图分析，资金反套利方案",
            author: "体育风云黑产",
            platform: "抖音",
            riskType: "盘口宣传与包赢诱导",
            riskLevel: "中风险",
            snippet: "只要本金控制得当，通过对打盘口几乎零风险吃水。",
            asrText: "只要两边盘口同时挂，这波水钱稳拿。",
            videoTimestamp: "00:36 - 00:48",
            source: "语音 ASR 识别结果",
            publishTime: "2026-07-26 19:40"
          }
        ],
        inquiryOptions: [
          { label: "查看这 8 个重点作者", prompt: "列出这 8 个重点作者的详细账号与历史风险数据。" },
          { label: "发起重点用户穿透调查", prompt: "对‘球赛情报局长’进行主页和关联评论用户穿透调查。" }
        ]
      }
    ]
  },
  {
    id: "session-ethnic-relations",
    title: "维汉民族关系专项调查",
    status: "配置中",
    updatedAt: "刚刚",
    draft: {
      taskName: "维汉民族关系专项调查",
      taskType: "平台话题采集",
      subject: "维汉民族关系",
      platforms: [],
      keywords: [
        "维汉通婚",
        "维汉夫妻",
        "新疆姑娘汉族小伙",
        "跨民族婚姻",
        "民族婚恋",
        "维汉家庭",
        "通婚争议",
        "民族刻板印象"
      ],
      matchedRuleSet: "民族意识形态风险规则集",
      analysisPlanName: "维汉民族关系专题研判方案",
      ruleSetDescription: "结合原帖、维吾尔语译文与评论上下文，识别民族刻板印象、侮辱歧视、排斥通婚和煽动对立等风险表达。",
      scopeDescription: "围绕维汉婚恋、家庭互动与相关评论争议开展专题采集。",
      status: "配置中",
      confirmed: false
    },
    executionPhase: "idle",
    executionProgress: 0,
    messages: [
      {
        id: "msg-eth-1",
        sender: "user",
        timestamp: "09:10",
        content: "对最近关于维汉民族关系这一话题相关的帖子做一下抓取和分析。"
      },
      {
        id: "msg-eth-2",
        sender: "assistant",
        timestamp: "09:11",
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
  },
  {
    id: "session-key-user",
    title: "某重点用户风险调查",
    status: "研判中",
    updatedAt: "昨天",
    draft: {
      taskName: "重点用户‘球赛情报局长’深钻穿透",
      taskType: "重点用户深钻",
      subject: "重点用户及其关联导流网络",
      platforms: ["dy"],
      keywords: ["球赛情报局长", "bet_2026_wc"],
      matchedRuleSet: "赌博博彩风险规则集",
      ruleSetDescription: "穿透重点用户主页历史动态、关联互动高频账号及跨平台同名引流矩阵。",
      status: "已创建",
      confirmed: true
    },
    executionPhase: "audit_working",
    executionProgress: 65,
    messages: [
      {
        id: "msg-ku-1",
        sender: "user",
        timestamp: "昨天 16:30",
        content: "对重点用户‘球赛情报局长’进行主页和关联用户穿透调查。"
      },
      {
        id: "msg-ku-2",
        sender: "assistant",
        timestamp: "昨天 16:31",
        type: "agent_collaboration"
      }
    ]
  }
];

export const mockSopStrategies = [
  {
    id: "sop-1",
    title: "涉赌/博彩高风险账号直接封禁与证据保全 SOP",
    riskLevel: "高风险",
    category: "网络赌博与黑产",
    triggerCondition: "识别到明确外围盘口入口、下注网址二维码、私彩带单微信/QQ号",
    actionSteps: [
      "1. 多媒体专员自动抓取视频完整关键帧与 ASR 文本并上传区块链存证",
      "2. 处置系统自动对发布账号执行永久封禁与设备指纹拉黑",
      "3. 关联同 IP 及同卡号批量账号进入 7 天观察禁言队列",
      "4. 生成标准化司法移交研判简报与证据包"
    ],
    evidenceRequirement: "包含清晰盘口链接/二维码或明确引导私域下注的口述/文字证据",
    reviewPeriod: "24 小时内完成人工复核",
    updatedAt: "2026-07-26 10:00"
  },
  {
    id: "sop-2",
    title: "涉导流暗语/变体字中风险内容限流与二次研判 SOP",
    riskLevel: "中风险",
    category: "黑产导流",
    triggerCondition: "发布内容包含隐晦谐音（如'菠菜'、'滚球'、'看主页简介'等）且无明确公开盘口",
    actionSteps: [
      "1. 触发平台推荐算法限流，暂停进入公域推荐池",
      "2. 派发至人工二审队列，由专家结合账号历史行为进行综合判定",
      "3. 若 48 小时内再有类似发布，升档为高风险处置"
    ],
    evidenceRequirement: "至少包含 2 处变体暗语或主页简介引导至第三方社交平台",
    reviewPeriod: "12 小时内完成审核",
    updatedAt: "2026-07-22 15:30"
  },
  {
    id: "sop-3",
    title: "民族宗教/地域敏感言论应急响应与舆情管控 SOP",
    riskLevel: "高风险",
    category: "意识形态与民族关系",
    triggerCondition: "发布煽动民族对立、地域歧视或歪曲民族风俗的违规音视频/图文",
    actionSteps: [
      "1. 立即对违规内容执行全网下架与评论区封禁",
      "2. 启动智能舆情监测，追踪同话题二次传播趋势",
      "3. 账号实施 30 天禁言并限制提现功能",
      "4. 向合规法务专员抄送风险事件研判日志"
    ],
    evidenceRequirement: "违规原声音频/字幕文本及评论区热评Top 10截图",
    reviewPeriod: "2 小时内极速响应",
    updatedAt: "2026-07-24 11:00"
  }
];

export const mockCipherVariants = [
  {
    id: "var-1",
    originalTerm: "博彩 / 赌博",
    variants: ["菠菜", "波菜", "⚽盘口", "滚球", "bc", "🎰下注", "外围盘"],
    category: "涉赌暗语",
    riskPattern: "谐音替代 + 符号Emoji叠加",
    detectionRate: "98.4%",
    updatedAt: "2026-07-27"
  },
  {
    id: "var-2",
    originalTerm: "微信 / 导流",
    variants: ["薇心", "威信", "v信", "溦", "📌看主页", "看简介V", "➕v", "🐧Q"],
    category: "私域引流",
    riskPattern: "拆字首字母 + 引导符号",
    detectionRate: "99.1%",
    updatedAt: "2026-07-26"
  },
  {
    id: "var-3",
    originalTerm: "兼职 / 刷单 / 诈骗",
    variants: ["日赚", "兼职+v", "动动手指", "日结500", "福利群", "免费领"],
    category: "电信诈骗",
    riskPattern: "高回报许诺 + 诱导加群",
    detectionRate: "96.7%",
    updatedAt: "2026-07-25"
  }
];

export const mockLegalPolicies = [
  {
    id: "leg-1",
    title: "《中华人民共和国反电信网络诈骗法》",
    issuingBody: "全国人民代表大会常务委员会",
    effectiveDate: "2022-12-01",
    clauseNo: "第二十五条、三十八条",
    summary: "任何单位和个人不得为他人实施电信网络诈骗活动提供技术支持、导流引流、资金结算等帮助。互联网服务提供者应当建立健全风险识别与处置机制。",
    relevantRiskTypes: ["网络诈骗", "黑产引流", "涉赌博彩"]
  },
  {
    id: "leg-2",
    title: "《网络信息内容生态治理规定》",
    issuingBody: "国家互联网信息办公室",
    effectiveDate: "2020-03-01",
    clauseNo: "第六条、第七条、第十条",
    summary: "网络信息内容生产者不得制作、复制、发布含有散布谣言、扰乱经济秩序和社会秩序、煽动民族仇恨/民族歧视、破坏民族团结等违法和不良信息。",
    relevantRiskTypes: ["意识形态", "民族关系", "虚假谣言"]
  },
  {
    id: "leg-3",
    title: "《互联网用户账号信息管理规定》",
    issuingBody: "国家互联网信息办公室",
    effectiveDate: "2022-08-01",
    clauseNo: "第十一条、十二条",
    summary: "互联网信息服务提供者应当核验用户真实身份信息，对假冒、冒充或者违法违规账号依法采取限期改正、暂停使用、注销账号等处置措施。",
    relevantRiskTypes: ["重点用户管理", "仿冒账号", "违规矩阵"]
  }
];
