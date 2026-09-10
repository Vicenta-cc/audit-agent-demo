"""TaskSnapshot entry point plus the three unchanged M0 object readers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
from typing import Any, Callable

from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.refs import ReferenceError
from hermes_m0.service import (
    EVIDENCE_TYPES,
    MAX_EVIDENCE_PER_LIST,
    MAX_EVIDENCE_PER_READ,
    MAX_POSTS_PER_READ,
    ToolInputError,
    _preview,
    _require_exact_keys,
    _require_int,
    _require_string,
    _require_string_list,
)
from hermes_m0.task_domain import TaskPost, TaskVideoTranscript
from hermes_m0.task_refs import TaskReferenceRegistry, TaskSessionScope
from hermes_m0.task_repository import (
    TaskPostSearchMatch,
    TaskRepository,
    TaskRepositoryLookupError,
    normalize_search_text,
)
from hermes_m0.tool_results import error_result, success_result


MAX_TASK_POSTS_PER_LIST = 20
MAX_SEARCH_PAGE = 10
MAX_SEARCH_CONTINUATIONS_PER_TURN = 3
MAX_SEARCH_CANDIDATES_PER_TURN = 20
DECISIONS = frozenset({"pass", "review", "reject"})
RISK_LEVELS = frozenset({"none", "low", "medium", "high"})


@dataclass
class SearchTurnUsage:
    search_calls: int = 0
    continuation_calls: int = 0
    candidates_returned: int = 0


class TaskInvestigationToolService:
    def __init__(
        self,
        repositories: dict[str, TaskRepository],
        *,
        refs: TaskReferenceRegistry | None = None,
        ledger: ToolExecutionLedger | None = None,
    ) -> None:
        self.repositories = repositories
        self.refs = refs or TaskReferenceRegistry()
        self.ledger = ledger
        self._session_repositories: dict[str, TaskRepository] = {}
        self._search_lock = threading.RLock()
        self._search_turn_usage: dict[tuple[str, int, str], SearchTurnUsage] = {}
        self._handlers: dict[str, Callable[[str, dict[str, Any]], str]] = {
            "search_posts": self._search_posts,
            "list_task_posts": self._list_task_posts,
            "read_posts": self._read_posts,
            "list_evidence": self._list_evidence,
            "read_evidence": self._read_evidence,
        }

    def bind_session(
        self,
        session_id: str,
        *,
        task_id: str,
        force_new_generation: bool = False,
    ) -> TaskSessionScope:
        try:
            repository = self.repositories[task_id]
        except KeyError as exc:
            raise ValueError("Unknown frozen task fixture") from exc
        snapshot = repository.snapshot
        scope = self.refs.bind(
            session_id=session_id,
            task_id=snapshot.task_id,
            snapshot_id=snapshot.id,
            revision=snapshot.revision,
            force_new_generation=force_new_generation,
        )
        self._session_repositories[session_id] = repository
        with self._search_lock:
            self._search_turn_usage = {
                key: value
                for key, value in self._search_turn_usage.items()
                if key[0] != session_id
            }
        return scope

    def has_session(self, session_id: str) -> bool:
        return session_id in self._session_repositories and self.refs.has_scope(
            session_id
        )

    def execute_tool_call(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        next_call: Callable[[dict[str, Any]], Any],
    ) -> Any:
        if self.ledger is None:
            return error_result(
                tool=tool_name,
                code="idempotency_ledger_unavailable",
                message="The Investigation tool execution ledger is not configured.",
            )
        return self.ledger.execute(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=args,
            next_call=next_call,
        )

    def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        session_id: str,
        turn_id: str = "",
    ) -> str:
        handler = self._handlers.get(tool_name)
        if handler is None:
            return error_result(
                tool=tool_name,
                code="tool_unavailable_for_task_scope",
                message=(
                    "This task scope supports search_posts, list_task_posts, read_posts, "
                    "list_evidence, and read_evidence."
                ),
            )
        try:
            self.refs.scope(session_id)
            if not isinstance(args, dict):
                raise ToolInputError(
                    "invalid_arguments", "Tool arguments must be an object."
                )
            if tool_name == "search_posts":
                return handler(session_id, args, turn_id=turn_id)
            return handler(session_id, args)
        except (ReferenceError, ToolInputError) as exc:
            return error_result(tool=tool_name, code=exc.code, message=exc.message)
        except TaskRepositoryLookupError:
            return error_result(
                tool=tool_name,
                code="scope_object_missing",
                message="The referenced object is not present in the bound TaskSnapshot.",
            )

    def _repository(self, session_id: str) -> TaskRepository:
        try:
            return self._session_repositories[session_id]
        except KeyError as exc:
            raise ReferenceError(
                "investigation_scope_unbound", "No TaskSnapshot is bound."
            ) from exc

    def _search_posts(
        self,
        session_id: str,
        args: dict[str, Any],
        *,
        turn_id: str,
    ) -> str:
        _require_exact_keys(
            args,
            {"query_text", "filters", "limit", "cursor"},
            required={"query_text", "limit"},
        )
        if not turn_id:
            raise ToolInputError(
                "missing_turn_identity",
                "Hermes task_id is required to enforce per-turn search limits.",
            )
        query_text = _require_string(args["query_text"], "query_text")
        if len(query_text) > 200:
            raise ToolInputError(
                "invalid_arguments", "query_text must contain at most 200 characters"
            )
        limit = _require_int(args["limit"], "limit", 1, MAX_SEARCH_PAGE)
        filters = args.get("filters", {})
        if not isinstance(filters, dict):
            raise ToolInputError("invalid_arguments", "filters must be an object")
        _require_exact_keys(filters, {"risk_level", "decision"})
        risk_level = filters.get("risk_level")
        if risk_level is not None:
            risk_level = _require_string(risk_level, "risk_level")
            if risk_level not in RISK_LEVELS:
                raise ToolInputError(
                    "invalid_arguments", "Unsupported risk_level filter."
                )
        decision = filters.get("decision")
        if decision is not None:
            decision = _require_string(decision, "decision")
            if decision not in DECISIONS:
                raise ToolInputError(
                    "invalid_arguments", "Unsupported decision filter."
                )
        cursor = args.get("cursor")
        if cursor is not None:
            cursor = _require_string(cursor, "cursor")
            if len(cursor) > 128:
                raise ToolInputError(
                    "invalid_arguments", "cursor must contain at most 128 characters"
                )

        repository = self._repository(session_id)
        matches = repository.search_posts(
            query_text,
            risk_level=risk_level,
            decision=decision,
        )
        query_fingerprint = _search_query_fingerprint(
            query_text, risk_level=risk_level, decision=decision
        )
        ordered_post_ids_hash = _ordered_post_ids_hash(matches)
        offset = 0
        if cursor is not None:
            cursor_record = self.refs.resolve_search_cursor(
                session_id,
                cursor,
                query_fingerprint=query_fingerprint,
                ordered_post_ids_hash=ordered_post_ids_hash,
            )
            offset = cursor_record.next_offset
        if offset < 0 or offset > len(matches):
            raise ReferenceError(
                "cursor_order_mismatch",
                "The cursor offset is outside the frozen result order.",
            )

        scope = self.refs.scope(session_id)
        usage_key = (session_id, scope.generation, turn_id)
        with self._search_lock:
            usage = self._search_turn_usage.setdefault(usage_key, SearchTurnUsage())
            if (
                cursor is not None
                and usage.continuation_calls >= MAX_SEARCH_CONTINUATIONS_PER_TURN
            ):
                raise ToolInputError(
                    "search_turn_limit_reached",
                    "This turn already used the maximum of three search continuations.",
                )
            remaining_budget = (
                MAX_SEARCH_CANDIDATES_PER_TURN - usage.candidates_returned
            )
            if remaining_budget <= 0:
                raise ToolInputError(
                    "search_turn_limit_reached",
                    "This turn already returned the maximum of twenty search candidates.",
                )
            page_size = min(limit, remaining_budget)
            returned = matches[offset : offset + page_size]
            usage.search_calls += 1
            if cursor is not None:
                usage.continuation_calls += 1
            usage.candidates_returned += len(returned)
            usage_snapshot = SearchTurnUsage(
                search_calls=usage.search_calls,
                continuation_calls=usage.continuation_calls,
                candidates_returned=usage.candidates_returned,
            )

        batch = self.refs.batch(session_id)
        cards = []
        for position, match in enumerate(returned, offset + 1):
            post = match.post
            post_ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=repository.snapshot.id,
                source_tool="search_posts",
                result_kind="post_search_results",
                content_state="preview",
            )
            cards.append(
                {
                    "position": position,
                    "type": "post",
                    "name": post.name,
                    "ref": post_ref,
                    "author_display_name": post.author.display_name,
                    "preview_source": match.preview_source,
                    "preview": _search_preview(match.preview_text, query_text),
                    "preview_is_full_post": False,
                }
            )

        next_offset = offset + len(returned)
        has_more = next_offset < len(matches)
        next_cursor = None
        if has_more:
            next_cursor = self.refs.issue_search_cursor(
                session_id,
                query_fingerprint=query_fingerprint,
                ordered_post_ids_hash=ordered_post_ids_hash,
                next_offset=next_offset,
            )
        can_continue_this_turn = (
            has_more
            and usage_snapshot.continuation_calls < MAX_SEARCH_CONTINUATIONS_PER_TURN
            and usage_snapshot.candidates_returned < MAX_SEARCH_CANDIDATES_PER_TURN
        )
        if not has_more:
            stop_reason = "no_more_results"
        elif usage_snapshot.candidates_returned >= MAX_SEARCH_CANDIDATES_PER_TURN:
            stop_reason = "candidate_limit_reached"
        elif usage_snapshot.continuation_calls >= MAX_SEARCH_CONTINUATIONS_PER_TURN:
            stop_reason = "continuation_limit_reached"
        else:
            stop_reason = None

        result = self._success(
            session_id,
            tool="search_posts",
            result_kind="post_search_results",
            content_state="preview_only",
            data={
                "query_text": query_text,
                "applied_filters": {
                    "risk_level": risk_level,
                    "decision": decision,
                },
                "posts": cards,
                "matched_count": len(matches),
                "returned_count": len(returned),
                "result_position_start": offset + 1 if returned else None,
                "result_position_end": next_offset if returned else None,
                "ordering": "当前调查任务冻结帖子顺序",
                "has_more": has_more,
                "next_cursor": next_cursor,
                "continuation": {
                    "can_continue_this_turn": can_continue_this_turn,
                    "stop_reason": stop_reason,
                    "search_calls_this_turn": usage_snapshot.search_calls,
                    "continuation_calls_this_turn": usage_snapshot.continuation_calls,
                    "max_continuation_calls_per_turn": MAX_SEARCH_CONTINUATIONS_PER_TURN,
                    "candidates_returned_this_turn": usage_snapshot.candidates_returned,
                    "max_candidates_per_turn": MAX_SEARCH_CANDIDATES_PER_TURN,
                },
                "authorized_projection_post_count": repository.snapshot.projection_post_count,
                "coverage_statement": repository.snapshot.coverage_statement,
            },
            not_loaded=[
                "complete author caption and available video speech transcripts",
                "author details beyond the displayed name",
                "effective Finding and risk_basis",
                "Evidence directories and Evidence full content",
            ],
            limitations=[
                repository.snapshot.coverage_statement,
                "Only the current session's bound investigation task was searched.",
                "Search candidates are previews and must be passed to read_posts before detail or comparison.",
                "The opaque cursor is valid only for this query, scope, snapshot revision, and frozen result order.",
            ],
        )
        batch.commit()
        return result

    def search_turn_usage(self, session_id: str, turn_id: str) -> SearchTurnUsage:
        scope = self.refs.scope(session_id)
        with self._search_lock:
            usage = self._search_turn_usage.get(
                (session_id, scope.generation, turn_id), SearchTurnUsage()
            )
            return SearchTurnUsage(
                search_calls=usage.search_calls,
                continuation_calls=usage.continuation_calls,
                candidates_returned=usage.candidates_returned,
            )

    def _list_task_posts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(
            args, {"risk_level", "decision", "limit"}, required={"limit"}
        )
        limit = _require_int(args["limit"], "limit", 1, MAX_TASK_POSTS_PER_LIST)
        risk_level = args.get("risk_level")
        if risk_level is not None:
            risk_level = _require_string(risk_level, "risk_level")
            if risk_level not in RISK_LEVELS:
                raise ToolInputError(
                    "invalid_arguments", "Unsupported risk_level filter."
                )
        decision = args.get("decision")
        if decision is not None:
            decision = _require_string(decision, "decision")
            if decision not in DECISIONS:
                raise ToolInputError(
                    "invalid_arguments", "Unsupported decision filter."
                )
        repository = self._repository(session_id)
        matched = [
            post
            for post in repository.ordered_posts()
            if (risk_level is None or post.finding.risk_level == risk_level)
            and (decision is None or post.finding.decision == decision)
        ]
        returned = matched[:limit]
        batch = self.refs.batch(session_id)
        cards = []
        for position, post in enumerate(returned, 1):
            ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=repository.snapshot.id,
                source_tool="list_task_posts",
                result_kind="task_post_directory",
                content_state="preview",
            )
            cards.append(
                {
                    "position": position,
                    "type": "post",
                    "name": post.name,
                    "ref": ref,
                    "author_display_name": post.author.display_name,
                    "decision": post.finding.decision,
                    "risk_level": post.finding.risk_level,
                    "preview": _preview(post.finding.summary),
                    "preview_is_full_post": False,
                }
            )
        result = self._success(
            session_id,
            tool="list_task_posts",
            result_kind="task_post_directory",
            content_state="preview_only",
            data={
                "task": {
                    "type": "interrupted_task_snapshot",
                    "name": repository.snapshot.task_name,
                    "status": repository.snapshot.job_status,
                },
                "posts": cards,
                "matched_count": len(matched),
                "returned_count": len(returned),
                "truncated": len(returned) < len(matched),
                "authorized_projection_post_count": repository.snapshot.projection_post_count,
                "authorized_projection_comment_count": repository.snapshot.projection_comment_count,
                "counted_risk_evidence": repository.snapshot.counted_risk_evidence,
                "readable_risk_evidence": repository.snapshot.readable_risk_evidence,
                "coverage_statement": repository.snapshot.coverage_statement,
                "applied_filters": {"risk_level": risk_level, "decision": decision},
            },
            not_loaded=[
                "complete author caption and available video speech transcripts",
                "effective Finding detail and risk_basis",
                "Evidence directories and Evidence full content",
            ],
            limitations=[
                repository.snapshot.coverage_statement,
                "Search/list matches establish candidate handles only and are not full post reads.",
                "Evidence counts do not convert ordinary comments into risk Evidence.",
            ],
        )
        batch.commit()
        return result

    def _read_posts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"post_refs"}, required={"post_refs"})
        refs = _require_string_list(
            args["post_refs"], "post_refs", minimum=1, maximum=MAX_POSTS_PER_READ
        )
        repository = self._repository(session_id)
        groups = []
        for position, ref in enumerate(refs, 1):
            record = self.refs.resolve(session_id, ref, expected_kind="post")
            post = repository.post(record.object_id)
            self._validate_post(repository, post, record.parent_id)
            groups.append(
                {
                    "group_position": position,
                    "post": {
                        "type": "post",
                        "name": post.name,
                        "ref": ref,
                    },
                    "post_content": {
                        "author_caption": post.author_caption,
                        "videos": [
                            _project_video_transcript(video) for video in post.videos
                        ],
                    },
                    "author": {
                        "display_name": post.author.display_name,
                        "avatar_url": post.author.avatar_url,
                        "platform": post.author.platform,
                    },
                    "effective_finding": {
                        "type": "finding",
                        "decision": post.finding.decision,
                        "risk_level": post.finding.risk_level,
                        "categories": list(post.finding.categories),
                        "summary": post.finding.summary,
                        "risk_basis": post.finding.risk_basis,
                        "completed_at": post.finding.completed_at.isoformat(),
                    },
                }
            )
        return self._success(
            session_id,
            tool="read_posts",
            result_kind="post_detail",
            content_state="post_content_and_effective_finding",
            data={"post_groups": groups, "returned_count": len(groups)},
            not_loaded=[
                "comments",
                "Evidence candidate directories and full Evidence content",
                "binary video, images, OCR, and other media-derived records",
            ],
            limitations=[
                "An empty author_caption means no caption is stored; it does not make a transcript into a caption.",
                "Each video exposes either its existing translated transcript or its original-language transcript, never both by default.",
                "Finding fields are separate frozen audit conclusions; no new risk inference was performed.",
            ],
        )

    def _list_evidence(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"post_ref", "types", "limit"}, required={"post_ref"})
        post_ref = _require_string(args["post_ref"], "post_ref")
        repository = self._repository(session_id)
        record = self.refs.resolve(session_id, post_ref, expected_kind="post")
        post = repository.post(record.object_id)
        self._validate_post(repository, post, record.parent_id)
        requested_types = None
        if "types" in args:
            requested_types = set(
                _require_string_list(args["types"], "types", minimum=1, maximum=5)
            )
            unknown = requested_types - EVIDENCE_TYPES
            if unknown:
                raise ToolInputError(
                    "invalid_arguments",
                    f"unknown Evidence types: {', '.join(sorted(unknown))}",
                )
        limit = _require_int(args.get("limit", 10), "limit", 1, MAX_EVIDENCE_PER_LIST)
        all_items = repository.evidence_for_post(post.id)
        selected = [
            item
            for item in all_items
            if not requested_types or item.evidence_type in requested_types
        ]
        returned = selected[:limit]
        batch = self.refs.batch(session_id)
        cards = []
        for item in returned:
            evidence_ref = batch.expose(
                kind="evidence",
                object_id=item.id,
                parent_id=post.id,
                source_tool="list_evidence",
                result_kind="evidence_candidate_directory",
                content_state="preview",
            )
            cards.append(
                {
                    "position": item.ordinal,
                    "type": "evidence",
                    "evidence_type": item.evidence_type,
                    "name": f"评论风险 Evidence {item.ordinal}",
                    "ref": evidence_ref,
                    "parent": {"type": "post", "name": post.name, "ref": post_ref},
                    "author_display_name": item.author_display_name,
                    "risk_type": item.risk_type,
                    "preview": _preview(item.original_text),
                    "preview_is_complete": False,
                }
            )
        result = self._success(
            session_id,
            tool="list_evidence",
            result_kind="evidence_candidate_directory",
            content_state="preview_only",
            data={
                "parent_post": {"type": "post", "name": post.name, "ref": post_ref},
                "candidates": cards,
                "matched_count": len(selected),
                "returned_count": len(returned),
                "truncated": len(returned) < len(selected),
                "applied_types": sorted(requested_types) if requested_types else [],
                "evidence_semantics": "Only frozen effective risk Evidence from audit_results is listed; ordinary comments are excluded.",
            },
            not_loaded=[
                "full original comment text",
                "existing full Chinese translation",
                "full audit reason",
            ],
            limitations=[
                "This is a candidate directory. Preview text must not be treated as full Evidence."
            ],
        )
        batch.commit()
        return result

    def _read_evidence(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"evidence_refs"}, required={"evidence_refs"})
        refs = _require_string_list(
            args["evidence_refs"],
            "evidence_refs",
            minimum=1,
            maximum=MAX_EVIDENCE_PER_READ,
        )
        repository = self._repository(session_id)
        groups = []
        for position, ref in enumerate(refs, 1):
            record = self.refs.resolve(session_id, ref, expected_kind="evidence")
            item = repository.evidence(record.object_id)
            if record.parent_id != item.parent_post_id:
                raise ReferenceError(
                    "wrong_parent_ref",
                    "Evidence reference parent does not match frozen data.",
                )
            post = repository.post(item.parent_post_id)
            if item.parent_revision_id != post.revision_id:
                raise ReferenceError(
                    "stale_revision_ref", "Evidence belongs to another post revision."
                )
            post_ref = self.refs.ref_for(session_id, "post", post.id)
            groups.append(
                {
                    "group_position": position,
                    "evidence": {
                        "type": "evidence",
                        "evidence_type": item.evidence_type,
                        "name": f"评论风险 Evidence {item.ordinal}",
                        "ref": ref,
                        "full_original_text": item.original_text,
                        "existing_chinese_translation": item.translated_text,
                        "risk_type": item.risk_type,
                        "audit_reason": item.audit_reason,
                        "severity": item.severity,
                    },
                    "parent_post": {"type": "post", "name": post.name, "ref": post_ref},
                    "natural_source": {
                        "source_type": "frozen_audit_comment",
                        "author_display_name": item.author_display_name,
                        "source_label": item.source_label,
                        "canonical_locator_withheld": True,
                    },
                }
            )
        return self._success(
            session_id,
            tool="read_evidence",
            result_kind="evidence_full_content",
            content_state="complete_text_record",
            data={"evidence_groups": groups, "returned_count": len(groups)},
            not_loaded=[
                "ordinary comments",
                "binary media",
                "account identity or real-world relationship",
            ],
            limitations=[
                "The existing Chinese translation is returned verbatim and must not be retranslated.",
                "Display names and text do not establish account identity or real-world relationships.",
            ],
        )

    def _validate_post(
        self, repository: TaskRepository, post: TaskPost, parent_id: str | None
    ) -> None:
        if post.id not in {item.id for item in repository.snapshot.posts}:
            raise ReferenceError("cross_scope_ref", "Post is outside the TaskSnapshot.")
        if parent_id != repository.snapshot.id:
            raise ReferenceError(
                "wrong_parent_ref", "Post reference parent does not match TaskSnapshot."
            )
        if post.revision_id not in {
            item.revision_id for item in repository.snapshot.posts
        }:
            raise ReferenceError(
                "stale_revision_ref", "Post revision is not frozen in scope."
            )

    def _success(self, session_id: str, **kwargs: Any) -> str:
        repository = self._repository(session_id)
        return success_result(
            scope={
                "task_name": repository.snapshot.task_name,
                "task_status": repository.snapshot.job_status,
                "frozen_snapshot": True,
                "revision_label": repository.snapshot.revision.split(":", 1)[0],
            },
            authority_basis="frozen_task_audit_results_projection",
            **kwargs,
        )


def _project_video_transcript(video: TaskVideoTranscript) -> dict[str, Any]:
    if video.translation_translated and video.existing_translation_text:
        label = "已有中文语音转写"
        text = video.existing_translation_text
    elif video.original_text:
        label = "原语言语音转写"
        text = video.original_text
    else:
        label = "当前没有可用语音转写"
        text = ""
    return {
        "position": video.ordinal,
        "speech_transcript": {"label": label, "text": text},
    }


def _search_query_fingerprint(
    query_text: str,
    *,
    risk_level: str | None,
    decision: str | None,
) -> str:
    encoded = json.dumps(
        {
            "query_text": normalize_search_text(query_text),
            "risk_level": risk_level,
            "decision": decision,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ordered_post_ids_hash(matches: tuple[TaskPostSearchMatch, ...]) -> str:
    encoded = "\0".join(match.post.id for match in matches).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _search_preview(text: str, query_text: str, limit: int = 160) -> str:
    visible = " ".join(text.split())
    if len(visible) <= limit:
        return visible
    normalized_query = normalize_search_text(query_text)
    needles = (normalized_query, *normalized_query.split())
    folded = visible.casefold()
    index = next(
        (
            folded.find(needle)
            for needle in needles
            if needle and folded.find(needle) >= 0
        ),
        0,
    )
    start = max(0, index - 40)
    end = min(len(visible), start + limit - 6)
    if end - start < limit - 6:
        start = max(0, end - (limit - 6))
    return (
        ("..." if start else "")
        + visible[start:end]
        + ("..." if end < len(visible) else "")
    )
