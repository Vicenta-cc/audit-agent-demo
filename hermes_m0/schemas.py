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


REPORT_COMMENT_STATISTICS_NOTICE = (
    "Comment coverage counts every stored comment in this report's frozen posts, including all authors; "
    "it is not the platform total or all crawl-task records. Completed, failed, pending and unknown "
    "are disjoint and sum to total. comment_own_risk counts only completed comments with their own "
    "low/medium/high risk; direct_comment_evidence_count counts selected direct comment evidence. "
    "Neither evidence counts, finding membership subsets, account cards nor parent-post risk "
    "can replace comment audit statistics. Null means unavailable, not zero. "
    "These complete statistics may be answered directly without reading posts or evidence."
)

REPORT_STATISTICS_DESCRIPTION = (
    "统计问题直接读取本工具给出的汇总，无须遍历帖子、证据或账号。"
    "comment_audit_coverage统计当前报告全部冻结帖子下、所有作者的已存评论，"
    "不代表平台全部评论或任务全部采集记录。total=completed+failed+pending+unknown，"
    "依次为审核完成、失败、待处理、状态未知，四项互斥；independently_reviewed_comments等于completed。"
    "comment_own_risk等于coverage.risk，统计已完成审核且评论自身风险为低/中/高的数量。"
    "risk_counts和decision_counts分别是帖子的风险等级和审核决定分布；本汇总未提供评论的低/中/高分项。"
    "direct_comment_evidence_count对用户称为‘报告引用的评论材料数’，它与风险评论数分别统计。"
    "这三项分别反映完成了多少评论审核、多少评论被判定有风险，以及报告引用了多少评论材料来说明发现。"
    "理解这些含义后，根据用户问题自然组织语言和篇幅，无须遵循固定句式或同时列出全部指标。"
    "数值取当前工具结果；有失败或待处理时按问题需要说明，已存总数不等于审核完成数。"
    "具体聚类中的材料称为‘支撑这类发现的帖子和评论’。无需主动展开未入选数量或原因；"
    "用户追问具体对应关系时再查相关明细。未读明细称‘尚未读取’，不称‘报告缺失’。"
    "available=false或null表示资料不足，不是0，不用其他统计补填。"
    "本工具仅读取当前会话绑定报告，不支持按报告名切换。"
)

REAL_REPORT_READ_REPORT = {
    "name": "read_report",
    "description": (
        "读取服务器为当前会话绑定的已发布调查报告。返回报告概览、确定性统计、3个"
        "InvestigationFinding preview和2个未归入共性发现的standalone风险事项"
        "，以及该报告对应任务在确认时冻结的规则来源和召回词配置"
        "preview。preview只是导航摘要，不是帖子或Evidence详情；报告陈述应明确归因于"
        "报告，用户追问具体风险依据时需沿Finding到Post和Evidence下钻。"
        "task_configuration中的search_terms是任务冻结的召回配置，actual_matched_terms是"
        "已记录的实际命中词；不能用帖子话题标签替代。若available为false，应如实说明报告快照"
        "没有保存这项配置，不要猜测。"
        + REPORT_STATISTICS_DESCRIPTION
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_FINDING_POSTS = {
    "name": "list_finding_posts",
    "description": (
        "用于浏览一个已经明确知道的InvestigationFinding或报告类别的关联帖子。仅当用户"
        "提到某个已知调查发现、报告类别，或询问‘这个发现涉及哪些帖子’时使用。读取该"
        "发现涉及的全部报告关联帖子，而非仅正文展示成员；每篇标明标题、发布作者、平台、"
        "是否为代表帖子、整帖审核摘要和整帖审核等级，并返回解释该Post为何属于当前Finding"
        "的归类材料摘要。membership_audit_finding是整帖审核背景；真正用于解释当前归类的是"
        "membership_evidence_subset，对用户可称‘支撑这类发现的材料’，只对应这项发现，"
        "不保证覆盖报告为该帖保留的其他材料。post_total_evidence_count是报告为该帖保留的"
        "直接支持材料总数，不是该聚类的材料数，也不是评论总数或风险评论数；"
        "post_content_loaded=false表示尚未读取帖子正文。每张帖子卡片的detail_links.detail_url"
        "是该同一帖子的报告详情入口；用户概览询问时使用limit=5并明确只展示5篇代表帖，"
        "不得把5篇说成聚类全部成员。"
        "若摘要已足以回答归类原因可以直接复用，只有需要Evidence详情时才读取对应evidence_ref。"
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
        "不包含Evidence目录或Evidence详情。返回的detail_links.detail_url是该帖在当前"
        "报告中的审核详情入口，platform_url是冻结记录中已有的平台来源链接；若字段有值，"
        "回答时原样展示，不能自行拼接或猜测链接。"
    ),
}

REAL_REPORT_LIST_EVIDENCE = {
    **LIST_EVIDENCE,
    "description": (
        "用户问一篇帖子在报告里还有哪些材料、其他风险依据或材料目录时使用。"
        "用先前报告、归类成员或搜索工具返回且已验证的post_ref即可；只读材料目录不必先读帖子正文。"
        "返回当前报告冻结范围内与该帖相关的材料目录预览，可称‘报告中这篇帖子的引用材料目录’，"
        "覆盖范围不限于某一类发现使用的membership子集，也不等于后台全部审核资料、全部评论或全部风险评论。"
        "candidates是本次返回的摘要；matched_count是筛选后匹配数，returned_count是本次返回数。"
        "directory_complete_for_post=true才表示当前报告中该帖的目录已完整返回；有类型筛选或truncated=true时按实际范围说明。"
        "需要尽量列全时可不设types并将limit设为20；仍被截断时说明尚未列全，不猜测其余内容。"
        "post_total_evidence_count计数该帖冻结的直接支持材料，不能用作评论数或某一聚类的材料数。"
        "摘要足以回答有哪些材料时直接使用；用户需要原文、完整翻译或详细审核理由时再调用read_evidence。"
        "目录完整与每条材料原文完整是两回事。风险解释只依据实际返回的内容，不扩展到未出现的风险类型。"
    ),
}

REAL_REPORT_READ_EVIDENCE = {
    **READ_EVIDENCE,
    "description": (
        "读取选中的单条或多条材料详情，包括已有原文、译文和审核说明。"
        "list_finding_posts的引用对应支撑某类发现的材料；list_evidence的引用来自当前报告中该帖的材料目录。"
        "保持材料各自的来源与归类用途；读完选中材料不代表已读完该帖全部材料或评论。Evidence"
        "的风险类型只能根据当前Evidence明确出现的内容判断；如果只提到年龄，不得扩展为"
        "外貌、婚史、生育或其他风险类型，也不得把相邻主题、推测关系或可能含义写成已确认事实。"
        "若来源是Comment，source_comment.author只在同一ReportVersion、Snapshot、Parent Post下按"
        "comment_id精确关联成功时返回；不得从评论文字、翻译、昵称或数组位置猜测。有display_name"
        "时可回答昵称；有public_profile_identifier时按其中文label回答抖音号，缺失时说明当前冻结"
        "资料未记录可公开展示的抖音号。用户追问账号活动时，仅在account_ref存在时调用现有"
        "Account Activity，无需按昵称重新搜索；无引用时可查同名候选，但不能仅凭昵称认定为该证据作者。"
    ),
}

LIST_POST_RISK_COMMENTS = {
    "name": "list_post_risk_comments",
    "description": (
        "列出一个已由服务器验证、且属于当前授权Report的Post之下，冻结Snapshot中全部"
        "对象自身audit_status=completed且自身risk_level为low/medium/high的Comment。"
        "面向用户可称‘这篇帖子下已审核为风险的评论’。它按评论自身审核结果筛选，"
        "与报告引用了哪些评论材料分别记录；材料条数不能代替风险评论数。"
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


SEARCH_ACCOUNTS = {
    "name": "search_accounts",
    "description": (
        "按昵称查找用户/账号，必填nickname；唯一精确匹配时直接返回完整账号活动概览。"
        "用户按昵称问‘还评论了什么/发过什么/经常评论哪些博主’时，先调用本工具；"
        "无需先read_report、搜索帖子、遍历风险评论或查找完整账号索引工具。"
        "搜索当前用户全部已授权调查中的所有稳定账号及已记录昵称，不限Top 5、当前报告或风险账号。"
        "默认exact精确匹配；contains仅用于用户要求模糊查找。"
        "match_status=unique时overview已含评论数、发帖数、近期时间及按评论次数排序的博主分布，"
        "可直接回答，不必再get_account_overview；列评论/发帖明细用overview.account.ref调用"
        "list_account_occurrences，必须传account_ref、kind（comment_author或post_author）和limit；未指定数量的发帖查询默认limit=5，仅展示最近5篇示例，评论明细通常limit=20。同名或模糊匹配返回needs_selection，须让用户选择，禁止取第一位或合并。"
        "not_found只表示授权资料无匹配。total_count是全部匹配人数，不是本页人数；"
        "需要全部候选时使用next_offset继续同一查询。已有确定account_ref的连续追问直接复用，无需重复搜人。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "minLength": 1, "maxLength": 200,
                         "description": "用户给出的昵称，不填内部账号引用。"},
            "match_mode": {"type": "string", "enum": ["exact", "contains"], "default": "exact"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
        },
        "required": ["nickname"],
        "additionalProperties": False,
    },
}


GET_ACCOUNT_OVERVIEW = {
    "name": "get_account_overview",
    "description": (
        "已有account_ref时读取该账号完整活动概览。只有昵称时先用search_accounts；"
        "search_accounts唯一匹配已返回overview时直接复用，不重复调用本工具。入口可来自报告默认账号卡片、"
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
        "已有账号引用时，按角色列出其全部已授权调查中的活动。必填account_ref、kind和limit（1~20）。只有昵称先search_accounts取得引用，"
        "不需要先查询帖子或风险评论。只问数量或常评论博主时直接用已有overview，不必列明细。"
        "kind=comment_author只返回该"
        "账号作为评论者的记录，并保留原评论、Parent Post与Parent Post作者链路；"
        "kind=post_author只返回该账号自己发布的Post，Post occurrence没有Parent Post。"
        "问‘他还发过什么帖子’且未指定数量或要求全部时，使用limit=5，展示最近收录的最多5篇示例后结束本轮，不因has_more=true自动翻完所有页。"
        "说明matched_count对应当前授权资料与筛选下的发帖总数，以及本次实际展示篇数；不足5篇按实际列出，0篇表示授权资料未收录其发帖。"
        "用户指定数量则按要求取；追问更多时沿同一账号、角色和筛选的cursor续取。面向用户的列表必须写入最终回答，中间工具调用时的文字不算已展示。"
        "结果按时间倒序稳定分页，来源任务只是元数据。列表文本只能称为评论摘要/预览，不能称完整原文；展开一条记录必须"
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
        "展开一条活动记录，必填occurrence_ref，使用list_account_occurrences或list_post_risk_comments返回的活动引用。评论记录返回完整存储评论、"
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
        "读取评论对应完整原帖，必填post_ref，使用read_account_occurrence返回的parent_post.ref。服务器使用"
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
    SEARCH_ACCOUNTS,
    GET_ACCOUNT_OVERVIEW,
    LIST_ACCOUNT_OCCURRENCES,
    READ_ACCOUNT_OCCURRENCE,
    READ_ACCOUNT_POST,
)
