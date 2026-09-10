from __future__ import annotations

from copy import deepcopy


COMMON_SOURCE_REFS = [
    {
        "title": "网络信息内容生态治理规定",
        "url": "https://www.gov.cn/zhengce/zhengceku/2019-12/21/content_5462973.htm",
        "notes": "违法和不良信息治理总纲，覆盖暴恐、极端、色情、赌博、诈骗、谣言、歧视等红线。",
    },
]


DEFAULT_KNOWLEDGE_PACKAGES: dict[str, dict] = {
    "terror": {
        "schema_version": "1.0",
        "id": "terror",
        "title": "暴恐极端风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS + [
            {
                "title": "中华人民共和国反恐怖主义法",
                "url": "https://www.gov.cn/zhengce/2015-12/28/content_5029899.htm",
                "notes": "界定恐怖主义、极端主义宣扬、煽动、组织、资助和协助相关风险边界。",
            },
        ],
        "audit_goal": "识别暴恐宣传、极端主义宣扬、暴力煽动、武器展示、组织招募和现实伤害行动号召风险",
        "output_labels": ["暴恐宣传", "极端主义宣扬", "暴力煽动", "武器展示", "组织招募", "行动号召"],
        "risk_definition": {
            "included": [
                "宣扬、赞美或模仿恐怖主义、极端主义、现实暴力袭击",
                "以文字、语音、图片或视频煽动伤害、爆炸、袭击、报复或线下聚集",
                "武器、爆炸物、危险工具展示同时伴随威胁话术、攻击对象或行动号召",
                "组织招募、筹款、接头、组群、线下聚集、执行路径或联系方式",
                "传播暴恐组织符号、口号、战果或制作使用危险物的教程",
            ],
            "excluded": [
                "新闻报道、公共安全提醒、反恐科普、举报或批判暴恐内容",
                "影视、游戏、小说、历史资料讨论或合法军事、警务、装备科普",
                "道具模型、体育射击、收藏展示等无现实伤害意图和行动路径的内容",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "危险物 + 威胁或行动号召",
                    "required_evidence": ["vision_or_ocr", "text_or_asr_or_comment"],
                    "description": "画面出现武器、爆炸物、袭击准备或危险教程，同时文本/口播/评论存在攻击对象、威胁、行动号召或执行路径。",
                },
                {
                    "name": "组织招募或接头闭环",
                    "required_evidence": ["text_or_comment", "contact_or_group_or_location"],
                    "description": "出现招募、筹款、接头、群入口、线下聚集、联系方式或任务分工等现实组织线索。",
                },
            ],
            "medium": [
                {
                    "name": "暴恐黑话或组织符号",
                    "required_evidence": ["keyword_or_symbol"],
                    "description": "出现暴恐黑话、组织符号、攻击性口号或疑似招募语气，但缺少明确执行路径。",
                }
            ],
            "low": [
                {
                    "name": "弱相关或语境不完整",
                    "required_evidence": ["weak_keyword"],
                    "description": "只出现弱相关词、剧情台词、历史事件、武器模型或上下文不完整表达。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注标题、正文、账号资料中的威胁对象、行动号召、组织招募、筹款、接头暗号、联系方式和危险教程。",
            "ocr": "关注字幕、水印、聊天截图、海报、二维码旁文字中的组织名、口号、行动暗号、群入口、联系方式和位置线索。",
            "asr": "关注口播、背景音中的威胁话术、攻击对象、动员语气、招募或极端主义宣扬。",
            "vision": "关注武器、爆炸物、危险工具、袭击场景、组织旗帜/符号、训练画面、伤害动作和现实场景。",
            "comment": "关注评论区接头暗号、求群、求教程、线下聚集、赞美袭击或作者迎合互动。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["新闻", "反恐", "科普", "演习", "影视", "游戏", "历史", "道具", "模型", "警示"],
        },
        "evidence_rules": [
            {
                "id": "terror_weapon_threat",
                "name": "武器/爆炸物 + 威胁或行动号召",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "vision_or_ocr", "type": "weapon_or_explosive_or_attack_preparation"},
                    {"source": "text_or_asr_or_comment", "type": "threat_target_call_to_action_or_execution_path"},
                ],
            },
            {
                "id": "terror_recruitment_path",
                "name": "组织招募/接头路径",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_comment", "type": "recruitment_fundraising_grouping_or_contact"},
                    {"source": "text_or_ocr_or_comment", "type": "contact_group_location_or_task_assignment"},
                ],
            },
        ],
        "fusion_rules": [
            "高危必须出现危险对象/行为与宣扬、威胁、招募或执行路径的组合；单独武器、战争词、历史事件或剧情台词不得直接判 high。",
            "图片/关键帧中的武器和 OCR 只是候选线索，融合阶段必须结合标题、正文、ASR、评论判断是否存在现实伤害意图。",
        ],
        "exemption_rules": [
            {"name": "新闻/科普/举报", "condition": "内容主旨为报道、科普、举报、辟谣、公共安全提醒或反恐宣传，未提供执行路径", "effect": "downgrade_or_pass"},
            {"name": "影视游戏/历史/道具", "condition": "明确处于影视游戏剧情、历史资料讨论、合法装备科普或道具展示语境", "effect": "downgrade_or_pass"},
        ],
    },
    "drug": {
        "schema_version": "1.0",
        "id": "drug",
        "title": "涉毒风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS + [
            {
                "title": "中华人民共和国禁毒法",
                "url": "https://www.gov.cn/flfg/2007-12/29/content_847311.htm",
                "notes": "支撑毒品、吸贩毒、非法提供或传播涉毒信息的判定边界。",
            },
        ],
        "audit_goal": "识别涉毒交易、吸贩毒暗号、违禁药物导流、同城邀约、组织接头和吸食工具制作买卖风险",
        "output_labels": ["涉毒交易", "吸贩毒暗号", "违禁药物导流", "同城邀约", "吸食展示", "工具制作买卖"],
        "risk_definition": {
            "included": [
                "毒品或违禁药物买卖、代购、邮寄、同城自取、询价、预约或交付",
                "价格、数量、支付方式、交付位置、联系方式、二维码、群入口或私聊暗号",
                "吸食展示、吸食工具、可疑药片/粉末/包装与交易暗示结合",
                "评论区求货、求渠道、接头暗号、作者主动回应或引导",
            ],
            "excluded": [
                "禁毒宣传、新闻报道、法律科普、反毒举报、影视剧情批判",
                "医疗合规药品说明、正规药品科普、康复治疗或戒毒经验分享",
                "歌词、调侃、剧情台词且无获取意图和交易路径的内容",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "违禁对象 + 交易路径",
                    "required_evidence": ["keyword_or_vision", "contact_price_payment_delivery"],
                    "description": "涉毒对象、黑话、可疑物品或吸食工具与价格、数量、支付、同城、邮寄、联系方式等获取路径共同出现。",
                }
            ],
            "medium": [
                {
                    "name": "黑话 + 接头互动",
                    "required_evidence": ["keyword", "comment_or_private_contact"],
                    "description": "出现涉毒黑话或可疑物品，同时评论或正文存在求渠道、私聊、同城、有没有货等互动。",
                }
            ],
            "low": [
                {
                    "name": "弱相关语境",
                    "required_evidence": ["weak_keyword"],
                    "description": "仅出现疑似暗号、歌词、剧情、反毒讨论或上下文不足的表达。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注标题、正文里的买卖、价格、数量、同城、私聊、暗号、邮寄、代购、上门、接头等获取意图。",
            "ocr": "关注聊天截图、价格表、封面、水印、二维码旁文字中的联系方式、同城、价格、数量、暗号和群入口。",
            "asr": "关注口播中的暗号、规避表达、同城邀约、交易话术、联系方式和交付方式。",
            "vision": "关注可疑药片、粉末、包装、吸食工具、二维码、聊天截图、转账截图和场景上下文。",
            "comment": "关注求渠道、求联系方式、求货、有没有、同城、私我、作者回复、群入口和接头互动。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["禁毒", "反毒", "科普", "法律", "新闻", "举报", "戒毒", "医疗", "药品说明"],
        },
        "evidence_rules": [
            {
                "id": "drug_trade_path",
                "name": "涉毒交易路径",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "keyword_or_vision", "type": "drug_related_object_slang_or_tool"},
                    {"source": "text_or_ocr_or_asr_or_comment", "type": "contact_price_payment_delivery_or_location"},
                ],
            }
        ],
        "fusion_rules": [
            "核心判断是否形成违禁物品/暗号 + 获取或交易意图 + 联系/支付/交付路径；单个弱相关词不得直接 high。",
            "医疗合规、禁毒宣传、新闻科普和举报语境应降级或通过；引用涉毒话术进行警示不等于传播交易信息。",
        ],
        "exemption_rules": [
            {"name": "禁毒宣传/新闻科普", "condition": "主旨为禁毒、新闻、法律科普、举报或反毒提醒，未提供交易路径", "effect": "downgrade_or_pass"},
            {"name": "医疗合规语境", "condition": "正规药品说明、治疗、康复、戒毒经验分享且无非法获取路径", "effect": "downgrade_or_pass"},
        ],
    },
    "gambling": {
        "schema_version": "1.0",
        "id": "gambling",
        "title": "赌博博彩风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS + [
            {
                "title": "中华人民共和国刑法 第三百零三条",
                "url": "https://www.gov.cn/guoqing/2021-10/29/content_5647622.htm",
                "notes": "支撑赌博、开设赌场、组织网络赌博、资金结算和代理推广等边界。",
            },
        ],
        "audit_goal": "识别赌博、博彩、私彩、盘口、上分、提现、投注平台、代理推广、群聊导流和资金交易风险",
        "output_labels": ["赌博交易", "投注平台导流", "上分提现", "代理推广", "盘口赔率", "资金结算"],
        "risk_definition": {
            "included": [
                "投注、盘口、赔率、下注、上分、提现、结算、刷流水或资金盘",
                "投注平台、App、开户链接、二维码、群入口、代理、会员发展或返佣",
                "代投、包赢、稳赚、回血、返水、带飞、房卡、车队等引导参与话术",
                "评论或直播互动组织用户下注、加群、私聊、充值或提现",
            ],
            "excluded": [
                "普通棋牌/桌游/游戏娱乐、体育赛事讨论、概率数学科普",
                "新闻反赌、风险提示、亲友间无金钱交易的娱乐表达",
                "游戏抽卡、赛事预测或虚拟玩法讨论但无投注和资金路径",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "投注平台/群入口 + 投注资金路径",
                    "required_evidence": ["platform_or_group", "betting_or_payment"],
                    "description": "出现投注平台、App、二维码、群入口或代理，同时存在下注、充值、上分、提现、赔率、返水或结算路径。",
                }
            ],
            "medium": [
                {
                    "name": "赌博黑话 + 私域引导",
                    "required_evidence": ["keyword", "contact_or_private_message"],
                    "description": "出现上分、盘口、回血、车队、带飞等黑话，并引导私信、加群、看主页或联系作者。",
                }
            ],
            "low": [
                {
                    "name": "娱乐元素无资金路径",
                    "required_evidence": ["game_or_sports_element"],
                    "description": "仅出现棋牌、体育比赛、游戏筹码、抽卡等元素，缺少金钱交易或组织参与证据。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注盘口、赔率、下注、上分、提现、结算、返水、包赢、稳赚、代理、开户链接和群入口。",
            "ocr": "关注截图、水印、二维码、赔率表、战绩图、提现图、群公告和开户链接中的平台或资金路径。",
            "asr": "关注口播中的带单、下注、上车、上分、回血、群内安排、充值提现和私聊引导。",
            "vision": "关注投注平台界面、赔率表、转账/提现截图、二维码、群聊截图、筹码或赌局场景与资金路径结合。",
            "comment": "关注求带、上车、群号、私我、问赔率/返水/提现、作者回复导流。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["反赌", "新闻", "科普", "娱乐", "桌游", "亲友", "概率", "赛事讨论"],
        },
        "evidence_rules": [
            {
                "id": "gambling_platform_payment",
                "name": "赌博平台或群入口 + 资金路径",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_ocr_or_comment", "type": "betting_platform_group_agent_or_link"},
                    {"source": "text_or_ocr_or_asr", "type": "betting_payment_recharge_withdrawal_odds_or_rebate"},
                ],
            }
        ],
        "fusion_rules": [
            "不能因棋牌、体育或抽卡元素误判；必须有投注、资金、平台、代理或组织参与证据。",
            "包赢、稳赚、返水、上分提现与私域导流共同出现时应上调风险。",
        ],
        "exemption_rules": [
            {"name": "普通娱乐/赛事讨论", "condition": "棋牌、体育、游戏或概率讨论中没有投注、平台、群入口、资金结算或代理推广", "effect": "pass_or_low"},
            {"name": "反赌新闻科普", "condition": "主旨为新闻报道、反赌宣传、被骗曝光或风险提示", "effect": "downgrade_or_pass"},
        ],
    },
    "fraud": {
        "schema_version": "1.0",
        "id": "fraud",
        "title": "诈骗风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS + [
            {
                "title": "中华人民共和国反电信网络诈骗法",
                "url": "https://www.gov.cn/xinwen/2022-09/02/content_5708113.htm",
                "notes": "支撑电信网络诈骗、钓鱼、冒充、资金诱导和互联网服务风险防控边界。",
            },
        ],
        "audit_goal": "识别诈骗话术、刷单返利、虚假投资、认证金、冒充身份、钓鱼链接、私域导流和转账诱导风险",
        "output_labels": ["诈骗话术", "刷单返利", "虚假投资", "冒充身份", "钓鱼链接", "转账诱导", "敏感信息索取"],
        "risk_definition": {
            "included": [
                "保证金、认证金、解冻费、手续费、刷流水、垫付任务款或安全账户转账",
                "稳赚不赔、高额返利、内部渠道、空投私募、老师带单、快速翻倍等虚假承诺",
                "冒充公检法、银行、客服、平台官方、亲友、名人、企业或公益机构",
                "索取验证码、支付密码、银行卡、身份证、人脸认证、账号密码等敏感信息",
                "可疑链接、二维码、非官方 App、外部联系方式、私信、群入口和离站交易",
            ],
            "excluded": [
                "反诈科普、新闻报道、被骗经历曝光、举报避雷、正规招聘和透明公益求助",
                "普通商品交易、正规商业推广、金融知识分享、投资亏损复盘",
                "没有收益保证、异常费用、离站路径、收款方式或敏感信息索取的普通表达",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "资金或敏感信息执行路径",
                    "required_evidence": ["fraud_scenario", "payment_or_sensitive_info"],
                    "description": "出现转账、垫付、收款、验证码、支付密码、银行卡、钓鱼链接或可疑 App 等执行路径。",
                },
                {
                    "name": "冒充身份 + 付款/信息索取",
                    "required_evidence": ["impersonation", "payment_or_sensitive_info"],
                    "description": "冒充官方、客服、银行、公检法、亲友或名人，并诱导付款或提交敏感信息。",
                },
            ],
            "medium": [
                {
                    "name": "高风险场景 + 离站导流",
                    "required_evidence": ["high_risk_offer", "contact_or_group"],
                    "description": "兼职、贷款、投资、返利、公益等场景存在夸张承诺或资质不明，并引导私信、加群、外链或扫码。",
                }
            ],
            "low": [
                {
                    "name": "弱风险词无链路",
                    "required_evidence": ["weak_keyword"],
                    "description": "只出现赚钱、投资、兼职、求助、返利等词，缺少欺骗手法和获利链路。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注收益承诺、异常费用、冒充身份、私信加群、外链、下载 App、验证码/银行卡/密码索取。",
            "ocr": "关注聊天截图、转账截图、收益图、证书、公文、二维码、链接、App 下载页和收款信息。",
            "asr": "关注口播中的带单、任务、返利、认证、解冻、安全账户、私聊、扫码、下载和付款指令。",
            "vision": "关注伪造官方界面、转账/提现/收益截图、二维码、群聊截图、证书、公文和可疑 App 界面。",
            "comment": "关注私我、加群、领取任务、名额有限、老师带、怎么做、作者回复导流。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["反诈", "被骗", "曝光", "避雷", "新闻", "科普", "正规招聘", "官方渠道", "风险提示"],
        },
        "evidence_rules": [
            {
                "id": "fraud_execution_path",
                "name": "诈骗执行路径",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_ocr_or_asr", "type": "fraud_scenario_or_false_promise_or_impersonation"},
                    {"source": "text_or_ocr_or_comment", "type": "payment_sensitive_info_link_app_or_contact"},
                ],
            }
        ],
        "fusion_rules": [
            "核心不是出现赚钱/投资/兼职词，而是欺骗或误导 + 受害者行动路径 + 获利链路。",
            "明确出现转账、垫付、收款、验证码、支付密码、安全账户、钓鱼链接或可疑 App 时通常 high。",
        ],
        "exemption_rules": [
            {"name": "反诈/被骗经历", "condition": "内容主旨为反诈科普、新闻曝光、被骗经历、举报避雷或风险提示", "effect": "downgrade_or_pass"},
            {"name": "正规交易/招聘/公益", "condition": "主体、价格、职位、用途或官方渠道相对透明，未诱导绕平台转账或提交敏感信息", "effect": "pass_or_low"},
        ],
    },
    "soft": {
        "schema_version": "1.0",
        "id": "soft",
        "title": "色情低俗风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS,
        "audit_goal": "识别色情低俗、身体隐私部位暴露或凸显、性暗示姿势动作、色情资源导流和低俗互动风险",
        "output_labels": ["色情低俗", "隐私部位暴露", "性暗示动作", "色情资源导流", "低俗互动"],
        "risk_definition": {
            "included": [
                "生殖器、肛门、女性乳头及乳晕、臀部等隐私部位明确暴露或透视走光",
                "刻意局部聚焦胸部、臀部、裆部、大腿根等敏感部位，弱化人物整体或场景",
                "撩衣、掀裙、拉低领口、抚摸隐私部位、趴伏、撅臀、双腿张开、床上俯拍等强性暗示动作",
                "色情资源、付费群、外部色情平台、联系方式、二维码、私信引导或评论低俗互动且作者迎合",
            ],
            "excluded": [
                "正常穿搭、泳池海边、运动健身、舞蹈训练、旅游记录、赛事活动、艺术写真、服装展示",
                "新闻报道、科普教学、举报、吐槽、批判低俗内容或反诈/警示语境",
                "游戏角色、商品展示、日常场景中无色情资源、联系方式、购买路径或私信导流的内容",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "隐私暴露或色情导流",
                    "required_evidence": ["visual_or_text", "clear_sexual_or_resource_path"],
                    "description": "出现隐私部位暴露、透视走光、强性暗示或色情交易/资源导流等明确证据。",
                }
            ],
            "medium": [
                {
                    "name": "擦边画面 + 附加风险信号",
                    "required_evidence": ["visual", "text_or_comment_or_ocr"],
                    "description": "画面存在局部聚焦、诱导姿势或私密场景，同时出现暧昧文案、评论低俗互动或导流暗示。",
                }
            ],
            "low": [
                {
                    "name": "边界擦边但证据弱",
                    "required_evidence": ["visual_or_keyword"],
                    "description": "露肤较多或存在擦边词，但缺少强性暗示、敏感部位暴露、导流或作者迎合互动。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注标题、正文中的色情资源、福利、想看更多、私聊、付费群、暧昧引导和话题引流语境。",
            "ocr": "关注封面、字幕、水印、聊天截图、二维码旁文字中的资源、联系方式、外链、价格或私信引导。",
            "asr": "关注口播中的暧昧暗示、资源获取、私聊、付费群、外部平台或作者迎合低俗互动。",
            "vision": "关注隐私部位暴露、透视走光、局部怼拍、强性暗示姿势、私密场景摆拍和画面是否正常生活/艺术/运动语境。",
            "comment": "关注低俗互动、求资源、求私信、作者置顶/点赞/回复迎合、外部联系方式或群入口。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["穿搭", "运动", "健身", "舞蹈", "旅游", "海边", "泳池", "写真", "科普", "举报", "吐槽", "新闻"],
        },
        "evidence_rules": [
            {
                "id": "soft_visual_plus_signal",
                "name": "擦边画面 + 附加风险信号",
                "risk_level": "medium",
                "logic": "AND",
                "conditions": [
                    {"source": "vision", "type": "sexualized_pose_focus_exposure_or_private_scene"},
                    {"source": "text_or_ocr_or_comment", "type": "ambiguous_invitation_resource_contact_or_author_engagement"},
                ],
            },
            {
                "id": "soft_resource_path",
                "name": "色情资源导流路径",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_ocr_or_comment", "type": "sexual_resource_or_service"},
                    {"source": "text_or_ocr_or_comment", "type": "contact_payment_group_private_message_or_external_platform"},
                ],
            },
        ],
        "fusion_rules": [
            "不能仅因露肤、泳衣、短裙、紧身、擦边词或游戏角色判违规；必须综合场景、镜头、动作、文案、评论和导流。",
            "图片/关键帧 risk_items 如果只是穿着暴露或露肤多，融合阶段必须重新校准，不得直接沿用为 medium/high。",
        ],
        "exemption_rules": [
            {"name": "正常生活/运动/艺术", "condition": "穿搭、运动、旅游、舞蹈、赛事、写真或艺术展示中姿态自然、镜头整体、无导流", "effect": "pass_or_low"},
            {"name": "批判/举报/科普", "condition": "主旨为新闻、科普、举报、吐槽、批判低俗内容且未提供资源获取路径", "effect": "downgrade_or_pass"},
        ],
    },
    "prohibited": {
        "schema_version": "1.0",
        "id": "prohibited",
        "title": "违禁引流风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS,
        "audit_goal": "识别私域导流、非法交易入口、管制物品、灰产服务、绕平台交易和规避平台治理风险",
        "output_labels": ["违禁引流", "非法交易入口", "管制物品", "灰产服务", "绕平台交易"],
        "risk_definition": {
            "included": [
                "联系方式、二维码、外链、群入口、私信暗号、主页引导或第三方平台水印",
                "导流对象关联色情、赌博、诈骗、涉毒、暴恐、管制物品、假证、账号灰产、违规服务等高风险场景",
                "绕平台收款、发货、资源获取、接头、售卖或规避审核治理",
            ],
            "excluded": [
                "正规客服、官方渠道、合法商品售后、透明商家信息",
                "新闻曝光、风险提示、举报避雷、公益求助或平台内正常分享链接",
                "普通社交联系方式但无违禁交易对象或高风险场景",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "导流动作 + 高风险交易对象",
                    "required_evidence": ["contact_or_link", "prohibited_goods_or_service"],
                    "description": "联系方式、二维码、群入口、私信或外链与违法违规商品、资源、服务或灰产交易共同出现。",
                }
            ],
            "medium": [
                {
                    "name": "强私域引导 + 灰产暗示",
                    "required_evidence": ["private_traffic", "resource_or_trade_hint"],
                    "description": "强私信、看主页、加群、暗号回复等导流，结合资源、收益、交易、灰产或规避表达。",
                }
            ],
            "low": [
                {
                    "name": "普通联系方式",
                    "required_evidence": ["contact_only"],
                    "description": "仅出现联系方式、客服或店铺信息，缺少违禁交易对象。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注私信、看主页、加群、暗号、外链、绕平台交易、资源、管制物品、灰产服务和交易对象。",
            "ocr": "关注图片/视频里的二维码、水印、联系方式、群公告、价格表、外链、聊天截图和主页引导。",
            "asr": "关注口播中的私聊、扫码、加群、看主页、暗号回复、资源获取、绕平台交易和发货收款。",
            "vision": "关注二维码、聊天截图、交易截图、管制物品、账号灰产材料、第三方平台界面和水印。",
            "comment": "关注私我、看主页、加群、暗号、求资源、作者回复、联系方式和跨平台引导。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["客服", "售后", "官方", "正规渠道", "举报", "避雷", "新闻", "风险提示", "公益"],
        },
        "evidence_rules": [
            {
                "id": "prohibited_contact_trade",
                "name": "违禁对象 + 导流路径",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_ocr_or_comment", "type": "contact_link_group_private_message_or_external_platform"},
                    {"source": "text_or_ocr_or_vision", "type": "prohibited_goods_service_resource_or_gray_trade"},
                ],
            }
        ],
        "fusion_rules": [
            "联系方式本身不是违规，必须判断导流对象和交易性质；普通客服、官方渠道、合法售后应降级或通过。",
            "导流对象若关联色情、赌博、诈骗、涉毒、暴恐、管制物品或账号灰产，应上调风险。",
        ],
        "exemption_rules": [
            {"name": "正规客服/官方渠道", "condition": "联系方式服务于合法商品、售后、官方渠道或透明商家信息", "effect": "pass_or_low"},
            {"name": "新闻曝光/举报避雷", "condition": "主旨为曝光、举报、避雷、新闻或风险提示，未鼓励交易", "effect": "downgrade_or_pass"},
        ],
    },
    "hate": {
        "schema_version": "1.0",
        "id": "hate",
        "title": "民族意识形态风险",
        "version": "1.0.0",
        "source_refs": COMMON_SOURCE_REFS,
        "audit_goal": "识别民族、宗教、族裔、种族、肤色、国籍、地域出身、语言文字群体相关的意识形态风险，包括仇恨歧视、排斥污名、跨群体婚恋/交往排斥、暴力煽动、恶性谣言和极端主义宣扬",
        "output_labels": ["民族意识形态风险", "歧视贬损", "跨群体婚恋排斥", "暴力煽动", "恶性谣言", "极端主义宣扬", "仇恨符号"],
        "risk_definition": {
            "included": [
                "基于民族、宗教、族裔、种族、肤色、国籍、地域出身、语言文字群体的攻击、羞辱、贬损或排斥",
                "非人化、低等化、污名化，或鼓励歧视性抵制、隔离、剥夺权利",
                "基于民族、宗教、地域出身或语言身份反对跨群体婚恋、交往、合作或民族融合，并进行泛化排斥、嘲讽或污名化",
                "号召攻击、驱逐、清除、灭绝、骚扰、举报轰炸或线下围堵",
                "针对群体的恶性谣言、仇恨符号、极端主义/仇恨组织宣传、招募或筹款",
            ],
            "excluded": [
                "民族文化、宗教知识、语言文字、服饰饮食、音乐舞蹈、节日习俗介绍",
                "新闻历史、学术讨论、政策分析、反歧视、反极端主义、举报或批判仇恨内容",
                "群体自我表达、温和自嘲、姓名、昵称、店名、水印、祝福语、普通宗教问候",
            ],
        },
        "risk_patterns": {
            "high": [
                {
                    "name": "受保护群体 + 暴力/清除号召",
                    "required_evidence": ["protected_group_reference", "violence_or_exclusion_call"],
                    "description": "明确指向受保护群体，并号召攻击、驱逐、清除、灭绝、剥夺权利或组织骚扰。",
                },
                {
                    "name": "极端组织或仇恨宣传",
                    "required_evidence": ["symbol_or_slogan", "praise_recruitment_or_fundraising"],
                    "description": "传播极端主义/仇恨组织口号、符号、战果、招募或筹款，并呈正向宣扬。",
                },
            ],
            "medium": [
                {
                    "name": "群体贬损或恶性刻板印象",
                    "required_evidence": ["protected_group_reference", "degrading_claim_or_slur"],
                    "description": "对群体使用强贬义黑称、侮辱宗教神圣对象，或宣称该群体肮脏、低智、危险、不配享有资源。",
                },
                {
                    "name": "跨群体交往/婚恋排斥",
                    "required_evidence": ["protected_group_reference", "intergroup_relationship_exclusion"],
                    "description": "基于民族、宗教、地域或语言身份表达不能嫁娶、不该通婚、不要来往、应远离、都不可靠，或嘲讽民族融合。",
                }
            ],
            "low": [
                {
                    "name": "容易引战但证据不足",
                    "required_evidence": ["ambiguous_reference"],
                    "description": "含蓄排外、历史影射、争议字幕或疑似黑话，但上下文不足以确认攻击意图。",
                }
            ],
        },
        "modality_guidance": {
            "text": "关注是否指向受保护群体，以及贬损、非人化、排斥、恶性谣言、暴力号召或极端主义招募。",
            "ocr": "关注字幕、海报、截图、旗帜、标语、水印中的群体称谓、侮辱语、仇恨符号、攻击口号和组织信息。",
            "asr": "关注口播中的群体攻击、排斥号召、恶性谣言、极端主义宣扬和反讽/批判语境。",
            "vision": "关注仇恨符号、旗帜、手势、群体形象被侮辱或非人化的画面，同时区分正常文化宗教场景。",
            "comment": "关注评论区群体攻击、接龙羞辱、组织骚扰、作者置顶/点赞/回复迎合或纠偏；民族关系/婚恋场景中，普通个人顾虑不直接判高危，基于身份的泛化排斥、反对通婚、嘲讽融合判中危，驱逐隔离、暴力威胁、恶性谣言或非人化判高危。",
        },
        "keywords": {
            "exact": [],
            "fuzzy": [],
            "negative_context": ["文化", "宗教知识", "旅行", "历史", "学术", "新闻", "反歧视", "反极端", "祝福", "姓名", "店名"],
        },
        "evidence_rules": [
            {
                "id": "hate_group_attack",
                "name": "受保护群体 + 攻击/排斥",
                "risk_level": "medium",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_ocr_or_asr_or_comment", "type": "protected_group_reference"},
                    {"source": "text_or_ocr_or_asr_or_comment", "type": "slur_degrading_claim_exclusion_or_violence_call"},
                ],
            },
            {
                "id": "hate_extremist_recruitment",
                "name": "极端/仇恨组织正向传播",
                "risk_level": "high",
                "logic": "AND",
                "conditions": [
                    {"source": "text_or_ocr_or_vision", "type": "extremist_or_hate_symbol_slogan_or_group"},
                    {"source": "text_or_asr_or_comment", "type": "praise_recruitment_fundraising_or_action_call"},
                ],
            },
        ],
        "fusion_rules": [
            "核心是是否基于受保护身份攻击人或群体；不能因民族服饰、宗教文字、姓名、祝福语、语言文字本身判风险。",
            "OCR/ASR 译文若语义生硬或像机器误译，不得作为 medium/high 的唯一依据；需要标题、正文、语音、评论或多个时间段共同支持。",
        ],
        "exemption_rules": [
            {"name": "文化宗教正常表达", "condition": "民族文化、宗教知识、语言文字、服饰饮食、音乐舞蹈、节日习俗或旅行生活记录", "effect": "pass"},
            {"name": "新闻历史/反歧视", "condition": "新闻、历史、学术、政策讨论、反歧视、反极端主义、举报或批判仇恨内容", "effect": "downgrade_or_pass"},
            {"name": "姓名/祝福/水印", "condition": "仅出现姓名、昵称、店名、水印、普通宗教祝福或问候，无攻击谓词", "effect": "pass"},
        ],
    },
}


DEFAULT_KNOWLEDGE_PACKAGES["minority"] = {
    **deepcopy(DEFAULT_KNOWLEDGE_PACKAGES["hate"]),
    "id": "minority",
    "title": "民族意识形态风险",
}


def get_default_knowledge_package(category_id: str) -> dict | None:
    package = DEFAULT_KNOWLEDGE_PACKAGES.get(category_id)
    return deepcopy(package) if package else None
