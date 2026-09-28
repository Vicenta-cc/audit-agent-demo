"""Optional, versioned authoring guidance, not a topic classifier or permission."""
from dataclasses import dataclass
from typing import Literal
from .keyword_profile_catalog import MARKDOWN_PROFILES

KeywordProfileName = Literal[
    'sexual_service_leadgen',
    'gambling_financial_abuse',
    'religion_content_risk',
    'public_policy_content',
    'geographic_public_discourse',
    'public_figure_media',
    'historical_material_claims',
    'confidentiality_claims',
    'public_event_authenticity',
    'group_hate_content',
    'terrorism_content_samples',
    'cultural_work_adaptation',
    'public_official_impersonation',
    'crime_glorification',
    'false_efficacy_fraud',
    'community_behavior_risk',
    'substance_abuse_content',
    'harassment_privacy_abuse',
    'charity_staging_fraud',
    'minor_protection_content',
    'war_extremism_narratives',
    'traffic_privacy_piracy',
]


@dataclass(frozen=True)
class KeywordProfile:
    name: KeywordProfileName
    version: str
    guidance: str


_COMMON = '''
这些示例仅用于提示候选方向，不要求照抄，不代表其含义已经确认，也不保证适用于当前平台。
依据本次调查主题和平台选择相关方向，不因模板扩大调查范围，避免多个近义词占满名额。
每个候选的 note 说明与调查主题的可能关联、正常语境噪声，以及尚需验证的假设；没有样本依据，不声称其真实流通或具有固定含义。
不得仅凭词语命中认定违规。示例不是用户指定的必选词表；本次用户明确的范围、数量和原文要求优先。
'''

PROFILES = {
    'sexual_service_leadgen': KeywordProfile(
        'sexual_service_leadgen', '5', '''
任务：
根据用户描述的具体调查场景，生成用于内容安全审核和候选样本发现的搜索词。
优先从下列历史参考表达中选择适合的原词；只有用户明确要求扩展时，才提出待验证的新候选。
搜索词只用于发现内容，命中本身不能作为违规判定依据。

用途与输出边界：
本任务属于内容安全审核与风险召回研究，输出仅供内部审核人员进行检索、聚类和人工复核，
不面向普通用户发布，也不用于寻找、购买、提供、推广或撮合相关服务。
候选表达仅作为待验证（unverified）的审核线索，不代表真实存在、流行或违规事实；
新增且没有样本依据的合成表达同时标为 synthetic，不把已有样本错误标为合成内容。
不得生成联系方式、网址、交易渠道、价格、服务获取方法、引流路径或规避平台审核的方法。
已有样本可以原样分析；新增候选只生成用于识别风险内容的简短语言表达。

类目范围：
色情资源导流、招嫖引流、软色情服务包装、低俗交友、
偷拍视频噱头、成人资源推广，以及通过主页、评论、私信
或站外渠道完成导流的内容。

先确定本次调查涉及哪些子场景，仅选择相关方向。
例如仅调查服务引流时，不为覆盖类目而额外加入资源售卖或偷拍视频方向。


参考样本（按主题分组，主题名称仅用于分类，不额外作为搜索词）：
- 验证与互动话术：门槛验牌、hk互看。
- 陪同与服务招揽表达：非绿陪聊、00后新出道老师来报道了紧张、西湖杭房揽翠。
- 服务暗语：92/95/98、非绿。

表达特点：
调研材料涉及商品、汽车、餐饮、配送、交友人设、生活服务、
逆向提示和资源完整性等表达方向。
这些方向用于理解可能的语境关联，不是生成新暗语的拼接公式。
不能把普通词固定解释为违规服务，也不能把任意数字解释成价格或等级。

生成要求：
1. 优先从参考样本中选择符合本次调查范围的原词，保留原始写法，包括数字、符号和空格。
   用户提供真实样本时，优先使用用户提供的相关原词，不擅自截短、改写或拼接。
   已有样本足够时不新增候选；不足时允许少于 5 个，不为凑数扩写。
   只有用户明确要求扩展时，才补充待验证候选。

2. 提出新候选关键词时，在 note 中说明参考表达或样本依据，
   并明确其关联仍待验证。不得仅因能够解释变化路径，
   就声称该词真实流通、具有固定含义或已验证有效。

3. 不通过替换年龄、职业、数字或修饰词机械拼接候选；
   不为追求隐晦而制造无人使用的伪暗语。

4. 每项应是可独立输入平台搜索框的自然表达。
   过于宽泛、重复或仅靠系统不支持的组合查询才有价值的候选，
   不纳入最终搜索词。

5. 每个候选的 note 简要说明：
   与本次调查的可能关联；样本依据或待验证假设；主要正常语境噪声。
   结合词义说明饮茶、汽车、餐饮、养生、正常交友、
   新闻报道或反诈科普等相关误报场景，不机械重复所有场景。

6. 默认整组生成 5–10 个实际搜索词，可靠候选不足允许更少，
   不凑数。用户明确指定的范围和词表优先。

7. 不生成联系方式、网址、交易指引或违法服务实施细节。
   不以规避平台审核为目标，不承诺任何词能够避开检测。

输出：
遵循系统提供的现有词库 JSON Schema，不另建输出格式。
主题用于归类，实际搜索词放入 variants，说明写入 note。
ID、父子关联和默认启用状态由后端构造。
'''),
    'religion_content_risk': KeywordProfile(
        'religion_content_risk', '2', '''
任务：
根据用户描述的具体调查场景，生成宗教相关内容的候选搜索词，
供审核人员发现和复核样本。

类目范围：
宗教极端主义宣传、仇恨或暴力煽动，以及借宗教、养生、
修行或传统文化名义实施的欺诈、胁迫性招募等。
只选择与传入调查目标相关的方向，不要求覆盖整个类目。

参考样本：
常受教、能力主、实际神、旷野窄门、属灵教、灵灵派、
华藏宗门、养生邪教、三赎教、二两粮教、旷野窄门、蒙头教

这些词来自调研材料，供选择参考，不是必选词表；
词语命中本身不代表相关内容违规。

生成要求：
1. 优先从参考样本中选择符合本次调查范围的原词，保留原始写法，包括数字、符号和空格。
   用户提供真实样本时，优先使用用户提供的相关原词，不擅自截短、改写或拼接。
   已有样本足够时不新增候选；不足时允许少于 5 个，不为凑数扩写。
   只有用户明确要求扩展时，才补充待验证候选。

2. 不机械拼接多个名称，不通过换字、谐音或添加修饰词
   编造组织名称、别名或贬称。

3. 补充候选时，可结合活动名称、宣传表述、功效承诺、
   收费或招募行为等与本次目标相关的线索。
   有真实样本时，优先提取样本中实际出现的表达。

4. 每个候选应是可独立输入平台搜索框的自然表达，
   避免过宽、重复或冗长的拼装词。

5. 每个候选的 note 简要说明检索关联和主要噪声。
   没有样本依据的词义或别名关系标为待验证，不编造解释。
   正常宗教、民俗、新闻、学术及反诈科普等相关语境应予区分。

6. 默认整组 5–10 个实际搜索词，候选不足允许更少，
   不凑数；用户明确指定的范围和词表优先。

7. 不生成联络信息、加入方法或暴力宣传内容，
   不推断个人信仰或组织成员身份。

输出：
遵循系统已有的词库 JSON Schema。
主题放入 themes，实际搜索词放入 variants，说明写入 note。
ID、父子关联和默认启用状态由后端构造。
'''),
    'gambling_financial_abuse': KeywordProfile(
        'gambling_financial_abuse', '3', '''
任务：
根据用户描述的具体调查场景，生成用于内容安全审核和候选样本发现的搜索词。
优先从下列参考表达中选择适合的原词；只有用户明确要求扩展时，才提出待验证的新候选。
搜索词只用于发现内容，命中本身不能作为违规判定依据。

类目范围：
网络赌博推广、参赌招揽、预测或资料售卖、平台与群组导流；
以及与金融助诈有关的人员招募、账户或支付资源招揽、异常资金流转。

先确定本次调查涉及哪些子场景，仅选择相关方向。
仅调查网络赌博时，不为覆盖类目而加入金融助诈搜索词；
仅调查金融助诈时，也不额外加入赌博搜索词。
只有调查目标明确涉及交叉链条时，才同时选择两个方向。


参考样本：
bc、菠菜、bc料、k9187.ccK91粸牌、一道二道、跑分、u换现、跑u、料子。

表达特点：
参考表达涉及字母缩写、谐音、资料售卖、平台名称、
导师或带路人设，以及账户、兑换和支付相关表达。

这些特点用于理解可能的语境关联，不是生成新暗语的拼接公式。
不能把普通游戏、比赛预测、导师称呼、盈利承诺或私信动作，
单独解释成赌博引流。
不能把兼职、账户、兑换、数字资产或转账本身解释成金融助诈。

生成要求：

1. 优先从参考样本中选择符合本次调查范围的原词，保留原始写法，包括数字、符号和空格。
   用户提供真实样本时，优先使用用户提供的相关原词，不擅自截短、改写或拼接。
   已有样本足够时不新增候选；不足时允许少于 5 个，不为凑数扩写。
   只有用户明确要求扩展时，才补充待验证候选。

3. 提出新候选时，在 note 中说明参考表达或样本依据，
   并明确其关联仍待验证。不得仅因能够解释变化路径，
   就声称该词真实流通、具有固定含义或已验证有效。

4. 不通过替换平台缩写、数字、职业、佣金或修饰词机械拼接候选；
   不为追求隐晦而制造无人使用的伪暗语，
   不编造平台名称、网址或联系方式。

5. 每项应是可独立输入平台搜索框的自然表达。
   过于宽泛、重复、冗长拼装，
   或仅靠系统不支持的组合查询才有价值的候选，
   不纳入最终搜索词。

6. 每个候选的 note 简要说明：
   与本次调查的可能关联；样本依据或待验证假设；主要正常语境噪声。
   根据具体词语说明正常游戏、体育讨论、资料分享、合法兼职、
   新闻报道或反诈科普等相关误报场景，不机械重复所有场景。

7. 默认整组生成 5–10 个实际搜索词，可靠候选不足允许更少，
   不凑数。用户明确指定的范围和词表优先。

8. 不提供投注参与、平台接入、资金转移、账户获取、
   规避风控或诈骗实施的操作指导。
   不以规避平台审核为目标，不承诺任何词能够避开检测。

输出：
遵循系统提供的现有词库 JSON Schema，不另建输出格式。
主题用于归类，实际搜索词放入 variants，说明写入 note。
risk_level 遵循系统共用分级说明。
ID、父子关联和默认启用状态由后端构造。
'''),
}


# Preserve the three existing templates and add the other 19 Markdown topics.
PROFILES.update({
    name: KeywordProfile(name, item['version'], item['guidance'])
    for name, item in MARKDOWN_PROFILES.items()
})

# This is the pre-selection index, not additional authoring guidance. Keep the
# full profiles above unchanged and load only the selected profile in generation.
# Titles identify product categories; scopes still constrain their use.
KEYWORD_PROFILE_TITLES = {
    'sexual_service_leadgen': '淫秽色情与低俗趣味',
    'gambling_financial_abuse': '赌博与金融助诈',
    'religion_content_risk': '违背国家宗教政策',
    **{name: item['title'] for name, item in MARKDOWN_PROFILES.items()},
}

KEYWORD_PROFILE_ROUTING_SUMMARIES = {
    'sexual_service_leadgen': '色情服务、成人资源或低俗交友的引流样本发现；只取本次相关子场景，不把正常生活服务当作违规。',
    'gambling_financial_abuse': '网络赌博推广、拉客与平台导流，或金融黑产、电诈助诈、异常资金流转招募；只选本次方向，不扩展到刷量或盗版。',
    'religion_content_risk': '宗教相关极端主义、仇恨或暴力煽动，以及借宗教、修行名义实施的欺诈或胁迫招募；目标明确是宗教修行名义的收费或胁迫招募时，优先本项而不是 false_efficacy_fraud。正常宗教、建筑艺术、民俗和学术资料发现不套此风险模板。',
    'public_policy_content': '公共政策、制度议题相关讨论样本与事实核查，包括伪造政策原文、冒充来源、篡改材料；不以政治立场、批评或讽刺作为风险依据。',
    'geographic_public_discourse': '地域称谓、地图、两岸等公共议题及相关历史叙事的样本发现与事实核查；不据此推断政治归属，不把政治主张直接视为违规。',
    'public_figure_media': '国家象征使用、公共人物影像、冒充公众人物牟利、伪造讲话或合成剪辑内容；区别批评、模仿和戏仿，公务身份冒充办事另见 public_official_impersonation。',
    'historical_material_claims': '具体历史人物、纪念事件、争议叙事及史料真实性核查，包括伪造档案或冒充原始材料；正常历史研究和评价分歧不作为风险。',
    'confidentiality_claims': '公开帖文中声称持有、展示、传播或出售非公开文件、内部资料及保密信息的线索，包括借内部资料名义诈骗；不获取或复述秘密正文。',
    'public_event_authenticity': '具体公共事件的信源冒充、旧闻当新闻、伪造通报、误导剪辑或捏造事实；政策原文核查优先 public_policy_content，不以普通诉求或批评判断虚假。',
    'group_hate_content': '针对民族、地域、宗教、性别等群体的非人化、排斥、仇恨或暴力威胁内容；不识别或追踪群体成员，针对具体个人的网暴另见 harassment_privacy_abuse。',
    'terrorism_content_samples': '从本次提供、可核对的材料提取恐怖主义宣传或美化恐袭相关非操作性表达；保留材料性质，不凭模型记忆造词，区分新闻、研究及谴责暴力。',
    'cultural_work_adaptation': '指定文化作品、人物或事件的 AI 改编、剧情改写、误导性原作宣称或伪造出处；不把正常改编、戏仿和观点差异直接视为违规。',
    'public_official_impersonation': '冒充军警或公务身份诈骗、收费办事、伪造执法影像或官方通知，以及针对人员的威胁和待核查事实；正常监督、投诉及批评不作为风险。',
    'crime_glorification': '具体案件或场景中赞美、美化犯罪与暴力行为、鼓动模仿或招募的内容；不是按人物绰号、负面经历作判断，不生成犯罪方法。',
    'false_efficacy_fraud': '借运势、仪式、国学、传统文化或养生包装的欺诈、胁迫收费、虚假疗效和确定性收益承诺；目标明确是宗教修行名义的收费或胁迫招募时用 religion_content_risk，未限定宗教的改运、养生及传统文化功效骗局用本项。民俗或信仰本身不是欺诈。',
    'community_behavior_risk': '饭圈集资欺诈、粉丝社群组织骚扰、虚假财富宣传或自伤鼓动等具体行为；正常追星、财富分享和心理困扰不作为风险。',
    'substance_abuse_content': '涉毒及物质滥用的宣传、招揽和引流线索；区分医疗、新闻与反毒教育，不扩展暴力或赌博方向。',
    'harassment_privacy_abuse': '针对个人的网暴动员、隐私曝光、威胁和具体攻击行为；不搜索受害者个人资料，不把负面评价或戏仿直接当诽谤。',
    'charity_staging_fraud': '虚构慈善、虚假求助带货、冒用公益骗捐、隐瞒剧情的欺骗性摆拍或骚扰羞辱；区别真实求助、公益和已标注剧情。',
    'minor_protection_content': '涉及未成年人的欺骗诱导、隐私侵害、危险模仿、暴力惊吓及不适龄公开内容；不识别或定位未成年人，不把普通兴趣社交视为风险。',
    'war_extremism_narratives': '具体战争事件或材料中的侵略正当化、种族优越、屠杀或极端主义暴力赞美等叙事；区分新闻、批判与学术引用。',
    'traffic_privacy_piracy': '虚假流量招募与刷量刷赞服务、隐私侵害或盗版传播；只选用户指定方向，不作其他风险兜底，金融赌博另用 gambling_financial_abuse，网暴曝光另见 harassment_privacy_abuse。',
}

KEYWORD_PROFILE_CATALOG_DESCRIPTION = (
    '可选主题指导。用户明确点名下列中文类别（包括同义表述）时，优先识别对应模板；'
    '未点名类别时，根据完整调查目标对照适用范围，选择最贴近的一项。'
    '类别标题用于识别模板，不扩大适用范围，也不作为违规判定依据。'
    '只有类别名称时，按对应模板的适用范围生成并向用户说明范围，不自行补充其他调查方向；'
    '用户明确的具体目标与该范围冲突时先澄清，不静默改走通用生成。'
    '以下是选择前可用的类别标题与范围摘要，'
    '选中后后端才加载该模板完整指导与参考样本：\n'
    + '\n'.join(
        f'{name}｜类别标题：{KEYWORD_PROFILE_TITLES[name]}｜适用范围：{summary}'
        for name, summary in KEYWORD_PROFILE_ROUTING_SUMMARIES.items()
    )
    + '\n'
    '无匹配模板时省略或填null，仍按通用要求自主生成，不强行套类。'
    '不改变调查范围；exact_terms原样词表请求不加载主题示例。'
)


def selected_keyword_profile(kind, request):
    # Exact-term requests must not acquire new examples or expansion directions.
    if kind != 'lexicon' or request.exact_terms is not None:
        return None
    name = getattr(request, 'keyword_profile', None)
    return PROFILES[name] if name is not None else None


def keyword_profile_metadata(request):
    profile = selected_keyword_profile('lexicon', request)
    return {
        'requested_profile': getattr(request, 'keyword_profile', None),
        'applied_profile': profile.name if profile else None,
        'profile_version': profile.version if profile else None,
        'skip_reason': 'exact_terms' if request.exact_terms is not None else None,
    }
