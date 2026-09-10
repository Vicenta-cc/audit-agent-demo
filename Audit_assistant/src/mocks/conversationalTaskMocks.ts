import type { PlatformOption, TaskSession } from "../types/conversationalTask";

export const platformOptionsList: PlatformOption[] = [
  { code: "dy", label: "抖音" },
  { code: "xhs", label: "小红书" },
  { code: "wb", label: "微博" },
  { code: "multi", label: "多平台" }
];

export const initialSessions: TaskSession[] = [
  {
    id: "session-wc-gambling",
    title: "世界杯博彩专题采集",
    status: "等待确认",
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
      policyName: "赌博博彩研判方案",
      policyDescription:
        "对采集结果中的赌博推广、赌球引流、下注诱导、盘口宣传和外部渠道引流等风险进行分析。",
      status: "等待确认",
      confirmed: true
    },
    messages: [
      {
        id: "m1-1",
        sender: "user",
        timestamp: "10:20",
        content: "对最近关于世界杯博彩这一话题相关的帖子做一下抓取和分析。"
      },
      {
        id: "m1-2",
        sender: "assistant",
        timestamp: "10:21",
        type: "task_proposal",
        content:
          "已识别为‘世界杯博彩专题采集与分析任务’。\n\n推荐使用‘赌博博彩研判方案’，对采集结果中的赌博推广、赌球引流、下注诱导、盘口宣传和外部渠道引流等风险进行分析。\n\n当前没有专门的‘世界杯博彩专题黑话库’。通用博彩黑话库覆盖范围较大，不适合直接完整用于本次专题搜索。\n\n建议本次使用以下临时搜索词：",
        proposalData: {
          taskName: "世界杯博彩专题采集与分析任务",
          taskType: "平台话题采集",
          subject: "世界杯博彩",
          policyName: "赌博博彩研判方案",
          policyDescription:
            "对采集结果中的赌博推广、赌球引流、下注诱导、盘口宣传和外部渠道引流等风险进行分析。",
          policyExpandedReason:
            "该研判方案内置赌球术语识别、资金交易诱导分类与境外博彩引流链路追踪规则，能够高效过滤体育赛事讨论中的合规分析与涉赌违规推广。",
          keywordsNotice:
            "这些临时搜索词仅用于本次任务的平台搜索和内容召回，不会自动保存到正式黑话库。",
          noticeText: "请选择需要采集的平台。",
          platformsSelected: ["dy", "xhs"],
          platformsConfirmed: true
        }
      },
      {
        id: "m1-3",
        sender: "user",
        timestamp: "10:23",
        content: "采集抖音和小红书。"
      },
      {
        id: "m1-4",
        sender: "assistant",
        timestamp: "10:24",
        content:
          "已完成本次任务配置。\n\n系统将使用本次临时搜索词在抖音和小红书进行内容召回，并使用‘赌博博彩研判方案’分析采集结果。\n\n请确认任务配置后创建任务。"
      }
    ]
  },
  {
    id: "session-intermarriage",
    title: "维汉通婚相关讨论采集",
    status: "等待确认",
    updatedAt: "09:15",
    draft: {
      taskName: "维汉通婚相关讨论采集",
      taskType: "平台话题采集",
      subject: "维汉通婚相关讨论",
      platforms: ["dy", "wb"],
      keywords: [
        "维汉通婚",
        "汉维通婚",
        "维汉婚姻",
        "民族通婚",
        "跨民族婚姻",
        "反对维汉通婚",
        "支持维汉通婚"
      ],
      policyName: "民族意识形态风险研判方案",
      policyDescription:
        "重点分析相关内容中是否存在侮辱歧视、排斥民族通婚、煽动民族对立、攻击特定民族群体等风险表达。",
      status: "等待确认",
      confirmed: true
    },
    messages: [
      {
        id: "m2-1",
        sender: "user",
        timestamp: "09:10",
        content: "抓取最近关于维汉通婚的一些帖子并进行分析。"
      },
      {
        id: "m2-2",
        sender: "assistant",
        timestamp: "09:11",
        type: "task_proposal",
        content:
          "已识别为‘维汉通婚相关公开讨论采集与分析任务’。\n\n推荐使用‘民族意识形态风险研判方案’，重点分析相关内容中是否存在侮辱歧视、排斥民族通婚、煽动民族对立、攻击特定民族群体等风险表达。\n\n当前没有专门的‘维汉通婚专题黑话库’。\n\n建议本次使用以下临时搜索词：",
        proposalData: {
          taskName: "维汉通婚相关公开讨论采集与分析任务",
          taskType: "平台话题采集",
          subject: "维汉通婚相关讨论",
          policyName: "民族意识形态风险研判方案",
          policyDescription:
            "重点分析相关内容中是否存在侮辱歧视、排斥民族通婚、煽动民族对立、攻击特定民族群体等风险表达。",
          policyExpandedReason:
            "方案聚焦涉民族关系敏感言论，结合上下文识别极端的地域歧视、撕裂民族团结的煽动性评论，精准剔除中性祝福与客观政策讨论。",
          keywordsNotice:
            "这些词只用于搜索和内容召回。内容提及维汉通婚或命中搜索词，并不代表内容本身存在风险，仍需通过‘民族意识形态风险研判方案’结合原文语义进行判断。",
          noticeText: "请选择需要采集的平台。",
          platformsSelected: ["dy", "wb"],
          platformsConfirmed: true
        }
      },
      {
        id: "m2-3",
        sender: "user",
        timestamp: "09:14",
        content: "采集抖音和微博。"
      },
      {
        id: "m2-4",
        sender: "assistant",
        timestamp: "09:15",
        content:
          "已完成本次任务配置。\n\n系统将使用本次临时搜索词在抖音和微博进行内容召回，并使用‘民族意识形态风险研判方案’分析采集结果。\n\n请确认任务配置后创建任务。"
      }
    ]
  }
];
