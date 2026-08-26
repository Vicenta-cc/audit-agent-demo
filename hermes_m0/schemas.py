"""The only business tools exposed by the M0 profile."""

TOOLSET = "investigation"

SEARCH_POSTS = {
    "name": "search_posts",
    "description": (
        "在服务器绑定的当前调查任务冻结范围内搜索帖子。query_text只匹配帖子标题、"
        "作者显示名、博主配文和当前已有的视频语音转写；filters只支持受控的风险等级"
        "和审核决定。结果是稳定有序的preview候选，只支持标题、作者、顺序、数量和实际"
        "可见片段，不包含帖子正文详情、Finding或Evidence；必须调用read_posts后才能"
        "依据帖子内容、作者详情和Finding进行回答或多帖比较。cursor是服务器生成的不可"
        "解释续搜参数，仅在同一搜索目标尚未满足且结果表示仍有更多时使用。同一轮最多"
        "续搜3次、累计最多返回20个候选，达到上限后按当前实际结果回答。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Natural text to match inside the current authorized scope.",
            },
            "filters": {
                "type": "object",
                "properties": {
                    "risk_level": {
                        "type": "string",
                        "enum": ["none", "low", "medium", "high"],
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["pass", "review", "reject"],
                    },
                },
                "additionalProperties": False,
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "cursor": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Opaque continuation value returned by search_posts.",
            },
        },
        "required": ["query_text", "limit"],
        "additionalProperties": False,
    },
}

LIST_TASK_POSTS = {
    "name": "list_task_posts",
    "description": (
        "List stable post previews from the interrupted task snapshot bound to this "
        "conversation. Supports controlled risk_level and decision filters. Results "
        "are candidate handles only; use read_posts for post content and Finding data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "risk_level": {
                "type": "string",
                "enum": ["none", "low", "medium", "high"],
            },
            "decision": {
                "type": "string",
                "enum": ["pass", "review", "reject"],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["limit"],
        "additionalProperties": False,
    },
}

READ_REPORT = {
    "name": "read_report",
    "description": (
        "Read the report bound to this conversation. Returns an overview, exact "
        "statistics, and a bounded ordered preview of report cases. The report "
        "and frozen snapshot are selected by the server, not by arguments."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_CASE_MEMBERS = {
    "name": "list_case_members",
    "description": (
        "List the ordered posts included for display in one report case. This is "
        "the report's selected membership, not every post in the frozen snapshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "case_ref": {
                "type": "string",
                "description": "Session-safe case reference returned by read_report.",
            }
        },
        "required": ["case_ref"],
        "additionalProperties": False,
    },
}

READ_POSTS = {
    "name": "read_posts",
    "description": (
        "Read one or more posts previously exposed in this conversation. Returns "
        "each post's complete body, display author information, and effective finding."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "post_refs": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": "Session-safe post references from prior tool results.",
            }
        },
        "required": ["post_refs"],
        "additionalProperties": False,
    },
}

TASK_READ_POSTS = {
    **READ_POSTS,
    "description": (
        "读取先前在当前对话中展示的一篇或多篇帖子。返回完整博主配文、当前已有的"
        "视频语音转写、作者显示信息和有效审核结论；不包含评论或具体风险Evidence。"
    ),
}

LIST_EVIDENCE = {
    "name": "list_evidence",
    "description": (
        "List an ordered evidence candidate directory for one post. Returns type, "
        "display metadata, and bounded previews only; it does not load full evidence. "
        "本工具只返回Evidence目录预览；完整原文、已有译文和具体审核理由需要调用"
        "read_evidence。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "post_ref": {
                "type": "string",
                "description": "Session-safe post reference from a prior tool result.",
            },
            "types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["text", "comment", "asr", "ocr", "keyframe"],
                },
                "minItems": 1,
                "maxItems": 5,
                "uniqueItems": True,
                "description": "Optional exact evidence types to include.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["post_ref"],
        "additionalProperties": False,
    },
}

READ_EVIDENCE = {
    "name": "read_evidence",
    "description": (
        "Read full content for one or more evidence objects previously exposed in "
        "this conversation, including type, parent post, and natural source metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": "Session-safe evidence references from list_evidence.",
            }
        },
        "required": ["evidence_refs"],
        "additionalProperties": False,
    },
}

TOOLS = (
    READ_REPORT,
    LIST_CASE_MEMBERS,
    READ_POSTS,
    LIST_EVIDENCE,
    READ_EVIDENCE,
)

TASK_TOOLS = (
    SEARCH_POSTS,
    LIST_TASK_POSTS,
    TASK_READ_POSTS,
    LIST_EVIDENCE,
    READ_EVIDENCE,
)


REPORT_TASK_READ_REPORT = {
    "name": "read_report",
    "description": (
        "读取服务器为当前会话绑定的调查报告概览、准确统计和类别preview。报告、"
        "ReportVersion、调查任务和冻结范围均由服务器确定，不接受范围参数。类别"
        "preview不包含类别帖子目录、帖子详情或Evidence。"
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_CATEGORY_POSTS = {
    "name": "list_category_posts",
    "description": (
        "读取已验证报告类别下，当前报告正文实际列出的帖子preview。应向用户表述为"
        "“当前报告在该类别下列出了以下帖子”；这些是报告展示成员，不是该类别的完整"
        "成员集合。preview不支持帖子正文、完整转写、Finding详情或Evidence；需要"
        "调用read_posts读取选定帖子。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category_ref": {
                "type": "string",
                "description": "read_report返回的当前会话安全类别引用。",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["category_ref"],
        "additionalProperties": False,
    },
}

REPORT_TASK_SEARCH_POSTS = {
    "name": "search_posts",
    "description": (
        "从服务器绑定的当前调查任务冻结数据中取得稳定、有序的风险帖子preview候选。"
        "风险资格由服务器依据已有Finding字段确定；query_text记录本次发现目标，服务器"
        "不按中文关键词做语义筛选或排序，需由你依据返回preview判断主题相关性、相似性"
        "和典型性。context_ref可传入先前工具返回的已验证帖子或报告类别，作为“类似的”"
        "语义上下文。结果只支持标题、作者、顺序、数量、有限内容片段和Finding预览，"
        "不支持帖子详情、完整Finding或Evidence；选定候选后必须调用read_posts。cursor"
        "是服务器生成的不可解释续取参数，仅在同一目标仍未满足且结果表示还有更多时"
        "使用；同一轮最多续取3次、累计最多20个候选。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "当前风险帖子发现的自然语言目标。",
            },
            "filters": {
                "type": "object",
                "properties": {
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["review", "reject"],
                    },
                },
                "additionalProperties": False,
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "cursor": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "search_posts返回的同一目标续取参数。",
            },
            "context_ref": {
                "type": "string",
                "description": "先前工具返回的当前帖子或报告类别引用。",
            },
        },
        "required": ["query_text", "limit"],
        "additionalProperties": False,
    },
}

REPORT_TASK_READ_POSTS = {
    **TASK_READ_POSTS,
    "description": (
        "读取先前由报告类别目录或风险发现目录展示的一篇或多篇帖子详情。返回帖子"
        "内容、作者显示信息和有效Finding；不包含Evidence目录或Evidence原文。仅有"
        "read_posts成功返回后，才可依据帖子详情回答或进行多帖子比较。"
    ),
}

REPORT_TASK_TOOLS = (
    REPORT_TASK_READ_REPORT,
    LIST_CATEGORY_POSTS,
    REPORT_TASK_SEARCH_POSTS,
    REPORT_TASK_READ_POSTS,
    LIST_EVIDENCE,
    READ_EVIDENCE,
)


REAL_REPORT_READ_REPORT = {
    "name": "read_report",
    "description": (
        "读取服务器为当前会话绑定的已发布调查报告。返回报告概览、确定性统计、3个"
        "InvestigationFinding preview和2个未归入共性发现的standalone风险事项"
        "preview。preview只是导航摘要，不是帖子或Evidence详情；报告陈述应明确归因于"
        "报告，用户追问为什么时需沿Finding到Post和Evidence下钻。"
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_FINDING_POSTS = {
    "name": "list_finding_posts",
    "description": (
        "用于浏览一个已经明确知道的InvestigationFinding或报告类别的关联帖子。仅当用户"
        "提到某个已知调查发现、报告类别，或询问‘这个发现涉及哪些帖子’时使用。读取该"
        "发现涉及的全部报告关联帖子，而非仅正文展示成员；每篇标明是否为代表帖子，并返"
        "回解释该Post为何属于当前Finding的专属AuditFinding摘要和有界membership Evidence"
        "子集。此Evidence子集只能用于解释当前归类，不能冒充该Post的全部Evidence；若摘要"
        "已足以回答归类原因可以直接复用，只有需要Evidence详情时才读取对应evidence_ref。"
        "本工具不会按主题搜索，不负责判断语义相似性，也不会发现报告类别之外的新帖子。"
        "如果用户要求在当前调查中寻找某主题、类似风险内容或几篇相关风险帖子，必须使用"
        "search_posts。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "finding_ref": {
                "type": "string",
                "description": "read_report返回的当前会话安全InvestigationFinding引用。",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["finding_ref"],
        "additionalProperties": False,
    },
}

REAL_REPORT_SEARCH_POSTS = {
    "name": "search_posts",
    "description": (
        "用于在当前调查范围内寻找与用户主题相关的风险帖子。当用户说‘搜索’、‘找几篇"
        "相关内容’、‘还有类似风险帖子吗’，或提出婚史、外貌、民族、地域等主题时使用。"
        "不得用list_finding_posts代替主题搜索。服务器从当前调查任务冻结数据中取得稳定、"
        "有序的风险帖子preview候选；requested_count表示用户最终希望你选出的相关帖子数"
        "量，不是服务器候选池大小。风险资格由服务器依据已有Finding字段确定；query_text只"
        "记录发现目标，服务器不做中文语义匹配、筛选或排序。当前风险候选不超过20篇时一"
        "次返回全部候选；超过20篇时才通过cursor稳定分页。候选preview不是语义匹配结果，"
        "也不是帖子或Evidence详情；应阅读候选preview，只选择实际内容与用户主题相关的帖"
        "子，按实际返回结果回答，不足时按实际数量回答，不得拿不相关候选补足。需要具体正文、作者、Finding"
        "或完整审核资料时，必须继续调用read_posts。搜索结果不等于精确关键词匹配，也不代"
        "表已经穷举全部语义相似内容。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "当前风险帖子发现目标；服务器只记录，不执行语义匹配。",
            },
            "requested_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": (
                    "用户希望最终获得的相关帖子数量；不控制服务器候选池大小。"
                ),
            },
            "filters": {
                "type": "object",
                "properties": {
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["review", "reject"],
                    },
                },
                "additionalProperties": False,
            },
            "cursor": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": (
                    "仅在还有未展示风险候选时由search_posts返回的稳定续取参数；"
                    "不表示还有语义匹配结果。"
                ),
            },
            "context_ref": {
                "type": "string",
                "description": "先前工具返回的当前帖子或InvestigationFinding引用。",
            },
        },
        "required": ["query_text", "requested_count"],
        "additionalProperties": False,
    },
}

REAL_REPORT_READ_POSTS = {
    **REPORT_TASK_READ_POSTS,
    "description": (
        "读取先前由InvestigationFinding成员目录、standalone风险事项或风险搜索展示的"
        "一篇或多篇帖子详情。返回选定Post的结构化内容和完整effective AuditFinding；"
        "不包含Evidence目录或Evidence详情。"
    ),
}

REAL_REPORT_LIST_EVIDENCE = {
    **LIST_EVIDENCE,
    "description": (
        "只返回read_posts已读取Post的Evidence目录预览；切换到Post视角，返回该帖所记录的全部Evidence目录，而不是"
        "某个InvestigationFinding membership使用的Evidence子集。目录包含每条Evidence"
        "的有来源摘要、类型和安全引用，足以比较该帖还有哪些风险；需要评论原文、完整"
        "翻译或详细审核理由时再调用read_evidence。Evidence的风险类型只能根据当前Evidence"
        "明确出现的内容判断；如果Evidence只提到年龄，不得扩展为外貌、婚史、生育或其他"
        "风险类型，也不得把相邻主题、推测关系或可能含义写成已确认事实。"
    ),
}

REAL_REPORT_READ_EVIDENCE = {
    **READ_EVIDENCE,
    "description": (
        "读取选定Evidence的完整详情。可读取list_finding_posts返回的membership Evidence"
        "引用以深入解释当前归类，也可读取list_evidence返回的Post全量Evidence引用。"
        "Evidence必须保持其来源范围，不得把membership子集表述为Post全部Evidence。Evidence"
        "的风险类型只能根据当前Evidence明确出现的内容判断；如果只提到年龄，不得扩展为"
        "外貌、婚史、生育或其他风险类型，也不得把相邻主题、推测关系或可能含义写成已确认事实。"
    ),
}

LIST_POST_RISK_COMMENTS = {
    "name": "list_post_risk_comments",
    "description": (
        "列出一个已由服务器验证、且属于当前授权Report的Post之下，冻结Snapshot中全部"
        "对象自身audit_status=completed且自身risk_level为low/medium/high的Comment。"
        "本目录不依赖Report Evidence，不会继承Parent Post风险；返回完整评论原文、评论"
        "自身风险类型和等级、时间、Parent Post，以及稳定身份可用时的统一Account引用和"
        "Account occurrence引用。Comment原文完整仅表示无需再次读取原文；只能复述目录返回"
        "的冻结audit_status、risk_level和risk_type，不得自行解释工具未提供的审核依据。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "post_ref": {
                "type": "string",
                "description": "当前Report工具先前返回且服务器验证过的Post引用。",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "cursor": {
                "type": "string",
                "description": "上一页返回的同一Parent Post稳定续取引用。",
            },
        },
        "required": ["post_ref", "limit"],
        "additionalProperties": False,
    },
}

REAL_REPORT_TASK_TOOLS = (
    REAL_REPORT_READ_REPORT,
    LIST_FINDING_POSTS,
    REAL_REPORT_SEARCH_POSTS,
    REAL_REPORT_READ_POSTS,
    LIST_POST_RISK_COMMENTS,
    REAL_REPORT_LIST_EVIDENCE,
    REAL_REPORT_READ_EVIDENCE,
)


GET_ACCOUNT_OVERVIEW = {
    "name": "get_account_overview",
    "description": (
        "读取任意可信Account入口指向账号的完整活动概览。入口可来自报告默认账号卡片、"
        "完整报告账号索引、Post作者、Comment作者或comment target博主；服务器默认并且只查询当前用户已授权的"
        "全部调查数据，确定性返回评论总数、涉及帖子数、涉及帖子作者数、最早和最近活动"
        "时间、发布帖子数、按来源调查的活动分布及带安全引用的评论对象分布；来源任务只作"
        "记录维度，不能切换查询范围。账号"
        "身份由服务器稳定解析，不能按显示名合并。概览不提供评论风险率，不从父帖继承评论"
        "风险，也不提供历史AuditFinding或Evidence。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "account_ref": {
                "type": "string",
                "description": "服务器返回的当前会话Account引用或当前Report的稳定账号索引引用。",
            }
        },
        "required": ["account_ref"],
        "additionalProperties": False,
    },
}


LIST_ACCOUNT_OCCURRENCES = {
    "name": "list_account_occurrences",
    "description": (
        "按角色列出当前账号在全部已授权调查数据中的活动记录。kind=comment_author只返回该"
        "账号作为评论者的记录，并保留原评论、Parent Post与Parent Post作者链路；"
        "kind=post_author只返回该账号自己发布的Post，Post occurrence没有Parent Post。"
        "结果按时间倒序稳定分页，来源任务只是元数据。返回内容是preview；展开一条记录必须"
        "继续调用read_account_occurrence。comment_target_ref可筛选指定评论对象，但只允许和"
        "kind=comment_author一起使用；risk_filter=risk_only只返回对象自身审核完成且自身风险"
        "等级为low/medium/high的记录，不继承Parent Post风险。筛选与计数均由服务器完成。不能把评论者、帖子作者或"
        "被评论帖作者混为一人。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "account_ref": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["comment_author", "post_author"],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "cursor": {
                "type": "string",
                "description": "上一页返回的当前会话稳定续取短别名。",
            },
            "comment_target_ref": {
                "type": "string",
                "description": (
                    "get_account_overview评论对象分布返回的当前会话安全短别名；"
                    "仅用于筛选comment_author记录。"
                ),
            },
            "risk_filter": {
                "type": "string",
                "enum": ["risk_only"],
                "description": "可选；仅保留对象自身审核完成且自身有风险的记录。",
            },
        },
        "required": ["account_ref", "kind", "limit"],
        "additionalProperties": False,
    },
}


READ_ACCOUNT_OCCURRENCE = {
    "name": "read_account_occurrence",
    "description": (
        "展开list_account_occurrences或list_post_risk_comments实际展示的一条活动记录。评论记录返回完整存储评论、"
        "正确Parent Post预览和安全引用、Parent Post作者、时间和来源任务；要打开完整Parent "
        "Post必须继续调用read_account_post。发布记录返回原Post、当前账号"
        "作为Post作者、时间和来源任务，且不会虚构Parent Post。本工具不提供历史"
        "AuditFinding或Evidence；用户追问历史审核依据时必须明确当前Account Activity只能"
        "确认到活动来源链路，不能根据Post或Comment内容自行推断风险原因。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "occurrence_ref": {
                "type": "string",
                "description": "list_account_occurrences返回的当前会话活动记录短别名。",
            }
        },
        "required": ["occurrence_ref"],
        "additionalProperties": False,
    },
}


READ_ACCOUNT_POST = {
    "name": "read_account_post",
    "description": (
        "读取read_account_occurrence实际展示的Comment所指向的完整Parent Post。服务器使用"
        "该occurrence的来源调查与Post identity解析经过授权报告中的冻结Post payload，不按"
        "标题或显示名匹配。返回标题、完整正文、帖子作者、发布时间、来源调查及已有原始内容"
        "投影；没有冻结payload时明确返回不可用。本工具不返回或解释跨任务AuditFinding、"
        "Evidence、Claim或风险原因。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "post_ref": {
                "type": "string",
                "description": (
                    "read_account_occurrence返回的当前会话Parent Post安全短别名。"
                ),
            }
        },
        "required": ["post_ref"],
        "additionalProperties": False,
    },
}


M2_ACCOUNT_ACTIVITY_TOOLS = (
    *REAL_REPORT_TASK_TOOLS,
    GET_ACCOUNT_OVERVIEW,
    LIST_ACCOUNT_OCCURRENCES,
    READ_ACCOUNT_OCCURRENCE,
    READ_ACCOUNT_POST,
)
