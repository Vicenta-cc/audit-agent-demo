"""Read-only tools for published one-to-five-post unified audit reports."""

from __future__ import annotations

import json
from copy import deepcopy

from .pass_support import PassReportToolService, pass_tool_schemas
from .schemas import REPORT_STATISTICS_DESCRIPTION
from .service import (
    ToolInputError,
    _require_exact_keys,
    _require_string_list,
)


COMPARE_AUTHORIZED_REPORT_ACCOUNTS = {
    "name": "compare_authorized_report_accounts",
    "description": (
        "完整枚举并比较当前用户已授权的所有已发布新增统一审核报告中的稳定账号。"
        "当用户询问两份或多份新增报告是否存在共同发布者、共同评论者、共同账号，"
        "或要求列出各报告全部发布者和评论者时，直接调用本工具，不要用昵称搜索逐个拼接。"
        "结果按抖音稳定账号标识确定性计算，同时给出每份报告的完整稳定账号目录、"
        "任意角色交集、共同发布者、共同评论者以及无法稳定识别的活动数量。"
        "同昵称不会合并；结果不返回任何内部账号引用或平台身份原值。"
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


class UnifiedAuditReportToolService(PassReportToolService):
    """Expose every frozen report post without changing legacy report modes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for tool_name in (
            "search_posts",
            "list_finding_posts",
            "list_post_risk_comments",
        ):
            self._handlers.pop(tool_name, None)
        if self.account_activity is not None:
            self._handlers["compare_authorized_report_accounts"] = (
                self._dispatch_account_activity
            )

    def _read_real_report(self, session_id, args):
        if args:
            raise ToolInputError("invalid_arguments", "read_report takes no arguments")
        repo = self.repository
        posts = tuple(repo.ordered_posts())
        return self._success(
            tool="read_report",
            result_kind="unified_report_overview",
            content_state="overview_with_complete_post_previews",
            data={
                "title": repo.report.title,
                "overview": repo.report_overview,
                "statistics": {
                    "post_count": len(posts),
                    "decision_counts": repo.report.statistics.decision_counts,
                    "risk_counts": repo.report.statistics.risk_counts,
                    **repo.report_comment_statistics(),
                },
                "post_previews": self._post_cards(
                    session_id, posts, "read_report"
                ),
                "post_previews_complete_for_report": True,
                "account_entries": (
                    []
                    if self.account_activity is None
                    else self.account_activity.report_entries(session_id)
                ),
            },
            not_loaded=["full post content", "individual comments"],
            limitations=[
                "Every post conclusion is limited to the frozen audit result and its recorded rules.",
                "Use read_posts with a post preview reference before presenting full post content or detailed audit reasoning.",
            ],
        )

    def _post_cards(self, session_id, posts, source):
        posts = tuple(posts)
        cards = super()._post_cards(session_id, posts, source)
        for card, post in zip(cards, posts):
            card["audit_summary"] = self.repository.finding_for_post(post.id).summary
        return cards

    def _read_posts(self, session_id, args):
        """Allow one call to expand the unified report's complete five-post set."""

        _require_exact_keys(args, {"post_refs"}, required={"post_refs"})
        post_refs = _require_string_list(
            args["post_refs"], "post_refs", minimum=1, maximum=5
        )
        if len(post_refs) <= 4:
            return super()._read_posts(session_id, {"post_refs": post_refs})

        groups = []
        for refs in (post_refs[:4], post_refs[4:]):
            payload = json.loads(
                super()._read_posts(session_id, {"post_refs": refs})
            )
            groups.extend(payload["data"]["post_groups"])
        for position, group in enumerate(groups, 1):
            group["group_position"] = position
        return self._success(
            tool="read_posts",
            result_kind="post_detail",
            content_state="post_content_and_effective_finding",
            data={"post_groups": groups, "returned_count": len(groups)},
            not_loaded=["Evidence directories and full Evidence content"],
            limitations=[
                "Finding fields are frozen report data; no new audit rule was applied."
            ],
        )


def unified_tool_schemas():
    """Return an isolated catalog; unified reports do not need post discovery."""

    schemas = [
        schema for schema in pass_tool_schemas() if schema["name"] != "search_posts"
    ]
    schemas.append(deepcopy(COMPARE_AUTHORIZED_REPORT_ACCOUNTS))
    for schema in schemas:
        if schema["name"] == "read_report":
            schema["description"] = (
                "读取当前新增统一审核报告的概览、全部1至5条冻结帖子预览、"
                "每条帖子的审核结论与可供read_posts继续读取的引用。"
                "回答报告概况、帖子数量、通过/复审/拒绝分布或指定第几条帖子时，"
                "应先使用本工具；返回的帖子目录对当前报告完整。"
                + REPORT_STATISTICS_DESCRIPTION
            )
        elif schema["name"] == "read_posts":
            schema["description"] = (
                "读取read_report或账号活动工具已经返回引用的一篇或多篇当前报告帖子。"
                "安全、复审和拒绝帖子均可读取；返回冻结正文、作者、来源链接和既有审核结论。"
                "不得使用猜测或内部ID代替工具返回的帖子引用。"
            )
            schema["parameters"]["properties"]["post_refs"]["maxItems"] = 5
    return schemas


UNIFIED_REPORT_SYSTEM_PROMPT = """你是新增统一审核报告的问答助手。当前报告包含1至5条冻结帖子，可能同时存在审核通过、建议复审、建议拒绝或待确认结果。只使用当前会话工具返回的已授权资料，禁止自行重新审核、改变既有结论、猜测缺失内容或泄露内部标识和路径。
用户询问“这个报告的大概内容是什么”、报告概览、结果分布、有哪些帖子、哪些安全或哪些有风险时，先调用read_report。read_report返回当前报告全部帖子预览、审核决定、风险等级、审核摘要以及每条帖子的可读取引用；不得只讲风险帖而遗漏安全帖，也不得把预览扩写成尚未读取的正文。
用户要求展开“第几条帖子”“第几条安全帖”、帖子正文、ASR、翻译、完整审核理由或多个帖子的比较时，先从read_report返回的完整目录按报告顺序和审核决定确定对应引用，再调用read_posts。当前报告最多5条，不使用主题发现工具；需要按主题判断时，可以读取相关帖子，证据不足就明确说明，不能拿不相关帖子补足。
解释为什么通过、复审或拒绝，必须归因于read_posts返回的原审核说明及可核对材料。需要材料目录时调用list_evidence，需要具体材料原文时调用read_evidence；没有独立材料时明确说明结论依据仅为原审核说明，不能伪造Finding或Evidence。所有评论使用list_post_comments；未审核或审核失败的评论不等于无风险，帖子风险不能传递给评论。
询问评论统计时，重新调用read_report取得当前汇总。完成审核数、评论自身风险数和报告引用评论材料数含义不同，不能混用；范围限当前报告冻结帖子下的已存评论，不代表平台全部评论。
只有昵称时先调用search_accounts。唯一精确匹配可以继续查看账号概览和活动；同名或模糊匹配必须请用户选择，不能按昵称强行合并。报告内容范围与账号活动授权范围分别说明，账号没有整体审核决定，不能把帖子或评论通过说成“账号审核通过”。
用户询问两份或多份新增统一报告是否存在共同发布者、共同评论者、共同账号，或要求穷举各报告全部发布者和评论者时，必须调用compare_authorized_report_accounts。该工具已按稳定账号标识完整枚举授权新增报告，不要再用search_accounts逐个猜测或根据已读帖子自行补齐。回答时区分“任意角色共同账号”“共同发布者”“共同评论者”；如果存在无法稳定识别的活动，明确说明它们没有参与账号合并，不能按昵称强行归并。不得在回答中复述任何账号引用或稳定标识原值。
引用失效时重新读取当前报告取得入口，不猜引用。连续追问可以复用当前会话已经验证的引用和资料；需要新细节必须调用相应工具。回答使用中文、自然名称和可理解的依据，不展示工具名、内部ID、引用token、数据库路径或配置字段。"""
