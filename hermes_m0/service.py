"""Five read-only Investigation tools for one Hermes session scope."""

from __future__ import annotations

from typing import Any, Callable

from hermes_m0.domain import Evidence, Post
from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.refs import ReferenceError, SessionReferenceRegistry, SessionScope
from hermes_m0.repository import InvestigationRepository, RepositoryLookupError
from hermes_m0.tool_results import error_result, success_result


MAX_POSTS_PER_READ = 4
MAX_EVIDENCE_PER_LIST = 20
MAX_EVIDENCE_PER_READ = 4
EVIDENCE_TYPES = frozenset({"text", "comment", "asr", "ocr", "keyframe"})


class ToolInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InvestigationToolService:
    def __init__(
        self,
        repository: InvestigationRepository,
        refs: SessionReferenceRegistry | None = None,
        ledger: ToolExecutionLedger | None = None,
    ) -> None:
        self.repository = repository
        self.refs = refs or SessionReferenceRegistry()
        self.ledger = ledger
        self._handlers: dict[str, Callable[[str, dict[str, Any]], str]] = {
            "read_report": self._read_report,
            "list_case_members": self._list_case_members,
            "read_posts": self._read_posts,
            "list_evidence": self._list_evidence,
            "read_evidence": self._read_evidence,
        }

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

    def bind_session(
        self, session_id: str, *, force_new_generation: bool = False
    ) -> SessionScope:
        report = self.repository.report
        snapshot = self.repository.snapshot
        if report.snapshot_id != snapshot.id:
            raise RuntimeError("Repository ReportVersion/FrozenSnapshot mismatch")
        return self.refs.bind(
            session_id=session_id,
            report_version_id=report.id,
            snapshot_id=snapshot.id,
            domain_revision=report.revision,
            force_new_generation=force_new_generation,
        )

    def dispatch(self, tool_name: str, args: dict[str, Any], *, session_id: str) -> str:
        handler = self._handlers.get(tool_name)
        if handler is None:
            return error_result(
                tool=tool_name,
                code="unknown_tool",
                message="Only the five Investigation M0 tools are available.",
            )
        try:
            self.refs.scope(session_id)
            if not isinstance(args, dict):
                raise ToolInputError("invalid_arguments", "Tool arguments must be an object.")
            return handler(session_id, args)
        except (ReferenceError, ToolInputError) as exc:
            return error_result(tool=tool_name, code=exc.code, message=exc.message)
        except RepositoryLookupError:
            return error_result(
                tool=tool_name,
                code="scope_object_missing",
                message="The referenced object is not present in the bound frozen scope.",
            )

    def _read_report(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, set())
        report = self.repository.report
        batch = self.refs.batch(session_id)
        cards = []
        for case in self.repository.ordered_cases():
            ref = batch.expose(
                kind="case",
                object_id=case.id,
                parent_id=report.id,
                source_tool="read_report",
                result_kind="report_overview",
                content_state="preview",
            )
            cards.append(
                {
                    "position": case.position,
                    "type": "report_case",
                    "name": case.title,
                    "ref": ref,
                    "summary": case.summary,
                    "report_member_count": len(case.member_post_ids),
                }
            )
        result = success_result(
            tool="read_report",
            result_kind="report_overview",
            content_state="overview_with_case_previews",
            scope=self._public_scope(),
            data={
                "report": {
                    "type": "report_version",
                    "name": report.title,
                    "overview": report.overview,
                    "published_at": report.published_at.isoformat(),
                    "statistics": report.statistics.model_dump(mode="json"),
                },
                "case_previews": cards,
                "case_preview_count": len(cards),
                "case_previews_complete": True,
            },
            not_loaded=[
                "case member lists",
                "post full bodies and findings",
                "Evidence candidate directories and full Evidence content",
            ],
            limitations=[
                "Case previews describe this published report only.",
                "No post or Evidence content was loaded by this tool.",
            ],
        )
        batch.commit()
        return result

    def _list_case_members(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"case_ref"}, required={"case_ref"})
        case_ref = _require_string(args["case_ref"], "case_ref")
        record = self.refs.resolve(session_id, case_ref, expected_kind="case")
        case = self.repository.case(record.object_id)
        if case.id not in self.repository.report.case_ids:
            raise ReferenceError("cross_scope_ref", "ReportCase is outside the bound report.")
        if record.parent_id != self.repository.report.id:
            raise ReferenceError(
                "wrong_parent_ref", "ReportCase reference parent does not match the report."
            )
        batch = self.refs.batch(session_id)
        cards = []
        for position, post in enumerate(self.repository.case_posts(case.id), 1):
            post_ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=case.id,
                source_tool="list_case_members",
                result_kind="case_member_directory",
                content_state="preview",
            )
            cards.append(self._post_preview(post, post_ref, case_ref, case.title, position))
        result = success_result(
            tool="list_case_members",
            result_kind="case_member_directory",
            content_state="preview_only",
            scope=self._public_scope(),
            data={
                "case": {"type": "report_case", "name": case.title, "ref": case_ref},
                "members": cards,
                "returned_count": len(cards),
                "membership_semantics": (
                    "These are ordered posts selected for display in this ReportCase. "
                    "This result is not a search of every post in the FrozenSnapshot."
                ),
            },
            not_loaded=[
                "post full bodies",
                "effective Finding details",
                "Evidence candidate directories and full Evidence content",
            ],
            limitations=[
                "Membership is the report's displayed selection, not snapshot-wide discovery."
            ],
        )
        batch.commit()
        return result

    def _read_posts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"post_refs"}, required={"post_refs"})
        refs = _require_string_list(
            args["post_refs"], "post_refs", minimum=1, maximum=MAX_POSTS_PER_READ
        )
        groups = []
        for position, post_ref in enumerate(refs, 1):
            record = self.refs.resolve(session_id, post_ref, expected_kind="post")
            post = self.repository.post(record.object_id)
            self._validate_post_scope(post)
            self._validate_post_parent(record.parent_id, post)
            finding = self.repository.finding_for_post(post.id)
            groups.append(
                {
                    "group_position": position,
                    "post": {
                        "type": "post",
                        "name": post.title,
                        "ref": post_ref,
                        "full_body": post.body,
                        "author": {
                            "display_name": post.author.display_name,
                            "avatar_url": post.author.avatar_url,
                            "platform": post.author.platform,
                        },
                        "source": {
                            "platform": post.source.platform,
                            "url": post.source.url,
                            "published_at": post.source.published_at,
                            "captured_at": (
                                post.source.captured_at.isoformat()
                                if post.source.captured_at is not None
                                else ""
                            ),
                        },
                    },
                    "effective_finding": {
                        "type": "finding",
                        "decision": finding.decision,
                        "risk_level": finding.risk_level,
                        "categories": list(finding.categories),
                        "summary": finding.summary,
                        "completed_at": finding.completed_at.isoformat(),
                    },
                }
            )
        return success_result(
            tool="read_posts",
            result_kind="post_full_content",
            content_state="complete_post_and_effective_finding",
            scope=self._public_scope(),
            data={"post_groups": groups, "returned_count": len(groups)},
            not_loaded=["Evidence candidate directories and full Evidence content"],
            limitations=[
                "Finding fields are frozen report data; no new risk inference was performed."
            ],
        )

    def _list_evidence(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(
            args, {"post_ref", "types", "limit"}, required={"post_ref"}
        )
        post_ref = _require_string(args["post_ref"], "post_ref")
        record = self.refs.resolve(session_id, post_ref, expected_kind="post")
        post = self.repository.post(record.object_id)
        self._validate_post_scope(post)
        self._validate_post_parent(record.parent_id, post)
        requested_types = None
        if "types" in args:
            requested_types = set(
                _require_string_list(args["types"], "types", minimum=1, maximum=5)
            )
            unknown_types = requested_types - EVIDENCE_TYPES
            if unknown_types:
                raise ToolInputError(
                    "invalid_arguments",
                    f"unknown Evidence types: {', '.join(sorted(unknown_types))}",
                )
        limit = _require_int(args.get("limit", 10), "limit", 1, MAX_EVIDENCE_PER_LIST)
        all_items = self.repository.evidence_for_post(post.id)
        selected = [item for item in all_items if not requested_types or item.type in requested_types]
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
                    "evidence_type": item.type,
                    "name": _evidence_name(item),
                    "ref": evidence_ref,
                    "parent": {"type": "post", "name": post.title, "ref": post_ref},
                    "preview": _preview(item.content.original_text),
                    "preview_is_complete": len(item.content.original_text) <= 160,
                }
            )
        result = success_result(
            tool="list_evidence",
            result_kind="evidence_candidate_directory",
            content_state="preview_only",
            scope=self._public_scope(),
            data={
                "parent_post": {"type": "post", "name": post.title, "ref": post_ref},
                "candidates": cards,
                "matched_count": len(selected),
                "returned_count": len(returned),
                "truncated": len(returned) < len(selected),
                "applied_types": sorted(requested_types) if requested_types else [],
            },
            not_loaded=["full Evidence original text and detail fields"],
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
        groups = []
        for position, evidence_ref in enumerate(refs, 1):
            record = self.refs.resolve(session_id, evidence_ref, expected_kind="evidence")
            item = self.repository.evidence(record.object_id)
            self._validate_evidence_scope(item)
            if record.parent_id != item.parent_post_id:
                raise ReferenceError(
                    "wrong_parent_ref", "Evidence reference parent does not match frozen data."
                )
            post = self.repository.post(item.parent_post_id)
            post_ref = self.refs.ref_for(session_id, "post", post.id)
            groups.append(
                {
                    "group_position": position,
                    "evidence": {
                        "type": "evidence",
                        "evidence_type": item.type,
                        "name": _evidence_name(item),
                        "ref": evidence_ref,
                        "full_original_text": item.content.original_text,
                        "translated_text": item.content.translated_text,
                        "recorded_summary": item.content.summary,
                    },
                    "parent_post": {"type": "post", "name": post.title, "ref": post_ref},
                    "natural_source": _natural_source(item, post),
                }
            )
        return success_result(
            tool="read_evidence",
            result_kind="evidence_full_content",
            content_state="complete_text_record",
            scope=self._public_scope(),
            data={"evidence_groups": groups, "returned_count": len(groups)},
            not_loaded=["binary image, frame, audio, or video bytes"],
            limitations=[
                "The complete stored text record is returned; linked binary media is not loaded."
            ],
        )

    def _public_scope(self) -> dict[str, Any]:
        report = self.repository.report
        return {
            "report_name": report.title,
            "report_revision": report.public_revision,
            "frozen_snapshot": True,
        }

    def _validate_post_scope(self, post: Post) -> None:
        if post.id not in self.repository.snapshot.post_ids:
            raise ReferenceError("cross_scope_ref", "Post is outside the FrozenSnapshot.")
        if post.revision_id not in self.repository.snapshot.post_revision_ids:
            raise ReferenceError("stale_revision_ref", "Post revision is not frozen in scope.")

    def _validate_post_parent(self, parent_id: str | None, post: Post) -> None:
        case = self.repository.case_for_post(post.id)
        if parent_id != case.id:
            raise ReferenceError(
                "wrong_parent_ref", "Post reference parent does not match the ReportCase."
            )

    def _validate_evidence_scope(self, item: Evidence) -> None:
        if item.id not in self.repository.snapshot.evidence_ids:
            raise ReferenceError("cross_scope_ref", "Evidence is outside the FrozenSnapshot.")
        post = self.repository.post(item.parent_post_id)
        if item.parent_revision_id != post.revision_id:
            raise ReferenceError(
                "wrong_parent_ref", "Evidence belongs to another Post revision."
            )

    @staticmethod
    def _post_preview(
        post: Post,
        post_ref: str,
        case_ref: str,
        case_name: str,
        position: int,
    ) -> dict[str, Any]:
        return {
            "position": position,
            "type": "post",
            "name": post.title,
            "ref": post_ref,
            "author_display_name": post.author.display_name,
            "platform": post.source.platform,
            "parent": {"type": "report_case", "name": case_name, "ref": case_ref},
        }


def _require_exact_keys(
    args: dict[str, Any], allowed: set[str], *, required: set[str] | None = None
) -> None:
    extra = set(args) - allowed
    missing = (required or set()) - set(args)
    if extra or missing:
        details = []
        if extra:
            details.append(f"unexpected: {', '.join(sorted(extra))}")
        if missing:
            details.append(f"missing: {', '.join(sorted(missing))}")
        raise ToolInputError("invalid_arguments", "; ".join(details))


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError("invalid_arguments", f"{name} must be a non-empty string")
    return value.strip()


def _require_string_list(
    value: Any, name: str, *, minimum: int, maximum: int
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ToolInputError(
            "invalid_arguments", f"{name} must contain between {minimum} and {maximum} strings"
        )
    items = [_require_string(item, name) for item in value]
    if len(items) != len(set(items)):
        raise ToolInputError("invalid_arguments", f"{name} must not contain duplicates")
    return items


def _require_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolInputError(
            "invalid_arguments", f"{name} must be an integer from {minimum} to {maximum}"
        )
    return value


def _preview(text: str, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def _evidence_name(item: Evidence) -> str:
    labels = {
        "text": "帖子正文",
        "comment": "评论",
        "asr": "视频语音转写",
        "ocr": "视频画面文字识别",
        "keyframe": "风险关键帧",
    }
    return f"Evidence {item.ordinal} · {labels[item.type]}"


def _natural_source(item: Evidence, post: Post) -> dict[str, Any]:
    labels = {
        "text": "帖子正文",
        "comment": "帖子评论",
        "asr": "视频音轨转写",
        "ocr": "视频画面文字识别",
        "keyframe": "视频关键帧",
    }
    source: dict[str, Any] = {
        "platform": post.source.platform,
        "source_kind": labels[item.type],
        "availability": item.source.availability,
        "captured_at": item.source.captured_at.isoformat(),
    }
    if item.content.timestamp_start is not None:
        source["timestamp_start_seconds"] = item.content.timestamp_start
    if item.content.timestamp_end is not None:
        source["timestamp_end_seconds"] = item.content.timestamp_end
    return source
