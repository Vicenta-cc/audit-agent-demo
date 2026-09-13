"""Read-only M1/M2 adaptation of a verified all-pass report snapshot."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace

from .account_corpus import account_ref, freeze
from .account_activity_repository import AccountActivityRepository
from .report_task_service import (
    ReportTaskInvestigationToolService,
    SearchTurnUsage,
    MAX_SEARCH_CANDIDATES_PER_TURN,
    MAX_SEARCH_CONTINUATIONS_PER_TURN,
)
from .refs import ReferenceError
from .schemas import M2_ACCOUNT_ACTIVITY_TOOLS, REPORT_STATISTICS_DESCRIPTION
from .service import ToolInputError, _require_string


def project_pass_content(payload):
    source = payload.get("source_content") or {}
    videos = []
    for index, video in enumerate(source.get("video_results") or [], 1):
        transcript = video.get("transcript") or video.get("asr_text") or {}
        original = (
            transcript if isinstance(transcript, str) else transcript.get("text", "")
        )
        translated = (
            ""
            if isinstance(transcript, str)
            else (
                transcript.get("text_zh")
                or (transcript.get("translation") or {}).get("text")
                or ""
            )
        )
        videos.append(
            {
                "position": index,
                "speech_transcript": {
                    "label": "原语言语音转写",
                    "text": original,
                    "original_text": original,
                    "translated_text": translated,
                },
            }
        )
    return {
        "author_caption": str(
            source.get("desc")
            or source.get("title")
            or payload.get("source_title")
            or ""
        ),
        "translated_caption": str(
            source.get("desc_zh") or source.get("title_zh") or ""
        ),
        "videos": videos,
    }


class SnapshotAccountData:
    """AccountActivityRepository input built only from already verified snapshots.

    Missing stable identity stays unresolved. Nicknames are never identity keys.
    The caller supplies the authorized report set; there is no database discovery.
    """

    @classmethod
    def from_snapshot(cls, snapshot):
        """Use the same activity adapter before and after report publication."""
        findings = {f.post_ref: SimpleNamespace(**{
            key: f.payload[key] for key in ("decision", "risk_level")
        }) for f in snapshot.findings}
        comments = {}
        for post in snapshot.posts:
            comments[post.ref] = tuple(SimpleNamespace(
                id=str(c["comment_id"]), text=c["content"],
                author_source_key=c["sec_uid"], author_display_name=c.get("nickname", ""),
                identity_consistent=True, published_at=c["create_time"],
                audit_status=c["audit_status"], risk_level=c["risk_level"],
                risk_type=c["risk_type"],
            ) for c in post.payload["comments"])
        repo = SimpleNamespace(
            fixture=SimpleNamespace(provenance=SimpleNamespace(source_task_id=snapshot.task_id)),
            report=SimpleNamespace(title=snapshot.display_name),
            template_kind="", account_source="report_snapshot",
            snapshot_payloads={p.ref: p.payload for p in snapshot.posts},
            finding_for_post=findings.__getitem__, _report_comments_by_post=comments,
            snapshot_hash=snapshot.snapshot_hash, content_hash=snapshot.snapshot_hash,
        )
        data = cls((repo,), completed_only=True)
        data.schema_version = "report-snapshot-accounts/v1"
        data._tasks = freeze({snapshot.task_id: {
            "task_display_name": snapshot.display_name,
            "snapshot_ref": snapshot.snapshot_ref, "source_hash": snapshot.snapshot_hash,
        }})
        return data

    def __init__(self, repositories, *, legacy_corpus=None, completed_only=False):
        repositories = tuple(repositories)
        self._accounts, self._aliases, self._tasks = {}, defaultdict(list), {}
        occurrences = []
        for repo in repositories:
            task = repo.fixture.provenance.source_task_id
            if task in self._tasks:
                raise ValueError("duplicate authorized task")
            self._tasks[task] = {"task_display_name": repo.report.title}
            if getattr(repo, "account_source", "") != "report_snapshot" and repo.template_kind not in {"all_pass", "single_risk_post", "selected_existing_audits"}:
                if (
                    legacy_corpus is None
                    or task not in legacy_corpus.authorized_task_ids
                ):
                    raise ValueError(
                        "Authorized legacy report requires its existing account corpus"
                    )
                self._tasks[task] = legacy_corpus.snapshot_for_task(task)
                for item in legacy_corpus.occurrences:
                    if item["task_id"] != task:
                        continue
                    occurrences.append(item)
                    ident = item.get("account_ref")
                    if ident and ident not in self._accounts:
                        self._accounts[ident] = legacy_corpus.account(ident)
                        self._aliases[ident].extend(legacy_corpus.aliases_for(ident))
                continue
            for post_id, payload in repo.snapshot_payloads.items():
                raw = payload.get("raw_content_payload") or {}
                platform = (
                    "douyin"
                    if payload.get("platform") in {"dy", "douyin"}
                    else str(payload.get("platform") or "")
                )

                def identify(author):
                    key = str(author.get("sec_uid") or "").strip()
                    if not key or platform != "douyin":
                        return None
                    ref = account_ref(
                        platform=platform,
                        source_namespace="douyin.sec_uid",
                        source_account_key=key,
                    )
                    self._accounts[ref] = {"account_ref": ref}
                    self._aliases[ref].append(
                        {"nickname": str(author.get("nickname") or "")}
                    )
                    return ref

                author = identify(raw.get("author") or {})
                finding = repo.finding_for_post(post_id)
                post = {
                    "content_key": post_id.split(":", 1)[-1],
                    "platform": platform,
                    "display_title": payload.get("display_title")
                    or payload.get("title"),
                    "display_title_source": payload.get("display_title_source")
                    or "source",
                    "published_at": payload.get("published_at") or None,
                }

                def occurrence(
                    kind, ident, key, timestamp, status, decision, risk, comment=None
                ):
                    item = {
                        "kind": kind,
                        "account_ref": ident,
                        "task_id": task,
                        "post": post,
                        "occurred_at": timestamp or None,
                        "audit_status": status,
                        "decision": decision,
                        "risk_level": risk,
                        "parent_post_author_account_ref": author,
                        "comment": comment,
                        "source_locator": {
                            "task_id": task,
                            "content_key": post["content_key"],
                            **({"comment_id": key} if kind == "comment_author" else {}),
                        },
                    }
                    item["occurrence_ref"] = (
                        "snapshot-activity:"
                        + hashlib.sha256(
                            f"{task}:{post_id}:{kind}:{key}".encode()
                        ).hexdigest()
                    )
                    occurrences.append(freeze(item))

                occurrence(
                    "post_author",
                    author,
                    post_id,
                    post["published_at"],
                    "completed",
                    finding.decision,
                    finding.risk_level,
                )
                for c in repo._report_comments_by_post.get(post_id, ()):
                    if completed_only and c.audit_status != "completed":
                        continue
                    ident = (
                        identify(
                            {
                                "sec_uid": c.author_source_key,
                                "nickname": c.author_display_name,
                            }
                        )
                        if c.identity_consistent
                        else None
                    )
                    occurrence(
                        "comment_author",
                        ident,
                        c.id,
                        c.published_at,
                        c.audit_status,
                        None,
                        c.risk_level,
                        {
                            "comment_id": c.id,
                            "risk_type": c.risk_type,
                            "text": c.text,
                            "comment_time": c.published_at,
                        },
                    )
        self._accounts = freeze(self._accounts)
        self._tasks = freeze(self._tasks)
        self._aliases = freeze(
            {key: list(value) for key, value in self._aliases.items()}
        )
        self.authorized_task_ids = tuple(sorted(self._tasks))
        self.occurrences = tuple(occurrences)
        self._by_ref = {o["occurrence_ref"]: o for o in occurrences}
        self._by_account = defaultdict(list)
        for o in occurrences:
            if o["account_ref"]:
                self._by_account[o["account_ref"]].append(o)
        self.corpus_revision = hashlib.sha256(
            json.dumps(
                sorted(
                    (
                        r.fixture.provenance.source_task_id,
                        r.snapshot_hash,
                        r.content_hash,
                    )
                    for r in repositories
                )
            ).encode()
        ).hexdigest()
        if legacy_corpus is not None:
            self.corpus_revision = hashlib.sha256(
                (self.corpus_revision + legacy_corpus.corpus_revision).encode()
            ).hexdigest()

    def account(self, ref):
        return self._accounts[ref]

    @property
    def accounts(self):
        return tuple(self._accounts.values())

    def aliases_for(self, ref):
        return tuple(self._aliases.get(ref, ()))

    def occurrences_for(self, ref):
        return tuple(self._by_account.get(ref, ()))

    def occurrence(self, ref):
        return self._by_ref[ref]

    def snapshot_for_task(self, task):
        return self._tasks[task]


class SnapshotAccountRepository(AccountActivityRepository):
    def ordered_occurrences(
        self, account_id, *, kind, comment_target_account_id=None, risk_filter=None
    ):
        if risk_filter != "normal_only":
            return super().ordered_occurrences(
                account_id,
                kind=kind,
                comment_target_account_id=comment_target_account_id,
                risk_filter=risk_filter,
            )
        values = super().ordered_occurrences(
            account_id, kind=kind, comment_target_account_id=comment_target_account_id
        )
        return tuple(
            o
            for o in values
            if o["audit_status"] == "completed" and o["risk_level"] == "none"
        )


class PassReportToolService(ReportTaskInvestigationToolService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._handlers["list_post_comments"] = self._list_post_comments

    def _read_real_report(self, session_id, args):
        if args:
            raise ToolInputError("invalid_arguments", "read_report takes no arguments")
        repo = self.repository
        return self._success(
            tool="read_report",
            result_kind="pass_report_overview",
            content_state="overview_with_post_previews",
            data={
                "title": repo.report.title,
                "overview": repo.report_overview,
                "statistics": {
                    "post_count": len(repo.ordered_posts()),
                    "decision_counts": repo.report.statistics.decision_counts,
                    "risk_counts": repo.report.statistics.risk_counts,
                    **repo.report_comment_statistics(),
                },
                "post_previews": self._post_cards(
                    session_id, repo.ordered_posts()[:20], "read_report"
                ),
                "account_entries": self.account_activity.report_entries(session_id),
            },
            not_loaded=["full post content", "individual comments"],
            limitations=[
                "The pass conclusion applies only to completed audits and their recorded rules; pending comments are not safe conclusions."
            ],
        )

    @staticmethod
    def _comment_coverage(comments):
        completed = sum(c.audit_status == "completed" for c in comments)
        return {
            "total": len(comments),
            "completed": completed,
            "failed": sum(c.audit_status == "failed" for c in comments),
            "pending": sum(c.audit_status in {"pending", "queued"} for c in comments),
            "unknown": sum(c.audit_status not in {"completed", "failed", "pending", "queued"} for c in comments),
            "scope": "stored_snapshot_comments_not_platform_total",
        }

    def _post_cards(self, session_id, posts, source):
        with_batch = self.refs.batch(session_id)
        cards = []
        for i, p in enumerate(posts, 1):
            token = with_batch.expose(
                kind="post",
                object_id=p.id,
                parent_id=self.repository.snapshot.id,
                source_tool=source,
                result_kind="snapshot_post_preview",
                content_state="preview",
            )
            finding = self.repository.finding_for_post(p.id)
            cards.append(
                {
                    "position": i,
                    "ref": token,
                    "title": p.title,
                    "author": p.author.display_name,
                    "published_at": p.source.published_at,
                    "preview": p.body[:700],
                    "decision": finding.decision,
                    "risk_level": finding.risk_level,
                    "comment_count": len(
                        self.repository._report_comments_by_post.get(p.id, ())
                    ),
                }
            )
        with_batch.commit()
        return cards

    def _validate_post_record(self, record, post):
        if (
            record.object_id not in self.repository.snapshot.post_ids
            or post.revision_id not in self.repository.snapshot.post_revision_ids
        ):
            raise ReferenceError(
                "stale_revision_ref", "Post is outside the frozen snapshot"
            )
        if not any(
            o.source_tool in {"read_report", "search_posts", "read_account_occurrence"}
            and o.parent_id == self.repository.snapshot.id
            for o in record.origins
        ):
            raise ReferenceError(
                "invalid_ref_source", "Post reference has no verified snapshot origin"
            )

    def _search_posts(self, session_id, args, *, turn_id=""):
        if not turn_id:
            raise ToolInputError("missing_turn_identity", "turn identity is required")
        if (
            set(args)
            - {"query_text", "requested_count", "filters", "cursor", "context_ref"}
            or not isinstance(args.get("query_text"), str)
            or not 1 <= len(args["query_text"].strip()) <= 200
        ):
            raise ToolInputError(
                "invalid_arguments", "query_text and requested_count are required"
            )
        context_ref = args.get("context_ref")
        if context_ref is not None:
            context_ref = _require_string(context_ref, "context_ref")
            context_record = self.refs.resolve(
                session_id, context_ref, expected_kind="post"
            )
            self._validate_post_record(
                context_record, self.repository.post(context_record.object_id)
            )
        if "cursor" in args:
            _require_string(args["cursor"], "cursor")
        count = args.get("requested_count")
        if type(count) is not int or not 1 <= count <= 20:
            raise ToolInputError("invalid_arguments", "requested_count must be 1..20")
        filters = args.get("filters", {})
        if not isinstance(filters, dict) or set(filters) - {"decision", "risk_level"}:
            raise ToolInputError("invalid_arguments", "unsupported filters")
        if any(not isinstance(value, str) for value in filters.values()):
            raise ToolInputError("invalid_arguments", "filter values must be strings")
        if filters.get("decision", "pass") not in {
            "pass",
            "review",
            "reject",
        } or filters.get("risk_level", "none") not in {"none", "low", "medium", "high"}:
            raise ToolInputError("invalid_arguments", "unsupported filter value")
        candidates = tuple(
            p
            for p in self.repository.ordered_posts()
            if all(
                getattr(self.repository.finding_for_post(p.id), key) == value
                for key, value in filters.items()
            )
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "query_text": args["query_text"].strip(),
                    "filters": filters,
                    "context_ref": context_ref,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        ordered_hash = hashlib.sha256(
            json.dumps([p.id for p in candidates]).encode()
        ).hexdigest()
        offset = 0
        if args.get("cursor"):
            cursor = self.refs.resolve_search_cursor(
                session_id,
                args["cursor"],
                discovery_fingerprint=fingerprint,
                ordered_post_ids_hash=ordered_hash,
            )
            offset = cursor.next_offset
        usage_key = (session_id, self.refs.scope(session_id).generation, turn_id)
        with self._search_lock:
            usage = self._search_turn_usage.setdefault(usage_key, SearchTurnUsage())
            remaining = MAX_SEARCH_CANDIDATES_PER_TURN - usage.candidates_returned
            if remaining <= 0 or (
                args.get("cursor")
                and usage.continuation_calls >= MAX_SEARCH_CONTINUATIONS_PER_TURN
            ):
                raise ToolInputError(
                    "search_turn_limit_reached",
                    "This turn exhausted its candidate or continuation budget",
                )
            selected = candidates[offset : offset + min(20, remaining)]
            usage.search_calls += 1
            usage.continuation_calls += int(bool(args.get("cursor")))
            usage.candidates_returned += len(selected)
        next_offset = offset + len(selected)
        cursor = (
            self.refs.issue_search_cursor(
                session_id,
                discovery_fingerprint=fingerprint,
                ordered_post_ids_hash=ordered_hash,
                next_offset=next_offset,
            )
            if next_offset < len(candidates)
            else None
        )
        return self._success(
            tool="search_posts",
            result_kind="snapshot_post_candidate_directory",
            content_state="preview",
            data={
                "candidates": self._post_cards(session_id, selected, "search_posts"),
                "total_candidate_count": len(candidates),
                "requested_count": count,
                "cursor": cursor,
                "has_more": cursor is not None,
                "server_semantic_matching_performed": False,
            },
            not_loaded=["full post content", "individual comments"],
            limitations=[
                "Candidates are in frozen order; select relevant ones from previews, never fill with unrelated posts."
            ],
        )

    def _list_post_comments(self, session_id, args):
        if (
            set(args) - {"post_ref", "limit", "cursor"}
            or not isinstance(args.get("post_ref"), str)
            or type(args.get("limit")) is not int
            or not 1 <= args["limit"] <= 20
        ):
            raise ToolInputError(
                "invalid_arguments", "post_ref and limit (1..20) are required"
            )
        if "cursor" in args:
            _require_string(args["cursor"], "cursor")
        record = self.refs.resolve(session_id, args["post_ref"], expected_kind="post")
        post = self.repository.post(record.object_id)
        self._validate_post_record(record, post)
        comments = self.repository._report_comments_by_post.get(post.id, ())
        digest = hashlib.sha256(
            json.dumps([c.id for c in comments]).encode()
        ).hexdigest()
        offset = 0
        if args.get("cursor"):
            offset = self.refs.resolve_risk_comment_cursor(
                session_id,
                args["cursor"],
                post_id=post.id,
                ordered_comment_ids_hash=digest,
            ).next_offset
        selected = comments[offset : offset + args["limit"]]
        next_offset = offset + len(selected)
        cursor = (
            self.refs.issue_risk_comment_cursor(
                session_id,
                post_id=post.id,
                ordered_comment_ids_hash=digest,
                next_offset=next_offset,
            )
            if next_offset < len(comments)
            else None
        )
        account_repository = self.account_activity._load_repository()

        def comment_card(c):
            occurrence = account_repository.comment_occurrence(
                self.repository.fixture.provenance.source_task_id,
                post.id.split(":", 1)[-1],
                c.id,
            )
            account_id = occurrence.get("account_ref")
            token = (
                self.account_activity.expose_account(
                    session_id,
                    account_id,
                    source_tool="list_post_comments",
                    content_state="comment_author_account",
                    repository=account_repository,
                )
                if account_id
                else None
            )
            return {
                "content": c.text,
                "author": c.author_display_name,
                "account_ref": token,
                "published_at": c.published_at,
                "audit_status": c.audit_status,
                "risk_level": c.risk_level,
            }

        return self._success(
            tool="list_post_comments",
            result_kind="snapshot_comments",
            content_state="complete_stored_comments",
            data={
                "post_ref": args["post_ref"],
                "total_count": len(comments),
                "audit_coverage": self._comment_coverage(comments),
                "comments": [comment_card(c) for c in selected],
                "cursor": cursor,
                "has_more": cursor is not None,
            },
            not_loaded=[],
            limitations=[
                "A comment without completed independent audit is not a normal/safe comment."
            ],
        )

    def _dispatch_account_activity(self, session_id, args, *, tool_name):
        result = json.loads(
            super()._dispatch_account_activity(session_id, args, tool_name=tool_name)
        )
        if tool_name == "read_account_occurrence" and result.get("ok"):
            occurrence = result["data"]["occurrence"]
            record = self.account_activity.refs.resolve(
                session_id,
                args["occurrence_ref"],
                expected_kind="occurrence",
                corpus_revision=self.account_activity._load_repository().corpus_revision,
            )
            raw = self.account_activity._load_repository().occurrence(
                record.parent_account_id, record.object_id
            )
            if raw["task_id"] == self.repository.fixture.provenance.source_task_id:
                post_id = "post:" + str(raw["post"]["content_key"])
                occurrence["original_post_ref"] = self._post_cards(
                    session_id,
                    [self.repository.post(post_id)],
                    "read_account_occurrence",
                )[0]["ref"]
        return json.dumps(result, ensure_ascii=False)


def pass_tool_schemas():
    schemas = deepcopy(list(M2_ACCOUNT_ACTIVITY_TOOLS))
    schemas = [
        s
        for s in schemas
        if s["name"] not in {"list_finding_posts", "list_post_risk_comments"}
    ]
    for s in schemas:
        if s["name"] == "read_report":
            s["description"] = (
                "读取当前绑定的正常通过报告概览、全部样本统计、帖子导航和账号入口。"
                + REPORT_STATISTICS_DESCRIPTION
            )
        if s["name"] == "search_posts":
            s["description"] = (
                "从当前报告全部冻结帖子中取得有序候选，包含 pass/none。依据 preview 判断相关性；用 read_posts 展开原文和审核说明。"
            )
            filters = s["parameters"]["properties"]["filters"]["properties"]
            filters["decision"]["description"] = "按已存审核决定过滤。"
            filters["risk_level"][
                "description"
            ] = "按已存风险等级过滤；none 表示未发现风险。"
            s["parameters"]["properties"]["context_ref"][
                "description"
            ] = "可选：本会话已验证的帖子引用，仅提供追问上下文，不扩大报告范围。"
            filters["decision"]["enum"] = ["pass", "review", "reject"]
            filters["risk_level"]["enum"] = ["none", "low", "medium", "high"]
        if s["name"] == "list_account_occurrences":
            s["parameters"]["properties"]["risk_filter"]["enum"] = [
                "risk_only",
                "normal_only",
            ]
            s[
                "description"
            ] += " normal_only 仅匹配对象自身已完成审核且无风险的活动；不包括未审核活动。"
    schemas.append(
        {
            "name": "list_post_comments",
            "description": "读取已验证帖子下的全部评论（含正常与未审核），返回原文、作者、时间及各自审核状态，支持分页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "post_ref": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "cursor": {"type": "string"},
                },
                "required": ["post_ref", "limit"],
                "additionalProperties": False,
            },
        }
    )
    return schemas


PASS_SYSTEM_PROMPT = """你是调查报告问答助手。只使用当前会话工具返回的已授权资料，禁止自行审核、猜测缺失内容或泄露内部标识和路径。
询问报告评论统计时，调用 read_report 取得当前汇总，旧对话缺字段时重新读取。independently_reviewed_comments表示完成了多少评论审核；comment_own_risk表示其中多少评论自身被判定有风险；direct_comment_evidence_count表示报告引用了多少评论材料来说明相关发现，对用户可称“报告引用的评论材料数”。这些指标分别描述审核结果和报告引用材料，按各自含义理解，不能混用。报告概览的risk_counts和decision_counts是帖子的分布，评论汇总未提供低/中/高风险分项；这不是评论资料缺失。具体聚类中的材料可称“支撑这类发现的帖子和评论”。根据用户实际问题自然组织语言、选择相关指标和篇幅，无须固定句式，也不必每次解释所有口径。数值取实际工具结果，不展示字段名；审核失败、待处理和状态未知不算完成，按问题需要说明。范围限当前报告冻结帖下已存评论，不代表平台全部或任务全部采集结果；空值表示资料不足，不用其他统计补填。统计回答无需逐帖查询；用户追问具体引用关系时再查相关明细，未加载称“尚未读取”。
当前报告是全部审核通过的样本报告。询问报告或报告帖子时先 read_report 获取入口；按昵称问账号活动不需要先读报告；搜索正常帖子用 search_posts，读取原文、原语言 ASR、译文、发布时间和已有审核说明用 read_posts；所有评论用 list_post_comments。
用户概览询问某个聚类“有哪些帖子/都有谁”且没有要求全部时，先用 list_finding_posts(limit=5)，说明聚类总数并明确当前只展示5篇代表帖；不要把5篇说成全部。帖子卡片或 read_posts 返回的 detail_links.detail_url 是同一帖子的审核详情入口，有值时直接作为标题链接或“查看审核详情”链接展示；platform_url 有值时也原样展示，不能自行拼接或猜测链接。若用户只问有哪些帖子，除标题和作者外，优先补充工具已返回的平台、归类支撑摘要或审核结论，避免只给裸链接；这不要求调用 read_posts。
为什么通过必须归因于原审核说明及可核对原文，没有直接证据时明确说明，不能伪造风险 Finding/Evidence。未审核或审核失败的评论不等于无风险，帖子风险不传给评论。评论审核完整性以工具返回的全量覆盖统计为准，不能根据已读分页推断全部审核完成；评论数量指快照已采集数量，不能冒充平台总数。
只有昵称时先 search_accounts，搜索全部已授权调查的已记录账号，不限默认卡片或风险评论。唯一精确匹配返回的 overview 可直接回答数量、发帖数、活动时间及常评论博主，不重复 get_account_overview；明细用其中 account.ref 调用 list_account_occurrences，必填 account_ref、kind（comment_author 或 post_author）、limit（通常20）；预览只能称评论摘要，完整原文用 read_account_occurrence 展开，必填 occurrence_ref（列表返回的活动引用）；其原帖用 read_account_post，必填 post_ref（详情中的 parent_post.ref）。同名或模糊匹配先请用户选择，不取首位或合并；没有匹配只表示授权资料未找到。连续追问“他”沿用最后确定的账号，读取评论对象不能改变指代。已知工具名时需要参数可直接 tool_describe 一次，无需先 tool_search 或重复读取说明。
M2 账号入口进入已授权调查数据的账号概览，使用 get_account_overview、list_account_occurrences、read_account_occurrence；normal_only 筛选已完成审核且无风险，risk_only 筛选自身风险活动。original_post_ref 可用 read_posts 回看当前报告原帖。
报告结论范围与账号活动范围分别说明。已授权调查总数不等于该账号有活动的调查数；账号涉及哪些调查必须以实际活动分组为准。回答审核覆盖率必须重新 read_report 获取完整统计，不能沿用先前抽样推断。覆盖统计的完成、失败、等待、未知四项互斥，相加为采集总数；失败属于未完成，不能再重复计作另一条未审核评论。不要输出 pass/none/completed/validated_no_risk 等枚举或内部字段，改用中文状态。账号没有整体审核决定，不能把其帖子或评论通过说成“账号审核通过”或推断账号身份与立场。引用失效时重新读取当前报告取得入口，不猜引用。连续追问复用当前会话的引用和已加载资料；需要新细节必须调用工具。候选不足、不存在或资料缺失如实说明，不补编。回答使用中文、自然名称和可理解的依据，不展示工具名、内部 ID、引用 token、数据库路径或配置字段。"""
