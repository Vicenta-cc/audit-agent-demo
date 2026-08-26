"""Account Overview and occurrence navigation for M2."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hermes_m0.account_activity_refs import AccountActivityReferenceRegistry
from hermes_m0.account_activity_repository import (
    DEFAULT_ACCOUNT_CORPUS_PATH,
    AccountActivityLookupError,
    AccountActivityRepository,
)
from hermes_m0.domain import Post
from hermes_m0.real_report_repository import PublishedReportRepository
from hermes_m0.repository import RepositoryLookupError
from hermes_m0.refs import ReferenceError
from hermes_m0.service import (
    ToolInputError,
    _require_exact_keys,
    _require_int,
    _require_string,
)
from hermes_m0.tool_results import error_result, success_result


MAX_ACCOUNT_OCCURRENCES_PER_PAGE = 20


class AccountActivityToolService:
    """Loads current Account data for each new call; ledger replay bypasses it."""

    def __init__(
        self,
        report_repository: PublishedReportRepository,
        *,
        corpus_path: Path | str = DEFAULT_ACCOUNT_CORPUS_PATH,
        refs: AccountActivityReferenceRegistry | None = None,
        repository_loader: Callable[[], AccountActivityRepository] | None = None,
        authorized_report_repositories: tuple[PublishedReportRepository, ...] | None = None,
    ) -> None:
        self.report_repository = report_repository
        self.corpus_path = Path(corpus_path)
        self.refs = refs or AccountActivityReferenceRegistry()
        self._repository_loader = repository_loader or (
            lambda: AccountActivityRepository.load(self.corpus_path)
        )
        self.authorized_report_repositories = authorized_report_repositories or (
            report_repository,
        )
        self.repository_load_count = 0
        self._handlers = {
            "get_account_overview": self._get_account_overview,
            "list_account_occurrences": self._list_account_occurrences,
            "read_account_occurrence": self._read_account_occurrence,
            "read_account_post": self._read_account_post,
        }

    def bind_session(
        self,
        session_id: str,
        *,
        task_id: str,
        report_version_id: str,
        snapshot_id: str,
        report_revision: str,
        snapshot_hash: str,
        content_hash: str,
        force_new_generation: bool,
    ) -> None:
        self.refs.bind(
            session_id=session_id,
            task_id=task_id,
            report_version_id=report_version_id,
            snapshot_id=snapshot_id,
            report_revision=report_revision,
            snapshot_hash=snapshot_hash,
            content_hash=content_hash,
            force_new_generation=force_new_generation,
        )

    def report_entries(self, session_id: str) -> list[dict[str, Any]]:
        repository = self._load_repository()
        entries = []
        for position, item in enumerate(
            repository.report_account_entries(self.report_repository), 1
        ):
            current_statistics = item.current_statistics or {
                "published_post_count": item.published_post_count,
                "comment_count": item.comment_count,
            }
            account_ref = self.refs.expose(
                session_id,
                kind="account",
                object_id=item.account_id,
                parent_account_id=None,
                corpus_revision=repository.corpus_revision,
                source_tool="read_report",
                content_state="current_investigation_account_card",
            )
            entries.append(
                {
                    "position": position,
                    "type": "account_activity_entry",
                    "display_name": item.display_name,
                    "account_ref": account_ref,
                    "report_role": item.roles[0],
                    "report_roles": list(item.roles),
                    "current_investigation_statistics": current_statistics,
                    "report_group_placement": {
                        "target_display_order": item.target_display_order,
                        "active_comment_display_order": item.active_comment_display_order,
                        "default_active_comment_visible": (
                            item.default_active_comment_visible
                        ),
                    },
                    "scope_boundary": (
                        "此卡片只统计当前调查；进入 Account Overview 后默认查询当前用户"
                        "已授权的全部调查数据。"
                    ),
                    "preview_only": True,
                }
            )
        return entries

    def expose_account(
        self,
        session_id: str,
        account_id: str,
        *,
        source_tool: str,
        content_state: str,
        repository: AccountActivityRepository | None = None,
    ) -> str:
        current = repository or self._load_repository()
        current.display_name(account_id)
        return self.refs.expose(
            session_id,
            kind="account",
            object_id=account_id,
            parent_account_id=None,
            corpus_revision=current.corpus_revision,
            source_tool=source_tool,
            content_state=content_state,
        )

    def expose_post_author(
        self, session_id: str, *, task_id: str, content_key: str
    ) -> str | None:
        repository = self._load_repository()
        occurrence = repository.post_occurrence(task_id, content_key)
        account_id = occurrence.get("account_ref")
        if account_id is None:
            return None
        return self.expose_account(
            session_id,
            str(account_id),
            source_tool="read_posts",
            content_state="post_author_account",
            repository=repository,
        )

    def expose_risk_comments(
        self,
        session_id: str,
        *,
        task_id: str,
        content_key: str,
        comments: tuple[dict[str, Any], ...],
    ) -> dict[str, dict[str, str | None]]:
        repository = self._load_repository()
        output: dict[str, dict[str, str | None]] = {}
        for expected in comments:
            comment_id = str(expected["comment_id"])
            occurrence = repository.comment_occurrence(
                task_id, content_key, comment_id
            )
            frozen = occurrence["comment"]
            if (
                occurrence.get("audit_status") != expected["audit_status"]
                or occurrence.get("risk_level") != expected["risk_level"]
                or frozen.get("risk_type") != expected["risk_type"]
                or frozen.get("text") != expected["text"]
                or frozen.get("comment_time") != expected["published_at"]
            ):
                raise AccountActivityLookupError(
                    "snapshot_occurrence_mismatch",
                    "The frozen Report Comment and Account occurrence disagree.",
                )
            account_id = occurrence.get("account_ref")
            account_token = None
            occurrence_token = None
            if account_id is not None:
                account_token = self.expose_account(
                    session_id,
                    str(account_id),
                    source_tool="list_post_risk_comments",
                    content_state="comment_author_account",
                    repository=repository,
                )
                occurrence_token = self.refs.expose(
                    session_id,
                    kind="occurrence",
                    object_id=str(occurrence["occurrence_ref"]),
                    parent_account_id=str(account_id),
                    corpus_revision=repository.corpus_revision,
                    source_tool="list_post_risk_comments",
                    content_state="preview",
                )
            output[comment_id] = {
                "account_ref": account_token,
                "occurrence_ref": occurrence_token,
            }
        return output

    def _resolve_account(
        self,
        session_id: str,
        token: str,
        repository: AccountActivityRepository,
    ) -> tuple[Any, str]:
        if token.startswith("account-entry-"):
            try:
                entry = self.report_repository.report_account_entry(token)
            except RepositoryLookupError as exc:
                raise ReferenceError(
                    "unknown_ref",
                    "The Report Account entry is outside the bound ReportVersion.",
                ) from exc
            token = self.expose_account(
                session_id,
                entry.account_id,
                source_tool="report_account_index",
                content_state="stable_report_account_entry",
                repository=repository,
            )
        record = self.refs.resolve(
            session_id,
            token,
            expected_kind="account",
            corpus_revision=repository.corpus_revision,
        )
        return record, token

    def dispatch(
        self, tool_name: str, args: dict[str, Any], *, session_id: str
    ) -> str:
        handler = self._handlers.get(tool_name)
        if handler is None:
            return error_result(
                tool=tool_name,
                code="tool_unavailable_for_account_activity",
                message="This tool is not part of Account Activity.",
            )
        try:
            if not isinstance(args, dict):
                raise ToolInputError(
                    "invalid_arguments", "Tool arguments must be an object."
                )
            return handler(session_id, args)
        except (ReferenceError, ToolInputError, AccountActivityLookupError) as exc:
            return error_result(tool=tool_name, code=exc.code, message=exc.message)

    def _get_account_overview(
        self, session_id: str, args: dict[str, Any]
    ) -> str:
        _require_exact_keys(args, {"account_ref"}, required={"account_ref"})
        account_ref = _require_string(args["account_ref"], "account_ref")
        repository = self._load_repository()
        account_record, session_account_ref = self._resolve_account(
            session_id, account_ref, repository
        )
        overview = repository.overview(account_record.object_id)
        target_distribution = []
        for item in overview["comment_target_distribution"]:
            target_account_id = str(item["account_id"])
            target_ref = self.refs.expose(
                session_id,
                kind="comment_target",
                object_id=target_account_id,
                parent_account_id=account_record.object_id,
                corpus_revision=repository.corpus_revision,
                source_tool="get_account_overview",
                content_state="comment_target_distribution",
            )
            target_account_ref = self.expose_account(
                session_id,
                target_account_id,
                source_tool="get_account_overview",
                content_state="comment_target_account",
                repository=repository,
            )
            target_distribution.append(
                {
                    "position": item["position"],
                    "author_display_name": item["author_display_name"],
                    "comment_count": item["comment_count"],
                    "comment_target_ref": target_ref,
                    "account_ref": target_account_ref,
                }
            )
        source_distribution = [
            {
                key: value
                for key, value in item.items()
                if key != "task_id"
            }
            for item in overview["activity_source_distribution"]
        ]
        return self._success(
            tool="get_account_overview",
            result_kind="account_activity_overview",
            content_state="complete_deterministic_statistics",
            data={
                "account": {
                    "type": "account",
                    "display_name": overview["display_name"],
                    "ref": session_account_ref,
                },
                "statistics": overview["statistics"],
                "comment_target_distribution": target_distribution,
                "activity_source_distribution": source_distribution,
                "activity_source_tasks": overview["activity_source_tasks"],
                "authorization_scope": {
                    "mode": "all_currently_authorized_investigations",
                    "authorized_investigation_count": overview[
                        "authorized_task_count"
                    ],
                    "user_switchable": False,
                },
                "risk_statistics": {
                    "comment_risk_rate_available": False,
                    "parent_post_risk_is_not_inherited": True,
                    "published_post_risk_count_available": False,
                },
            },
            not_loaded=[
                "individual occurrence detail",
                "historical AuditFinding or Evidence",
                "comment risk rate",
            ],
            limitations=[
                "Counts and ordering are computed by the server over all currently authorized Account data.",
                "Source investigations are metadata, not user-selectable query Scope.",
                "No comment inherits risk from its parent Post.",
            ],
        )

    def _list_account_occurrences(
        self, session_id: str, args: dict[str, Any]
    ) -> str:
        _require_exact_keys(
            args,
            {
                "account_ref",
                "kind",
                "limit",
                "cursor",
                "comment_target_ref",
                "risk_filter",
            },
            required={"account_ref", "kind", "limit"},
        )
        account_ref = _require_string(args["account_ref"], "account_ref")
        kind = _require_string(args["kind"], "kind")
        limit = _require_int(
            args["limit"],
            "limit",
            minimum=1,
            maximum=MAX_ACCOUNT_OCCURRENCES_PER_PAGE,
        )
        cursor = (
            None
            if "cursor" not in args
            else _require_string(args["cursor"], "cursor")
        )
        repository = self._load_repository()
        account_record, session_account_ref = self._resolve_account(
            session_id, account_ref, repository
        )
        risk_filter = (
            None
            if "risk_filter" not in args
            else _require_string(args["risk_filter"], "risk_filter")
        )
        comment_target_ref = (
            None
            if "comment_target_ref" not in args
            else _require_string(args["comment_target_ref"], "comment_target_ref")
        )
        comment_target_account_id = None
        if comment_target_ref is not None:
            target_record = self.refs.resolve(
                session_id,
                comment_target_ref,
                expected_kind="comment_target",
                corpus_revision=repository.corpus_revision,
                expected_source_tool="get_account_overview",
                expected_content_state="comment_target_distribution",
            )
            if target_record.parent_account_id != account_record.object_id:
                raise ReferenceError(
                    "wrong_parent_ref",
                    "The comment target was not displayed for this Account.",
                )
            comment_target_account_id = target_record.object_id
        values = repository.ordered_occurrences(
            account_record.object_id,
            kind=kind,
            comment_target_account_id=comment_target_account_id,
            risk_filter=risk_filter,
        )
        ordered_hash = repository.ordered_occurrence_hash(values)
        offset = 0
        if cursor is not None:
            cursor_record = self.refs.resolve_cursor(
                session_id,
                cursor,
                account_id=account_record.object_id,
                kind=kind,
                comment_target_account_id=comment_target_account_id,
                risk_filter=risk_filter,
                corpus_revision=repository.corpus_revision,
                ordered_occurrence_hash=ordered_hash,
            )
            offset = cursor_record.next_offset
        selected = values[offset : offset + limit]
        next_offset = offset + len(selected)
        next_cursor = (
            None
            if next_offset >= len(values)
            else self.refs.issue_cursor(
                session_id,
                account_id=account_record.object_id,
                kind=kind,
                comment_target_account_id=comment_target_account_id,
                risk_filter=risk_filter,
                corpus_revision=repository.corpus_revision,
                ordered_occurrence_hash=ordered_hash,
                next_offset=next_offset,
            )
        )
        risk_keys = self._report_risk_post_keys()
        projected = []
        for position, occurrence in enumerate(selected, offset + 1):
            occurrence_ref = self.refs.expose(
                session_id,
                kind="occurrence",
                object_id=str(occurrence["occurrence_ref"]),
                parent_account_id=account_record.object_id,
                corpus_revision=repository.corpus_revision,
                source_tool="list_account_occurrences",
                content_state="preview",
            )
            item = repository.occurrence_preview(
                occurrence,
                occurrence_ref=occurrence_ref,
                report_risk_post_keys=risk_keys,
            )
            item["position"] = position
            projected.append(item)
        return self._success(
            tool="list_account_occurrences",
            result_kind="account_activity_occurrence_directory",
            content_state="bounded_occurrence_previews",
            data={
                "account": {
                    "type": "account",
                    "display_name": repository.display_name(account_record.object_id),
                    "ref": session_account_ref,
                },
                "kind": kind,
                "risk_filter": risk_filter,
                "comment_target_filter": (
                    None
                    if comment_target_account_id is None
                    else {
                        "author_display_name": repository.display_name(
                            comment_target_account_id
                        ),
                        "comment_target_ref": comment_target_ref,
                    }
                ),
                "matched_count": len(values),
                "returned_count": len(projected),
                "occurrences": projected,
                "has_more": next_cursor is not None,
                "cursor": next_cursor,
                "ordering": "activity_time_desc_with_stable_tie_break",
                "authorization_scope": "all_currently_authorized_investigations",
            },
            not_loaded=[
                "full Comment or Post detail",
                "historical AuditFinding or Evidence",
            ],
            limitations=[
                "Occurrence previews do not replace read_account_occurrence detail.",
                "kind preserves whether the Account is the comment author or Post author.",
                "Source investigations are metadata and do not alter the query Scope.",
            ],
        )

    def _read_account_occurrence(
        self, session_id: str, args: dict[str, Any]
    ) -> str:
        _require_exact_keys(
            args, {"occurrence_ref"}, required={"occurrence_ref"}
        )
        occurrence_ref = _require_string(args["occurrence_ref"], "occurrence_ref")
        repository = self._load_repository()
        occurrence_record = self.refs.resolve(
            session_id,
            occurrence_ref,
            expected_kind="occurrence",
            corpus_revision=repository.corpus_revision,
        )
        if not any(
            origin.content_state == "preview"
            and origin.source_tool
            in {"list_account_occurrences", "list_post_risk_comments"}
            for origin in occurrence_record.origins
        ) or occurrence_record.source_tool not in {
            "list_account_occurrences",
            "list_post_risk_comments",
        } or occurrence_record.content_state != "preview":
            raise ReferenceError(
                "wrong_ref_source",
                "The Account occurrence was not displayed by an authorized directory.",
            )
        if occurrence_record.parent_account_id is None:
            raise ReferenceError(
                "wrong_parent_ref", "The Account occurrence has no verified Account parent."
            )
        occurrence = repository.occurrence(
            occurrence_record.parent_account_id, occurrence_record.object_id
        )
        parent_post_ref = None
        if occurrence["kind"] == "comment_author":
            parent_post_ref = self.refs.expose(
                session_id,
                kind="post",
                object_id=occurrence_record.object_id,
                parent_account_id=occurrence_record.parent_account_id,
                corpus_revision=repository.corpus_revision,
                source_tool="read_account_occurrence",
                content_state="parent_post_reference",
            )
        detail = repository.occurrence_detail(
            occurrence,
            occurrence_ref=occurrence_ref,
            report_risk_post_keys=self._report_risk_post_keys(),
            report_post=self._report_post(occurrence),
            parent_post_ref=parent_post_ref,
        )
        return self._success(
            tool="read_account_occurrence",
            result_kind="account_activity_occurrence_detail",
            content_state="complete_stored_activity_record",
            data={"occurrence": detail},
            not_loaded=["historical AuditFinding or Evidence", "binary media"],
            limitations=[
                "Account Activity can confirm the stored occurrence and provenance chain only.",
                "It cannot explain a historical risk decision without a separate authorized Finding/Evidence capability.",
            ],
        )

    def _read_account_post(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"post_ref"}, required={"post_ref"})
        post_ref = _require_string(args["post_ref"], "post_ref")
        repository = self._load_repository()
        post_record = self.refs.resolve(
            session_id,
            post_ref,
            expected_kind="post",
            corpus_revision=repository.corpus_revision,
            expected_source_tool="read_account_occurrence",
            expected_content_state="parent_post_reference",
        )
        if post_record.parent_account_id is None:
            raise ReferenceError(
                "wrong_parent_ref", "The Parent Post has no verified Account parent."
            )
        occurrence = repository.occurrence(
            post_record.parent_account_id, post_record.object_id
        )
        detail = repository.account_post_detail(
            occurrence,
            report_repositories=self.authorized_report_repositories,
        )
        if detail is None:
            return self._success(
                tool="read_account_post",
                result_kind="account_activity_parent_post_detail",
                content_state="frozen_post_payload_unavailable",
                data={
                    "available": False,
                    "reason": (
                        "The occurrence is verified, but no authorized frozen Report Post "
                        "payload is available for its source investigation."
                    ),
                },
                not_loaded=[
                    "Parent Post body",
                    "historical AuditFinding or Evidence",
                ],
                limitations=[
                    "Account Activity does not fall back to titles or display names when resolving Posts."
                ],
            )
        return self._success(
            tool="read_account_post",
            result_kind="account_activity_parent_post_detail",
            content_state="complete_frozen_post_payload",
            data={"available": True, "post": detail},
            not_loaded=["historical AuditFinding or Evidence", "binary media"],
            limitations=[
                "This is the original frozen Post content and provenance, not a risk explanation.",
                "Account Activity cannot infer relationships or coordination from activity records.",
            ],
        )

    def _load_repository(self) -> AccountActivityRepository:
        self.repository_load_count += 1
        return self._repository_loader()

    def _report_risk_post_keys(self) -> frozenset[str]:
        return frozenset(
            item.id.split(":", 1)[-1]
            for item in self.report_repository.risk_posts()
        )

    def _report_post(self, occurrence: dict[str, Any]) -> Post | None:
        report_task_id = self.report_repository.fixture.provenance.source_task_id
        if str(occurrence["task_id"]) != report_task_id:
            return None
        post_id = f'post:{occurrence["post"]["content_key"]}'
        try:
            return self.report_repository.post(post_id)
        except RepositoryLookupError:
            return None

    def _success(self, **kwargs: Any) -> str:
        return success_result(
            scope={
                "report_name": self.report_repository.report.title,
                "account_activity_scope": "all_currently_authorized_investigations",
                "source_task_is_metadata_only": True,
            },
            authority_basis="current_authorized_account_activity_corpus",
            **kwargs,
        )
