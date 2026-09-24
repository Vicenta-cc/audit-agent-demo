"""Read-only tools for published one-to-thirty-post unified audit reports."""

from __future__ import annotations

import json
from copy import deepcopy

from backend.audit_agent.limits import MAX_SUPPORTED_INVESTIGATION_POSTS

from .pass_support import PassReportToolService, pass_tool_schemas
from .schemas import REPORT_STATISTICS_DESCRIPTION
from .service import (
    ToolInputError,
    _require_exact_keys,
    _require_int,
    _require_string_list,
)


POST_DIRECTORY_PAGE_SIZE = 10
MAX_POST_DIRECTORY_PAGES = (
    MAX_SUPPORTED_INVESTIGATION_POSTS + POST_DIRECTORY_PAGE_SIZE - 1
) // POST_DIRECTORY_PAGE_SIZE
DEFAULT_PUBLISHER_ENTRY_LIMIT = 5
DEFAULT_COMMENTER_ENTRY_LIMIT = 5


LIST_REPORT_POSTS = {
    "name": "list_report_posts",
    "description": (
        "按报告冻结顺序读取新增统一审核报告的帖子目录，每页固定最多10条。"
        "返回全局序号、标题、作者、审核决定、风险等级、审核摘要和当前会话安全引用，"
        "不返回帖子完整正文。用户明确要求全部帖子时读取所有页面；指定第几条时，"
        "可直接读取包含该序号的页面。已经在当前会话展示过的引用可以直接复用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_POST_DIRECTORY_PAGES,
                "description": "从1开始的目录页码；每页固定最多10条。",
            }
        },
        "required": ["page"],
        "additionalProperties": False,
    },
}


REPORT_QUERY_PAGING = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
    "cursor": {"type": "string", "description": "同一报告选择和角色筛选下上一页返回的续页引用。"},
}
REPORT_ACCOUNT_ROLE = {
    "type": "string", "enum": ["any", "post_author", "comment_author"], "default": "any",
    "description": "any为任意角色，post_author为发布者，comment_author为评论者。",
}
LIST_AUTHORIZED_REPORTS = {
    "name": "list_authorized_reports",
    "description": "分页读取已授权新增统一报告的标题、目录序号和会话引用，不读取账号名单。"
                   "比较前从此目录选择用户指定报告；同名或指代不明时请用户选择。此目录不改变当前报告绑定。",
    "parameters": {"type": "object", "properties": deepcopy(REPORT_QUERY_PAGING),
                   "additionalProperties": False},
}
COMPARE_AUTHORIZED_REPORT_ACCOUNTS = {
    "name": "compare_authorized_report_accounts",
    "description": "比较明确选定的2至10份已授权新增报告，仅返回共同账号总数和分页交集，不返回各报告完整账号目录。"
                   "report_refs必须来自list_authorized_reports；共同指在每份所选报告都出现，"
                   "post_author/comment_author要求在每份报告均具备该角色。默认最多10个账号，最多20个。"
                   "计数基于完整稳定账号集合；同昵称不合并，未识别活动不参与比较。"
                   "结果account_ref可继续查询账号活动，不向用户展示引用。无参数不会默认比较所有报告。",
    "parameters": {
        "type": "object",
        "properties": {
            "report_refs": {"type": "array", "items": {"type": "string"},
                            "minItems": 2, "maxItems": 10, "uniqueItems": True},
            "role": deepcopy(REPORT_ACCOUNT_ROLE), **deepcopy(REPORT_QUERY_PAGING),
        },
        "required": ["report_refs"], "additionalProperties": False,
    },
}
LIST_REPORT_ACCOUNTS = {
    "name": "list_report_accounts",
    "description": "仅当用户要查看某份报告的账号目录时使用；按所选报告和角色筛选后分页，返回完整计数。"
                   "report_ref取自list_authorized_reports，默认10个、最多20个账号。"
                   "与比较共同账号分开；account_ref可用于后续账号穿透，不展示给用户。",
    "parameters": {
        "type": "object",
        "properties": {"report_ref": {"type": "string"}, "role": deepcopy(REPORT_ACCOUNT_ROLE),
                       **deepcopy(REPORT_QUERY_PAGING)},
        "required": ["report_ref"], "additionalProperties": False,
    },
}

GET_REPORT_ACCOUNT_STATISTICS = {
    "name": "get_report_account_statistics",
    "description": "查询一份报告的账号人数、评论总数、最活跃评论者或发布者TopN时使用。"
                   "直接返回完整快照的精确统计及具体账号排名，不需要逐帖读取评论后自行计数。"
                   "不传report_ref即当前报告；其他报告引用取自list_authorized_reports。"
                   "按稳定账号身份去重，活动数降序、并列按稳定身份固定排序；最多返回top_n个具体账号，"
                   "并列人数单独注明，不用‘其余人并列’代替具体账号。无法识别身份的活动单列，账号数不含这些活动。"
                   "account_ref可继续查询账号概览和评论明细；明细仍在原已授权任务范围，不能把全域数量当本报告数量。",
    "parameters": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "report_ref": {"type": "string", "description": "省略时仅统计当前报告，绝不默认所有报告。"},
            "role": {"type": "string", "enum": ["comment_author", "post_author"],
                     "default": "comment_author", "description": "评论者按评论数排名；发布者按发帖数排名。"},
            "top_n": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
    },
}


class UnifiedAuditReportToolService(PassReportToolService):
    """Expose every frozen report post without changing legacy report modes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._handlers["list_report_posts"] = self._list_report_posts
        for tool_name in (
            "search_posts",
            "list_finding_posts",
            "list_post_risk_comments",
        ):
            self._handlers.pop(tool_name, None)
        if self.account_activity is not None:
            for name in ("compare_authorized_report_accounts", "list_authorized_reports", "list_report_accounts",
                         "get_report_account_statistics"):
                self._handlers[name] = self._dispatch_account_activity

    def _read_real_report(self, session_id, args):
        if args:
            raise ToolInputError("invalid_arguments", "read_report takes no arguments")
        repo = self.repository
        posts = tuple(repo.ordered_posts())
        first_page = posts[:POST_DIRECTORY_PAGE_SIZE]
        account_entries, account_entry_statistics = self._default_account_entries(
            session_id
        )
        not_loaded = ["full post content", "individual comments"]
        if len(posts) > POST_DIRECTORY_PAGE_SIZE:
            not_loaded.insert(0, "remaining post directory pages")
        return self._success(
            tool="read_report",
            result_kind="unified_report_overview",
            content_state="overview_with_bounded_post_directory",
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
                    session_id, first_page, "read_report"
                ),
                "post_directory": self._directory_metadata(
                    total_count=len(posts), page=1, returned_count=len(first_page)
                ),
                "post_previews_complete_for_report": len(posts) <= POST_DIRECTORY_PAGE_SIZE,
                "account_entries": account_entries,
                "account_entry_statistics": account_entry_statistics,
            },
            not_loaded=not_loaded,
            limitations=[
                "Every post conclusion is limited to the frozen audit result and its recorded rules.",
                "The overview includes at most the first ten post previews; use list_report_posts for other directory pages.",
                "Use read_posts with a post preview reference before presenting full post content or detailed audit reasoning.",
            ],
        )

    def _list_report_posts(self, session_id, args):
        _require_exact_keys(args, {"page"}, required={"page"})
        page = _require_int(
            args["page"], "page", minimum=1, maximum=MAX_POST_DIRECTORY_PAGES
        )
        posts = tuple(self.repository.ordered_posts())
        page_count = max(
            1,
            (len(posts) + POST_DIRECTORY_PAGE_SIZE - 1)
            // POST_DIRECTORY_PAGE_SIZE,
        )
        if page > page_count:
            raise ToolInputError(
                "invalid_arguments",
                f"page must be between 1 and {page_count} for this report",
            )
        offset = (page - 1) * POST_DIRECTORY_PAGE_SIZE
        selected = posts[offset : offset + POST_DIRECTORY_PAGE_SIZE]
        cards = self._post_cards(session_id, selected, "list_report_posts")
        for local_position, card in enumerate(cards, 1):
            card["position"] = offset + local_position
        return self._success(
            tool="list_report_posts",
            result_kind="unified_report_post_directory",
            content_state="bounded_post_previews",
            data={
                "post_previews": cards,
                "post_directory": self._directory_metadata(
                    total_count=len(posts),
                    page=page,
                    returned_count=len(cards),
                ),
            },
            not_loaded=["full post content", "individual comments"],
            limitations=[
                "Directory previews do not replace read_posts detail.",
                "The report order is frozen and positions are global across pages.",
            ],
        )

    @staticmethod
    def _directory_metadata(*, total_count, page, returned_count):
        page_count = max(
            1,
            (total_count + POST_DIRECTORY_PAGE_SIZE - 1)
            // POST_DIRECTORY_PAGE_SIZE,
        )
        return {
            "total_count": total_count,
            "returned_count": returned_count,
            "page": page,
            "page_size": POST_DIRECTORY_PAGE_SIZE,
            "page_count": page_count,
            "has_more": page < page_count,
            "next_page": page + 1 if page < page_count else None,
            "complete_for_report": page == 1 and page_count == 1,
        }

    def _default_account_entries(self, session_id):
        if self.account_activity is None:
            return [], {
                "publisher_account_count": 0,
                "commenter_account_count": 0,
                "distinct_account_count": 0,
                "displayed_publisher_count": 0,
                "displayed_commenter_count": 0,
            }
        available = self.account_activity.report_entries(session_id)
        publishers = [
            item for item in available if "post_author" in item.get("report_roles", ())
        ][:DEFAULT_PUBLISHER_ENTRY_LIMIT]
        commenters = [
            item
            for item in available
            if "comment_author" in item.get("report_roles", ())
            and item.get("report_group_placement", {}).get(
                "default_active_comment_visible"
            )
        ][:DEFAULT_COMMENTER_ENTRY_LIMIT]
        selected = []
        seen = set()
        for item in (*publishers, *commenters):
            account_ref = item.get("account_ref")
            if account_ref in seen:
                continue
            seen.add(account_ref)
            selected.append(item)

        return selected, {
            **self.account_activity.current_report_account_statistics(),
            "displayed_publisher_count": len(publishers),
            "displayed_commenter_count": len(commenters),
        }

    def _post_cards(self, session_id, posts, source):
        posts = tuple(posts)
        cards = super()._post_cards(session_id, posts, source)
        for card, post in zip(cards, posts):
            card["audit_summary"] = self.repository.finding_for_post(post.id).summary
        return cards

    def _read_posts(self, session_id, args):
        """Expand at most five already exposed unified-report posts per call."""

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
    schemas.extend(
        [deepcopy(LIST_REPORT_POSTS), deepcopy(LIST_AUTHORIZED_REPORTS),
         deepcopy(COMPARE_AUTHORIZED_REPORT_ACCOUNTS), deepcopy(LIST_REPORT_ACCOUNTS),
         deepcopy(GET_REPORT_ACCOUNT_STATISTICS)]
    )
    for schema in schemas:
        if schema["name"] == "read_report":
            schema["description"] = (
                "读取当前1至30条帖子新增统一审核报告的概览、完整统计、第一页最多10条"
                "冻结帖子预览，以及最多5个默认发帖者和5个活跃评论者入口。"
                "回答报告概况时先使用本工具；帖子超过10条时使用list_report_posts读取"
                "其他目录页。预览包含可供read_posts继续读取的当前会话引用。"
                "账号总数来自完整快照，displayed字段仅表示本次入口展示量；"
                "查询评论者/发布者人数和TopN直接用get_report_account_statistics，不展开评论明细计数。"
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


UNIFIED_REPORT_SYSTEM_PROMPT = f"""你是新增统一审核报告的问答助手。当前报告包含1至{MAX_SUPPORTED_INVESTIGATION_POSTS}条冻结帖子，可能同时存在审核通过、建议复审、建议拒绝或待确认结果。只使用当前会话工具返回的已授权资料，禁止自行重新审核、改变既有结论、猜测缺失内容或泄露内部标识和路径。
用户询问“这个报告的大概内容是什么”、报告概览或结果分布时，先调用read_report。read_report返回完整统计和第一页最多{POST_DIRECTORY_PAGE_SIZE}条帖子目录；概览问题可据此回答并说明当前展示数量，不要为概览自动展开正文。用户明确要求列出全部帖子、全部安全帖或全部风险帖时，继续调用list_report_posts读取所有尚未加载的目录页，再完整回答，不得只讲风险帖而遗漏安全帖，也不得把预览扩写成尚未读取的正文。
用户要求展开“第几条帖子”“第几条安全帖”、帖子正文、ASR、翻译、完整审核理由或多个帖子的比较时，优先复用当前会话历史目录中已验证的引用；尚未展示目标时，调用read_report或直接调用包含该全局序号的list_report_posts页面，再调用read_posts。read_posts每次最多读取5条，可以分批读取后统一回答；不使用主题发现工具。需要按主题判断时读取相关目录页和帖子，证据不足就明确说明，不能拿不相关帖子补足。
解释为什么通过、复审或拒绝，必须归因于read_posts返回的原审核说明及可核对材料。需要材料目录时调用list_evidence，需要具体材料原文时调用read_evidence；没有独立材料时明确说明结论依据仅为原审核说明，不能伪造Finding或Evidence。所有评论使用list_post_comments；未审核或审核失败的评论不等于无风险，帖子风险不能传递给评论。
用户询问某帖的风险评论时，调用list_post_comments并使用risk_filter=risk；筛选和计数由服务器在分页前完成。用户要求全部匹配评论时，沿相同筛选读取所有分页。评论结果中的账号引用可以继续查询该评论者的账号概览与活动。
询问评论统计时，重新调用read_report取得当前汇总。完成审核数、评论自身风险数和报告引用评论材料数含义不同，不能混用；范围限当前报告冻结帖子下的已存评论，不代表平台全部评论。
只有昵称时先调用search_accounts。唯一精确匹配可以继续查看账号概览和活动；同名或模糊匹配必须请用户选择，不能按昵称强行合并。报告内容范围与账号活动授权范围分别说明，账号没有整体审核决定，不能把帖子或评论通过说成“账号审核通过”。
比较共同账号时，从list_authorized_reports选择用户指定报告，调用compare_authorized_report_accounts；同名或指代不明先澄清，不擅自扩大到全部报告。比较只返回交集；各报告账号目录另用list_report_accounts。按用户问题选择角色，默认展示首批并说明总数；明确要求全部才续页，未读完不能声称全部。结果账号引用可直接穿透，详情仍按原授权范围查询。无法稳定识别的活动不参与合并，同昵称不合并。报告引用失效时重读报告目录；内部引用不向用户展示。
引用失效时重新读取当前报告取得入口，不猜引用。连续追问可以复用当前会话已经验证的引用和资料；需要新细节必须调用相应工具。回答使用中文、自然名称和可理解的依据，不展示工具名、内部ID、引用token、数据库路径或配置字段。"""
