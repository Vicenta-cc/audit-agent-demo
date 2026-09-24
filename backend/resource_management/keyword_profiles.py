"""Optional, versioned authoring guidance, not a topic classifier or permission."""
from dataclasses import dataclass
from typing import Literal

KeywordProfileName = Literal['sexual_service_leadgen', 'gambling_financial_abuse']


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
        'sexual_service_leadgen', '1', '''
主题指导：色情服务引流内容调查，供审核人员收集候选样本并区分正常生活服务内容。
模板提供的候选表达示例包括：“00后新老师”“非绿”“地陪”“门槛验牌”。
围绕调查主题选择候选，兼顾身份或角色描述、生活服务场景、联系与预约流程等不同表达方向。
不要通过替换年龄、职业或修饰词机械拼接新词。
不得将年龄、职业、普通服务名称本身视为违规证据。
''' + _COMMON),
    'gambling_financial_abuse': KeywordProfile(
        'gambling_financial_abuse', '1', '''
主题指导：网络赌博与金融助诈相关调查。依据本次目标选择相关方向，不要求覆盖所有方向。
网络赌博方向：模板提供的候选表达示例包括“bc”“菠菜”“bc料”，关注内容招揽、预测或资料售卖、平台或群组导流。
金融助诈方向：模板提供的候选表达示例包括“一道二道”“跑分”“u换现”，关注人员招募、账户或支付资源招揽、异常资金流转。
用户仅调查网络赌博时，不为凑数加入金融助诈候选；用户仅调查金融助诈时，不为凑数加入赌博候选。只有调查目标明确涉及交叉链条时，才同时选取两个方向。
不要机械拼接缩写、币种、职业、佣金、流程序号或修饰词。
不得将比赛预测、游戏讨论、兼职、账户、兑换、数字资产或转账本身视为违规证据。
注意“菠菜”“跑分”和字母缩写在正常语境中的歧义，以及“bc料”“一道二道”“u换现”在不同上下文中的含义不确定性。
不提供投注参与、平台接入、资金转移、账户获取、规避风控或诈骗实施的操作指导。
''' + _COMMON),
}


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
