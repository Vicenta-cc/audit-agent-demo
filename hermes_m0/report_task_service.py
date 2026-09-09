"""Report-centered navigation and deterministic risk-post discovery."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
from typing import Any, Callable

from hermes_m0.account_activity_service import AccountActivityToolService
from hermes_m0.account_activity_repository import AccountActivityLookupError
from hermes_m0.domain import Evidence, Finding, Post, ReportCase
from hermes_m0.display_labels import (
    DECISION_LABELS,
    EVIDENCE_TYPE_LABELS,
    FINDING_TYPE_LABELS,
    PLATFORM_LABELS,
    RISK_LEVEL_LABELS,
    enum_label,
)
from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.refs import ReferenceError
from hermes_m0.report_task_refs import (
    ReportTaskReferenceRecord,
    ReportTaskReferenceRegistry,
    ReportTaskSessionScope,
)
from hermes_m0.repository import (
    InvestigationRepository,
    RepositoryLookupError,
    is_deterministic_risk_finding,
)
from hermes_m0.real_report_repository import PublishedReportRepository
from hermes_m0.service import (
    EVIDENCE_TYPES,
    MAX_EVIDENCE_PER_LIST,
    MAX_EVIDENCE_PER_READ,
    MAX_POSTS_PER_READ,
    ToolInputError,
    _evidence_name,
    _natural_source,
    _preview,
    _require_exact_keys,
    _require_int,
    _require_string,
    _require_string_list,
)
from hermes_m0.task_repository import normalize_search_text
from hermes_m0.task_service import (
    MAX_SEARCH_CANDIDATES_PER_TURN,
    MAX_SEARCH_CONTINUATIONS_PER_TURN,
    MAX_SEARCH_PAGE,
)
from hermes_m0.tool_results import error_result, success_result


RISK_DECISIONS = frozenset({"review", "reject"})
RISK_LEVELS = frozenset({"low", "medium", "high"})
MAX_CATEGORY_POSTS = 20
MAX_FINDING_POSTS = 20
MAX_RISK_COMMENTS_PER_PAGE = 50


@dataclass
class SearchTurnUsage:
    search_calls: int = 0
    continuation_calls: int = 0
    candidates_returned: int = 0


class ReportTaskInvestigationToolService:
    """One service bound to an authorized task, report, and frozen snapshot."""

    def __init__(
        self,
        repository: InvestigationRepository,
        *,
        refs: ReportTaskReferenceRegistry | None = None,
        ledger: ToolExecutionLedger | None = None,
        account_activity: AccountActivityToolService | None = None,
    ) -> None:
        self.repository = repository
        self.real_report_mode = isinstance(repository, PublishedReportRepository)
        self.refs = refs or ReportTaskReferenceRegistry()
        self.ledger = ledger
        self.account_activity = account_activity
        self.restored_reference_sessions: set[str] = set()
        self._reference_state_lock = threading.RLock()
        self._search_lock = threading.RLock()
        self._search_turn_usage: dict[tuple[str, int, str], SearchTurnUsage] = {}
        self._handlers: dict[str, Callable[..., str]] = {
            "read_report": self._read_report,
            "search_posts": self._search_posts,
            "read_posts": self._read_posts,
            "list_evidence": self._list_evidence,
            "read_evidence": self._read_evidence,
        }
        if self.real_report_mode:
            self._handlers["list_finding_posts"] = self._list_finding_posts
            self._handlers["list_post_risk_comments"] = (
                self._list_post_risk_comments
            )
            if self.account_activity is not None:
                for tool_name in (
                    "get_account_overview",
                    "list_account_occurrences",
                    "read_account_occurrence",
                    "read_account_post",
                ):
                    self._handlers[tool_name] = self._dispatch_account_activity
        else:
            self._handlers["list_category_posts"] = self._list_category_posts

    def bind_session(
        self, session_id: str, *, force_new_generation: bool = False
    ) -> ReportTaskSessionScope:
        fixture = self.repository.fixture
        report = fixture.report_version
        snapshot = fixture.snapshot
        task_id = fixture.provenance.source_task_id
        if not task_id or fixture.provenance.source_scope_ref != f"task:{task_id}":
            raise RuntimeError("Report fixture has no verifiable source task binding")
        if report.snapshot_id != snapshot.id:
            raise RuntimeError("ReportVersion/FrozenSnapshot mismatch")
        scope = self.refs.bind(
            session_id=session_id,
            task_id=task_id,
            report_version_id=report.id,
            snapshot_id=snapshot.id,
            revision=report.revision,
            snapshot_hash=str(
                getattr(
                    self.repository,
                    "snapshot_hash",
                    fixture.provenance.projection_manifest_hash,
                )
            ),
            content_hash=str(
                getattr(self.repository, "content_hash", report.revision)
            ),
            force_new_generation=force_new_generation,
        )
        with self._search_lock:
            self._search_turn_usage = {
                key: value
                for key, value in self._search_turn_usage.items()
                if key[0] != session_id
            }
        if self.account_activity is not None:
            self.account_activity.bind_session(
                session_id,
                task_id=task_id,
                report_version_id=report.id,
                snapshot_id=snapshot.id,
                report_revision=report.revision,
                snapshot_hash=scope.snapshot_hash,
                content_hash=scope.content_hash,
                force_new_generation=force_new_generation,
            )
        if self.ledger is not None and not force_new_generation:
            from .reference_state import restore
            if restore(self, session_id):
                self.restored_reference_sessions.add(session_id)
                scope = self.refs.scope(session_id)
        elif self.ledger is not None:
            from .reference_state import save
            save(self, session_id)
        return scope

    def has_session(self, session_id: str) -> bool:
        return self.refs.has_scope(session_id)

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
        def execute_and_checkpoint(arguments):
            result = next_call(arguments)
            from .reference_state import save
            save(self, session_id)
            return result

        return self.ledger.execute(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=args,
            next_call=execute_and_checkpoint,
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
                code="tool_unavailable_for_report_task_scope",
                message=(
                    "This scope supports report navigation, risk-post discovery, "
                    "post detail, and Evidence detail tools only."
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
            if tool_name in {
                "get_account_overview",
                "list_account_occurrences",
                "read_account_occurrence",
                "read_account_post",
            }:
                return handler(session_id, args, tool_name=tool_name)
            return handler(session_id, args)
        except (ReferenceError, ToolInputError, AccountActivityLookupError) as exc:
            return error_result(tool=tool_name, code=exc.code, message=exc.message)
        except RepositoryLookupError:
            return error_result(
                tool=tool_name,
                code="scope_object_missing",
                message="The referenced object is outside the bound report snapshot.",
            )

    def search_turn_usage(
        self, session_id: str, turn_id: str
    ) -> SearchTurnUsage | None:
        scope = self.refs.scope(session_id)
        with self._search_lock:
            usage = self._search_turn_usage.get(
                (session_id, scope.generation, turn_id)
            )
            if usage is None:
                return None
            return SearchTurnUsage(**usage.__dict__)

    def validate_comment_reference(
        self,
        session_id: str,
        *,
        comment_ref: str,
        parent_post_ref: str,
    ) -> None:
        """Verify a displayed Comment against its exact frozen Parent Post."""

        comment_record = self.refs.resolve(
            session_id, comment_ref, expected_kind="comment"
        )
        post_record = self.refs.resolve(
            session_id, parent_post_ref, expected_kind="post"
        )
        post = self.repository.post(post_record.object_id)
        self._validate_post_record(post_record, post)
        if not any(
            origin.source_tool == "list_post_risk_comments"
            and origin.result_kind == "post_risk_comment_directory"
            and origin.content_state == "complete_text_record"
            and origin.parent_id == post.id
            for origin in comment_record.origins
        ):
            raise ReferenceError(
                "wrong_parent_ref",
                "The Comment reference does not belong to this Parent Post.",
            )
        if comment_record.object_id not in {
            item.id for item in self._real_repository().risk_comments_for_post(post.id)
        }:
            raise ReferenceError(
                "cross_scope_ref", "The Comment is outside the frozen Parent Post."
            )

    def _read_report(self, session_id: str, args: dict[str, Any]) -> str:
        if self.real_report_mode:
            return self._read_real_report(session_id, args)
        _require_exact_keys(args, set())
        report = self.repository.report
        batch = self.refs.batch(session_id)
        categories = []
        for category in self.repository.ordered_cases():
            category_ref = batch.expose(
                kind="category",
                object_id=category.id,
                parent_id=report.id,
                source_tool="read_report",
                result_kind="report_overview",
                content_state="preview",
            )
            categories.append(
                {
                    "position": category.position,
                    "type": "report_category",
                    "name": category.title,
                    "ref": category_ref,
                    "summary": category.summary,
                    "report_displayed_post_count": len(category.member_post_ids),
                    "membership_scope": "report_displayed_posts",
                    "membership_complete": False,
                }
            )
        result = self._success(
            tool="read_report",
            result_kind="report_overview",
            content_state="overview_with_category_previews",
            data={
                "report": {
                    "type": "investigation_report",
                    "name": report.title,
                    "overview": report.overview,
                    "published_at": report.published_at.isoformat(),
                    "statistics": report.statistics.model_dump(mode="json"),
                },
                "category_previews": categories,
                "category_preview_count": len(categories),
                "category_previews_complete_for_report": True,
            },
            not_loaded=[
                "category displayed post directories",
                "post detail and effective Finding detail",
                "Evidence directories and full Evidence content",
            ],
            limitations=[
                "Categories and statistics describe only the bound published report.",
                "A category's displayed posts are representative report members, not a complete category corpus.",
            ],
        )
        batch.commit()
        return result

    def _read_real_report(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, set())
        repository = self._real_repository()
        report = repository.report
        batch = self.refs.batch(session_id)
        finding_previews = []
        for finding in repository.ordered_investigation_findings():
            finding_ref = batch.expose(
                kind="finding",
                object_id=finding.id,
                parent_id=report.id,
                source_tool="read_report",
                result_kind="investigation_finding_previews",
                content_state="preview",
            )
            finding_previews.append(
                {
                    "position": finding.position,
                    "type": "investigation_finding",
                    "type_label": enum_label(
                        FINDING_TYPE_LABELS, "investigation_finding"
                    ),
                    "name": finding.title,
                    "ref": finding_ref,
                    "report_statement": finding.statement,
                    "statement_source": "published_report",
                    "statement_is_raw_evidence": False,
                    "post_membership_count": len(finding.memberships),
                    "representative_post_count": sum(
                        item.is_representative for item in finding.memberships
                    ),
                    "preview_only": True,
                }
            )
        standalone_previews = []
        for position, item in enumerate(repository.standalone_risk_posts(), 1):
            post = repository.post(item.post_id)
            finding = repository.finding_for_post(item.post_id)
            post_ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=report.id,
                source_tool="read_report",
                result_kind="standalone_risk_post_previews",
                content_state="preview",
            )
            standalone_previews.append(
                {
                    "position": position,
                    "type": "standalone_risk_post",
                    "type_label": enum_label(
                        FINDING_TYPE_LABELS, "standalone_risk_post"
                    ),
                    "name": post.title,
                    "ref": post_ref,
                    "author_display_name": post.author.display_name,
                    "report_disposition": item.disposition_note,
                    "audit_finding_preview": {
                        "type_label": enum_label(
                            FINDING_TYPE_LABELS, "audit_finding"
                        ),
                        "decision": finding.decision,
                        "decision_label": enum_label(
                            DECISION_LABELS, finding.decision
                        ),
                        "risk_level": finding.risk_level,
                        "risk_level_label": enum_label(
                            RISK_LEVEL_LABELS, finding.risk_level
                        ),
                        "categories": list(finding.categories),
                        "recorded_summary": _preview(finding.summary, 160),
                    },
                    "not_in_common_investigation_finding": True,
                    "preview_only": True,
                }
            )
        account_entries = (
            []
            if self.account_activity is None
            else self.account_activity.report_entries(session_id)
        )
        data = {
            "report": {
                "type": "investigation_report",
                "name": report.title,
                "report_overview": repository.report_overview,
                "overview_source": "published_report",
                "published_at": report.published_at.isoformat(),
                "deterministic_statistics": report.statistics.model_dump(mode="json"),
            },
            "investigation_finding_previews": finding_previews,
            "investigation_finding_preview_count": len(finding_previews),
            "standalone_risk_post_previews": standalone_previews,
            "standalone_risk_post_preview_count": len(standalone_previews),
            "risk_post_accounting": {
                "unique_finding_member_posts": len(
                    {
                        membership.post_id
                        for finding in repository.ordered_investigation_findings()
                        for membership in finding.memberships
                    }
                ),
                "standalone_risk_posts": len(standalone_previews),
                "total_unique_risk_posts": len(repository.risk_posts()),
                "complete": True,
            },
        }
        not_loaded = [
            "InvestigationFinding Post membership directories",
            "Post content and effective AuditFinding detail",
            "full Post Evidence directories",
            "full Evidence content",
        ]
        limitations = [
            "InvestigationFinding statements are published report synthesis, not new raw Evidence.",
            "Standalone risk items were reviewed but were not assigned to a common InvestigationFinding.",
            "All returned items are previews; use the appropriate detail tool before presenting detail.",
        ]
        if self.account_activity is not None:
            data["account_activity_entries"] = account_entries
            data["account_activity_entry_count"] = len(account_entries)
            not_loaded.append("cross-investigation Account Overview and occurrence detail")
            limitations.append(
                "Account cards contain current-investigation counts only; Account Overview uses all currently authorized investigations."
            )
        result = self._success(
            tool="read_report",
            result_kind="published_report_overview",
            content_state="overview_with_finding_and_standalone_previews",
            data=data,
            not_loaded=not_loaded,
            limitations=limitations,
        )
        batch.commit()
        return result

    def _dispatch_account_activity(
        self, session_id: str, args: dict[str, Any], *, tool_name: str
    ) -> str:
        if self.account_activity is None:
            raise ToolInputError(
                "account_activity_unavailable",
                "Account Activity is not configured for this report session.",
            )
        return self.account_activity.dispatch(tool_name, args, session_id=session_id)

    def _list_finding_posts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"finding_ref", "limit"}, required={"finding_ref"})
        finding_ref = _require_string(args["finding_ref"], "finding_ref")
        limit = _require_int(
            args.get("limit", MAX_FINDING_POSTS),
            "limit",
            1,
            MAX_FINDING_POSTS,
        )
        repository = self._real_repository()
        record = self.refs.resolve(
            session_id, finding_ref, expected_kind="finding"
        )
        finding = repository.investigation_finding(record.object_id)
        self._validate_finding_record(record, finding.id)
        returned = finding.memberships[:limit]
        batch = self.refs.batch(session_id)
        cards = []
        for position, membership in enumerate(returned, 1):
            post = repository.post(membership.post_id)
            audit_finding = repository.finding_detail_for_post(post.id)
            post_ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=finding.id,
                source_tool="list_finding_posts",
                result_kind="investigation_finding_post_memberships",
                content_state="membership_preview",
            )
            evidence_cards = []
            for subset_position, item in enumerate(
                repository.membership_evidence(finding.id, post.id), 1
            ):
                evidence_ref = batch.expose(
                    kind="evidence",
                    object_id=item.id,
                    parent_id=finding.id,
                    source_tool="list_finding_posts",
                    result_kind="membership_evidence_subset",
                    content_state="preview",
                )
                visible_text = item.content.translated_text or item.content.original_text
                evidence_cards.append(
                    {
                        "subset_position": subset_position,
                        "post_evidence_position": item.ordinal,
                        "type": "evidence",
                        "evidence_type": item.type,
                        "evidence_type_label": enum_label(
                            EVIDENCE_TYPE_LABELS, item.type
                        ),
                        "name": _evidence_name(item),
                        "ref": evidence_ref,
                        "recorded_summary": _preview(item.content.summary, 160),
                        "text_preview": _preview(visible_text, 120),
                        "preview_only": True,
                    }
                )
            cards.append(
                {
                    "position": position,
                    "type": "finding_post_membership",
                    "post": {
                        "type": "post",
                        "name": post.title,
                        "ref": post_ref,
                        "author_display_name": post.author.display_name,
                        "platform": post.source.platform,
                        "platform_label": enum_label(
                            PLATFORM_LABELS, post.source.platform
                        ),
                    },
                    "is_representative": membership.is_representative,
                    "membership_audit_finding": {
                        "type_label": enum_label(
                            FINDING_TYPE_LABELS, "audit_finding"
                        ),
                        "decision": audit_finding["decision"],
                        "decision_label": enum_label(
                            DECISION_LABELS, audit_finding["decision"]
                        ),
                        "risk_level": audit_finding["risk_level"],
                        "risk_level_label": enum_label(
                            RISK_LEVEL_LABELS, audit_finding["risk_level"]
                        ),
                        "categories": audit_finding["categories"],
                        "recorded_summary": audit_finding["summary"],
                        "risk_basis": audit_finding["risk_basis"],
                        "scope": "post_effective_audit_finding_context",
                        "is_membership_evidence_subset": False,
                        "scope_label": "整帖审核背景",
                    },
                    "membership_classification_basis": {
                        "source": "membership_evidence_subset",
                        "recorded_evidence_summaries": [
                            item["recorded_summary"] for item in evidence_cards
                        ],
                        "must_exclude_other_post_evidence": True,
                    },
                    "membership_evidence_subset": evidence_cards,
                    "membership_evidence_count": len(evidence_cards),
                    "post_total_evidence_count": repository.post_total_evidence_count(
                        post.id
                    ),
                    "evidence_scope": "finding_membership_subset",
                    "post_content_loaded": False,
                    "membership_evidence_subset_complete": True,
                    "membership_evidence_is_all_post_evidence": False,
                    "preview_only": True,
                }
            )
        result = self._success(
            tool="list_finding_posts",
            result_kind="investigation_finding_post_memberships",
            content_state="membership_and_bounded_evidence_previews",
            data={
                "investigation_finding": {
                    "type": "investigation_finding",
                    "type_label": enum_label(
                        FINDING_TYPE_LABELS, "investigation_finding"
                    ),
                    "name": finding.title,
                    "ref": finding_ref,
                },
                "post_memberships": cards,
                "total_post_membership_count": len(finding.memberships),
                "returned_count": len(cards),
                "truncated_by_limit": len(cards) < len(finding.memberships),
                "post_memberships_complete": len(cards) == len(finding.memberships),
                "membership_scope": "all_report_post_memberships_for_this_finding",
                "evidence_scope": "finding_membership_subset",
            },
            not_loaded=[
                "Post body and full speech transcripts",
                "full Evidence original text and translation",
                "Evidence for each Post outside this membership subset",
            ],
            limitations=[
                "These are all returned post_memberships, not ReportCategory displayed members.",
                "is_representative marks the Post displayed in report prose; it does not change membership.",
                "Each Evidence preview explains only why that Post belongs to this InvestigationFinding.",
                "Use list_evidence from the Post when the user asks what other Evidence that Post has.",
            ],
        )
        batch.commit()
        return result

    def _list_category_posts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(
            args, {"category_ref", "limit"}, required={"category_ref"}
        )
        category_ref = _require_string(args["category_ref"], "category_ref")
        limit = _require_int(
            args.get("limit", MAX_CATEGORY_POSTS),
            "limit",
            1,
            MAX_CATEGORY_POSTS,
        )
        record = self.refs.resolve(
            session_id, category_ref, expected_kind="category"
        )
        category = self.repository.case(record.object_id)
        self._validate_category_record(record, category)
        displayed = self.repository.case_posts(category.id)
        returned = displayed[:limit]
        batch = self.refs.batch(session_id)
        cards = []
        for position, post in enumerate(returned, 1):
            post_ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=category.id,
                source_tool="list_category_posts",
                result_kind="report_category_post_directory",
                content_state="preview",
            )
            cards.append(
                {
                    "position": position,
                    "type": "post",
                    "name": post.title,
                    "ref": post_ref,
                    "author_display_name": post.author.display_name,
                    "platform": post.source.platform,
                    "parent": {
                        "type": "report_category",
                        "name": category.title,
                        "ref": category_ref,
                    },
                    "preview_only": True,
                }
            )
        result = self._success(
            tool="list_category_posts",
            result_kind="report_category_post_directory",
            content_state="preview_only",
            data={
                "category": {
                    "type": "report_category",
                    "name": category.title,
                    "ref": category_ref,
                },
                "posts": cards,
                "returned_count": len(cards),
                "report_displayed_post_count": len(displayed),
                "truncated_by_limit": len(returned) < len(displayed),
                "membership_scope": "report_displayed_posts",
                "membership_complete": False,
                "user_visible_membership_statement": (
                    "当前报告在该类别下列出了以下帖子"
                ),
            },
            not_loaded=[
                "posts in the task that are not displayed in this report category",
                "post body and full text records",
                "effective Finding detail",
                "Evidence directories and full Evidence content",
            ],
            limitations=[
                "Do not describe these displayed posts as the category's complete membership.",
                "Use read_posts before discussing post content or Finding detail.",
            ],
        )
        batch.commit()
        return result

    def _search_posts(
        self,
        session_id: str,
        args: dict[str, Any],
        *,
        turn_id: str,
    ) -> str:
        allowed_keys = {"query_text", "filters", "limit", "cursor", "context_ref"}
        required_keys = {"query_text", "limit"}
        if self.real_report_mode:
            allowed_keys.add("requested_count")
            required_keys = {"query_text"}
        _require_exact_keys(args, allowed_keys, required=required_keys)
        if not turn_id:
            raise ToolInputError(
                "missing_turn_identity",
                "Hermes task_id is required to enforce per-turn discovery limits.",
            )
        query_text = _require_string(args["query_text"], "query_text")
        if len(query_text) > 200:
            raise ToolInputError(
                "invalid_arguments", "query_text must contain at most 200 characters"
            )
        requested_count = None
        if self.real_report_mode:
            has_requested_count = "requested_count" in args
            has_legacy_limit = "limit" in args
            if has_requested_count == has_legacy_limit:
                raise ToolInputError(
                    "invalid_arguments",
                    "Provide requested_count exactly once; legacy limit is accepted only internally.",
                )
            if has_requested_count:
                requested_count = _require_int(
                    args["requested_count"], "requested_count", 1, 20
                )
            else:
                requested_count = _require_int(
                    args["limit"], "limit", 1, MAX_SEARCH_PAGE
                )
            page_limit = MAX_SEARCH_CANDIDATES_PER_TURN
        else:
            page_limit = _require_int(args["limit"], "limit", 1, MAX_SEARCH_PAGE)
        filters = args.get("filters", {})
        if not isinstance(filters, dict):
            raise ToolInputError("invalid_arguments", "filters must be an object")
        _require_exact_keys(filters, {"risk_level", "decision"})
        risk_level = filters.get("risk_level")
        if risk_level is not None:
            risk_level = _require_string(risk_level, "risk_level")
            if risk_level not in RISK_LEVELS:
                raise ToolInputError(
                    "invalid_arguments", "Only low, medium, or high risk is discoverable."
                )
        decision = filters.get("decision")
        if decision is not None:
            decision = _require_string(decision, "decision")
            if decision not in RISK_DECISIONS:
                raise ToolInputError(
                    "invalid_arguments", "Only review or reject is discoverable."
                )
        context_ref = args.get("context_ref")
        context_record = None
        if context_ref is not None:
            context_ref = _require_string(context_ref, "context_ref")
            context_record = self.refs.resolve(session_id, context_ref)
            allowed_context_kinds = (
                {"finding", "post"}
                if self.real_report_mode
                else {"category", "post"}
            )
            if context_record.kind not in allowed_context_kinds:
                raise ReferenceError(
                    "wrong_ref_type",
                    "Discovery context must be a verified report navigation object or Post.",
                )
        cursor = args.get("cursor")
        if cursor is not None:
            cursor = _require_string(cursor, "cursor")
            if len(cursor) > 128:
                raise ToolInputError(
                    "invalid_arguments", "cursor must contain at most 128 characters"
                )

        candidates = tuple(
            post
            for post in self.repository.risk_posts()
            if (
                (
                    risk_level is None
                    or self.repository.finding_for_post(post.id).risk_level
                    == risk_level
                )
                and (
                    decision is None
                    or self.repository.finding_for_post(post.id).decision == decision
                )
            )
        )
        ordered_post_ids_hash = _ordered_post_ids_hash(candidates)
        discovery_fingerprint = _discovery_fingerprint(
            query_text,
            risk_level=risk_level,
            decision=decision,
            context_record=context_record,
            requested_count=requested_count,
        )
        offset = 0
        if cursor is not None:
            cursor_record = self.refs.resolve_search_cursor(
                session_id,
                cursor,
                discovery_fingerprint=discovery_fingerprint,
                ordered_post_ids_hash=ordered_post_ids_hash,
            )
            offset = cursor_record.next_offset
        if offset < 0 or offset > len(candidates):
            raise ReferenceError(
                "cursor_order_mismatch",
                "The cursor offset is outside the frozen risk candidate order.",
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
                    "This turn already used the maximum of three continuations.",
                )
            remaining = MAX_SEARCH_CANDIDATES_PER_TURN - usage.candidates_returned
            if remaining <= 0:
                raise ToolInputError(
                    "search_turn_limit_reached",
                    "This turn already returned twenty risk candidates.",
                )
            page_size = min(page_limit, remaining)
            returned = candidates[offset : offset + page_size]
            usage.search_calls += 1
            if cursor is not None:
                usage.continuation_calls += 1
            usage.candidates_returned += len(returned)
            usage_snapshot = SearchTurnUsage(**usage.__dict__)

        batch = self.refs.batch(session_id)
        cards = []
        for position, post in enumerate(returned, offset + 1):
            finding = self.repository.finding_for_post(post.id)
            post_ref = batch.expose(
                kind="post",
                object_id=post.id,
                parent_id=self.repository.snapshot.id,
                source_tool="search_posts",
                result_kind="risk_post_candidate_directory",
                content_state="risk_preview",
            )
            cards.append(self._risk_preview(post, finding, post_ref, position))

        next_offset = offset + len(returned)
        has_more = next_offset < len(candidates)
        next_cursor = None
        if has_more:
            next_cursor = self.refs.issue_search_cursor(
                session_id,
                discovery_fingerprint=discovery_fingerprint,
                ordered_post_ids_hash=ordered_post_ids_hash,
                next_offset=next_offset,
            )
        can_continue = (
            has_more
            and usage_snapshot.continuation_calls < MAX_SEARCH_CONTINUATIONS_PER_TURN
            and usage_snapshot.candidates_returned < MAX_SEARCH_CANDIDATES_PER_TURN
        )
        if not has_more:
            stop_reason = "no_more_risk_candidates"
        elif usage_snapshot.candidates_returned >= MAX_SEARCH_CANDIDATES_PER_TURN:
            stop_reason = "candidate_limit_reached"
        elif usage_snapshot.continuation_calls >= MAX_SEARCH_CONTINUATIONS_PER_TURN:
            stop_reason = "continuation_limit_reached"
        else:
            stop_reason = None

        common_data = {
            "discovery_goal": query_text,
            "discovery_context": self._discovery_context(
                context_ref, context_record
            ),
            "risk_predicate": {
                "decision": ["review", "reject"],
                "decision_labels": [
                    enum_label(DECISION_LABELS, item)
                    for item in ("review", "reject")
                ],
                "risk_level": ["low", "medium", "high"],
                "risk_level_labels": [
                    enum_label(RISK_LEVEL_LABELS, item)
                    for item in ("low", "medium", "high")
                ],
                "operator": "decision AND risk_level",
                "source": "frozen effective Finding fields",
            },
            "applied_filters": {
                "risk_level": risk_level,
                "risk_level_label": enum_label(RISK_LEVEL_LABELS, risk_level),
                "decision": decision,
                "decision_label": enum_label(DECISION_LABELS, decision),
            },
            "ordering": "当前调查任务冻结帖子顺序",
            "server_semantic_matching_performed": False,
            "has_more": has_more,
        }
        if self.real_report_mode:
            result_data = {
                **common_data,
                "requested_count": requested_count,
                "candidate_pool_count": len(cards),
                "total_risk_candidate_count": len(candidates),
                "candidates": cards,
                "candidates_are_semantic_matches": False,
                "qwen_selection_responsibility": (
                    "Select only genuinely relevant candidates from the previews, up to "
                    "requested_count; return fewer when support is insufficient."
                ),
                "insufficient_relevant_candidates_policy": (
                    "Return the actual supported count; never fill with unrelated candidates."
                ),
                "result_position_start": offset + 1 if cards else None,
                "result_position_end": next_offset if cards else None,
                "cursor": next_cursor,
                "cursor_scope": (
                    "Undisplayed deterministic risk candidates only; not semantic matches."
                ),
                "continuation": {
                    "can_continue_this_turn": can_continue,
                    "stop_reason": stop_reason,
                    "search_calls_this_turn": usage_snapshot.search_calls,
                    "continuation_calls_this_turn": usage_snapshot.continuation_calls,
                    "max_continuation_calls_per_turn": MAX_SEARCH_CONTINUATIONS_PER_TURN,
                    "candidates_returned_this_turn": usage_snapshot.candidates_returned,
                    "max_candidates_per_turn": MAX_SEARCH_CANDIDATES_PER_TURN,
                },
            }
        else:
            result_data = {
                **common_data,
                "posts": cards,
                "risk_candidate_count": len(candidates),
                "returned_count": len(cards),
                "result_position_start": offset + 1 if cards else None,
                "result_position_end": next_offset if cards else None,
                "qwen_selection_responsibility": (
                    "Judge topic relevance, similarity, and typicality from these risk previews."
                ),
                "next_cursor": next_cursor,
                "continuation": {
                    "can_continue_this_turn": can_continue,
                    "stop_reason": stop_reason,
                    "search_calls_this_turn": usage_snapshot.search_calls,
                    "continuation_calls_this_turn": usage_snapshot.continuation_calls,
                    "max_continuation_calls_per_turn": MAX_SEARCH_CONTINUATIONS_PER_TURN,
                    "candidates_returned_this_turn": usage_snapshot.candidates_returned,
                    "max_candidates_per_turn": MAX_SEARCH_CANDIDATES_PER_TURN,
                },
            }

        result = self._success(
            tool="search_posts",
            result_kind="risk_post_candidate_directory",
            content_state="risk_preview_only",
            data=result_data,
            not_loaded=[
                "full post body",
                "full ASR, OCR, or other complete text records",
                "complete Finding detail beyond the displayed preview fields",
                "Evidence directories and full Evidence content",
                "deterministically safe pass/none posts",
            ],
            limitations=[
                "The server did not semantically rank or label the discovery goal.",
                "Only deterministic risk posts are candidates; use read_posts for selected details.",
            ],
        )
        batch.commit()
        return result

    def _read_posts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"post_refs"}, required={"post_refs"})
        post_refs = _require_string_list(
            args["post_refs"],
            "post_refs",
            minimum=1,
            maximum=MAX_POSTS_PER_READ,
        )
        groups = []
        for position, post_ref in enumerate(post_refs, 1):
            record = self.refs.resolve(
                session_id, post_ref, expected_kind="post"
            )
            post = self.repository.post(record.object_id)
            self._validate_post_record(record, post)
            finding = self.repository.finding_for_post(post.id)
            if self.real_report_mode:
                post_content = self._real_repository().post_content(post.id)
                effective_finding = self._real_repository().finding_detail_for_post(
                    post.id
                )
                author = {
                    "display_name": post.author.display_name,
                    "platform": post.author.platform,
                }
                if self.account_activity is not None:
                    author_account_ref = self.account_activity.expose_post_author(
                        session_id,
                        task_id=self.repository.fixture.provenance.source_task_id,
                        content_key=post.id.split(":", 1)[-1],
                    )
                    if author_account_ref is not None:
                        author["account_ref"] = author_account_ref
                natural_source = {
                    "platform": post.source.platform,
                    "published_at": post.source.published_at,
                    "published_at_availability": (
                        "available" if post.source.published_at else "unavailable"
                    ),
                    "captured_at": (
                        post.source.captured_at.isoformat()
                        if post.source.captured_at is not None
                        else ""
                    ),
                    "captured_at_availability": (
                        "available"
                        if post.source.captured_at is not None
                        else "unavailable"
                    ),
                    "analyzed_at": (
                        post.source.analyzed_at.isoformat()
                        if post.source.analyzed_at is not None
                        else ""
                    ),
                    "analyzed_at_availability": (
                        "available"
                        if post.source.analyzed_at is not None
                        else "unavailable"
                    ),
                }
            else:
                post_content = {"recorded_body": post.body}
                effective_finding = {
                    "type": "finding",
                    "decision": finding.decision,
                    "risk_level": finding.risk_level,
                    "categories": list(finding.categories),
                    "summary": finding.summary,
                    "completed_at": finding.completed_at.isoformat(),
                }
                author = {
                    "display_name": post.author.display_name,
                    "avatar_url": post.author.avatar_url,
                    "platform": post.author.platform,
                }
                natural_source = {
                    "platform": post.source.platform,
                    "url": post.source.url,
                    "published_at": post.source.published_at,
                    "captured_at": (
                        post.source.captured_at.isoformat()
                        if post.source.captured_at is not None
                        else ""
                    ),
                }
            effective_finding = dict(effective_finding)
            effective_finding["type_label"] = enum_label(
                FINDING_TYPE_LABELS, "audit_finding"
            )
            effective_finding["decision_label"] = enum_label(
                DECISION_LABELS, effective_finding.get("decision")
            )
            effective_finding["risk_level_label"] = enum_label(
                RISK_LEVEL_LABELS, effective_finding.get("risk_level")
            )
            author["platform_label"] = enum_label(
                PLATFORM_LABELS, author.get("platform")
            )
            natural_source["platform_label"] = enum_label(
                PLATFORM_LABELS, natural_source.get("platform")
            )
            groups.append(
                {
                    "group_position": position,
                    "post": {
                        "type": "post",
                        "name": post.title,
                        "ref": post_ref,
                    },
                    "post_content": post_content,
                    "author": author,
                    "natural_source": natural_source,
                    "effective_finding": effective_finding,
                }
            )
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

    def _list_post_risk_comments(
        self, session_id: str, args: dict[str, Any]
    ) -> str:
        _require_exact_keys(
            args, {"post_ref", "limit", "cursor"}, required={"post_ref", "limit"}
        )
        post_ref = _require_string(args["post_ref"], "post_ref")
        limit = _require_int(
            args["limit"],
            "limit",
            minimum=1,
            maximum=MAX_RISK_COMMENTS_PER_PAGE,
        )
        cursor = (
            None
            if "cursor" not in args
            else _require_string(args["cursor"], "cursor")
        )
        record = self.refs.resolve(session_id, post_ref, expected_kind="post")
        post = self.repository.post(record.object_id)
        self._validate_post_record(record, post)
        repository = self._real_repository()
        comments = repository.risk_comments_for_post(post.id)
        ordered_hash = hashlib.sha256(
            json.dumps(
                [item.id for item in comments], separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        offset = 0
        if cursor is not None:
            cursor_record = self.refs.resolve_risk_comment_cursor(
                session_id,
                cursor,
                post_id=post.id,
                ordered_comment_ids_hash=ordered_hash,
            )
            offset = cursor_record.next_offset
        selected = comments[offset : offset + limit]
        next_offset = offset + len(selected)
        next_cursor = (
            None
            if next_offset >= len(comments)
            else self.refs.issue_risk_comment_cursor(
                session_id,
                post_id=post.id,
                ordered_comment_ids_hash=ordered_hash,
                next_offset=next_offset,
            )
        )
        task_id = self.repository.fixture.provenance.source_task_id
        content_key = post.id.split(":", 1)[-1]
        bridges: dict[str, dict[str, str | None]] = {}
        if self.account_activity is not None and selected:
            bridges = self.account_activity.expose_risk_comments(
                session_id,
                task_id=task_id,
                content_key=content_key,
                comments=tuple(
                    {
                        "comment_id": item.id,
                        "audit_status": item.audit_status,
                        "risk_level": item.risk_level,
                        "risk_type": item.risk_type,
                        "text": item.text,
                        "published_at": item.published_at,
                    }
                    for item in selected
                ),
            )
        batch = self.refs.batch(session_id)
        cards = []
        for position, comment in enumerate(selected, offset + 1):
            comment_ref = batch.expose(
                kind="comment",
                object_id=comment.id,
                parent_id=post.id,
                source_tool="list_post_risk_comments",
                result_kind="post_risk_comment_directory",
                content_state="complete_text_record",
            )
            bridge = bridges.get(comment.id, {})
            author = {
                "display_name": comment.author_display_name
                or "未记录昵称的账号",
                "stable_identity_available": bool(comment.author_source_key),
            }
            if bridge.get("account_ref") is not None:
                author["account_ref"] = bridge["account_ref"]
            card = {
                "position": position,
                "type": "comment",
                "ref": comment_ref,
                "full_original_text": comment.text,
                "text_complete": True,
                "audit_status": comment.audit_status,
                "risk_level": comment.risk_level,
                "risk_level_label": enum_label(
                    RISK_LEVEL_LABELS, comment.risk_level
                ),
                "risk_type": comment.risk_type,
                "published_at": comment.published_at,
                "published_at_availability": (
                    "available" if comment.published_at else "unavailable"
                ),
                "author": author,
                "parent_post": {
                    "type": "post",
                    "name": post.title,
                    "ref": post_ref,
                    "published_at": post.source.published_at,
                    "published_at_availability": (
                        "available" if post.source.published_at else "unavailable"
                    ),
                },
            }
            if bridge.get("occurrence_ref") is not None:
                card["account_occurrence_ref"] = bridge["occurrence_ref"]
            cards.append(card)
        result = self._success(
            tool="list_post_risk_comments",
            result_kind="post_risk_comment_directory",
            content_state="complete_frozen_comment_records",
            data={
                "parent_post": {
                    "type": "post",
                    "name": post.title,
                    "ref": post_ref,
                },
                "comments": cards,
                "matched_count": len(comments),
                "returned_count": len(cards),
                "has_more": next_cursor is not None,
                "cursor": next_cursor,
                "directory_complete_for_post": next_cursor is None and offset == 0,
                "ordering": "comment_time_asc_with_stable_identity_tie_break",
                "risk_predicate": {
                    "audit_status": "completed",
                    "own_risk_level": ["low", "medium", "high"],
                    "own_risk_level_labels": [
                        enum_label(RISK_LEVEL_LABELS, item)
                        for item in ("low", "medium", "high")
                    ],
                    "inherits_parent_post_risk": False,
                },
            },
            not_loaded=["Report Evidence", "cross-task AuditFinding or Evidence"],
            limitations=[
                "Comment risk fields belong to the Comment itself and never inherit from the Parent Post.",
                "Account Activity confirms public occurrences but does not explain risk Evidence reasons.",
            ],
        )
        batch.commit()
        return result

    def _list_evidence(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(
            args, {"post_ref", "types", "limit"}, required={"post_ref"}
        )
        post_ref = _require_string(args["post_ref"], "post_ref")
        record = self.refs.resolve(session_id, post_ref, expected_kind="post")
        post = self.repository.post(record.object_id)
        self._validate_post_record(record, post)
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
        limit = _require_int(
            args.get("limit", 10), "limit", 1, MAX_EVIDENCE_PER_LIST
        )
        all_items = self.repository.evidence_for_post(post.id)
        selected = [
            item
            for item in all_items
            if not requested_types or item.type in requested_types
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
                    "evidence_type": item.type,
                    "name": _evidence_name(item),
                    "ref": evidence_ref,
                    "parent": {
                        "type": "post",
                        "name": post.title,
                        "ref": post_ref,
                    },
                    "preview": _preview(item.content.original_text),
                    "preview_is_complete": len(item.content.original_text) <= 160,
                    "recorded_summary": _preview(item.content.summary, 160),
                    "preview_only": True,
                }
            )
        if self.real_report_mode:
            limitations = [
                "本工具返回该Post的完整Evidence目录视角，但每项仍只是preview。",
                "完整原文、已有译文和具体审核理由需要调用read_evidence。",
                "此目录不得被缩减为某个InvestigationFinding的membership Evidence子集。",
            ]
        else:
            limitations = [
                "完整原文、已有译文和具体审核理由需要调用read_evidence。",
                "Evidence目录只属于已验证的父Post。",
            ]
        result = self._success(
            tool="list_evidence",
            result_kind="evidence_candidate_directory",
            content_state="preview_only",
            data={
                "parent_post": {
                    "type": "post",
                    "name": post.title,
                    "ref": post_ref,
                },
                "candidates": cards,
                "matched_count": len(selected),
                "post_total_evidence_count": (
                    self._real_repository().post_total_evidence_count(post.id)
                    if self.real_report_mode
                    else len(all_items)
                ),
                "returned_count": len(cards),
                "truncated": len(cards) < len(selected),
                "directory_complete_for_post": (
                    requested_types is None and len(cards) == len(all_items)
                ),
                "evidence_scope": "all_post_evidence",
                "applied_types": sorted(requested_types) if requested_types else [],
            },
            not_loaded=[
                "full Evidence original text",
                "full existing translation",
                "complete recorded audit reason or summary",
            ],
            limitations=limitations,
        )
        batch.commit()
        return result

    def _read_evidence(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {"evidence_refs"}, required={"evidence_refs"})
        evidence_refs = _require_string_list(
            args["evidence_refs"],
            "evidence_refs",
            minimum=1,
            maximum=MAX_EVIDENCE_PER_READ,
        )
        groups = []
        for position, evidence_ref in enumerate(evidence_refs, 1):
            record = self.refs.resolve(
                session_id, evidence_ref, expected_kind="evidence"
            )
            item = self.repository.evidence(record.object_id)
            self._validate_evidence_record(record, item)
            post = self.repository.post(item.parent_post_id)
            post_ref = self.refs.ref_for(session_id, "post", post.id)
            groups.append(
                {
                    "group_position": position,
                    "evidence": {
                        "type": "evidence",
                        "evidence_type": item.type,
                        "evidence_type_label": enum_label(
                            EVIDENCE_TYPE_LABELS, item.type
                        ),
                        "name": _evidence_name(item),
                        "ref": evidence_ref,
                        "full_original_text": item.content.original_text,
                        "existing_translation": item.content.translated_text,
                        "recorded_audit_summary": item.content.summary,
                    },
                    "parent_post": {
                        "type": "post",
                        "name": post.title,
                        "ref": post_ref,
                    },
                    "natural_source": _natural_source(item, post),
                }
            )
            groups[-1]["natural_source"]["platform_label"] = enum_label(
                PLATFORM_LABELS, groups[-1]["natural_source"].get("platform")
            )
            if self.real_report_mode and item.type == "comment":
                groups[-1]["source_comment"] = self._source_comment_projection(
                    session_id, item, post
                )
        return self._success(
            tool="read_evidence",
            result_kind="evidence_full_content",
            content_state="complete_text_record",
            data={"evidence_groups": groups, "returned_count": len(groups)},
            not_loaded=["binary image, frame, audio, or video bytes"],
            limitations=[
                "The complete stored text record is returned; linked binary media is not loaded."
            ],
        )

    def _source_comment_projection(
        self, session_id: str, item: Evidence, post: Post
    ) -> dict[str, Any]:
        repository = self._real_repository()
        comment = repository.comment_author_for_evidence(item.id)
        author: dict[str, Any] = {
            "display_name": None,
            "public_profile_identifier": None,
            "stable_identity_available": False,
            "account_ref": None,
        }
        occurrence_ref = None
        if comment is not None:
            author["display_name"] = comment.author_display_name or None
            if comment.author_public_identifier and comment.platform in {
                "dy",
                "douyin",
            }:
                author["public_profile_identifier"] = {
                    "platform": "douyin",
                    "platform_label": enum_label(PLATFORM_LABELS, "douyin"),
                    "label": "抖音号",
                    "value": comment.author_public_identifier,
                }
            if self.account_activity is not None:
                task_id = repository.fixture.provenance.source_task_id
                content_key = post.id.removeprefix("post:")
                try:
                    bridge = self.account_activity.expose_comment_evidence(
                        session_id,
                        task_id=task_id,
                        content_key=content_key,
                        comment_id=comment.id,
                        text=comment.text,
                        published_at=comment.published_at,
                    )
                except Exception:
                    # Account bridging is optional; a corpus or authorization
                    # failure must not hide the exact frozen Comment identity.
                    bridge = {"account_ref": None, "account_occurrence_ref": None}
                author["account_ref"] = bridge.get("account_ref")
                occurrence_ref = bridge.get("account_occurrence_ref")
            author["stable_identity_available"] = bool(author["account_ref"])
        return {"author": author, "account_occurrence_ref": occurrence_ref}

    def _validate_category_record(
        self, record: ReportTaskReferenceRecord, category: ReportCase
    ) -> None:
        if category.id not in self.repository.report.case_ids:
            raise ReferenceError(
                "cross_scope_ref", "Category is outside the bound report."
            )
        if not any(
            origin.source_tool == "read_report"
            and origin.parent_id == self.repository.report.id
            for origin in record.origins
        ):
            raise ReferenceError(
                "wrong_parent_ref",
                "Category reference was not exposed by the bound report.",
            )

    def _validate_finding_record(
        self, record: ReportTaskReferenceRecord, finding_id: str
    ) -> None:
        repository = self._real_repository()
        repository.investigation_finding(finding_id)
        if not any(
            origin.source_tool == "read_report"
            and origin.parent_id == repository.report.id
            for origin in record.origins
        ):
            raise ReferenceError(
                "wrong_parent_ref",
                "InvestigationFinding reference was not exposed by the bound report.",
            )
    def _validate_post_record(
        self, record: ReportTaskReferenceRecord, post: Post
    ) -> None:
        if (
            post.id not in self.repository.snapshot.post_ids
            or post.revision_id not in self.repository.snapshot.post_revision_ids
        ):
            raise ReferenceError(
                "stale_revision_ref", "Post revision is not frozen in scope."
            )
        valid_origin = False
        for origin in record.origins:
            if origin.source_tool == "list_category_posts":
                category = self.repository.case_for_post(post.id)
                valid_origin = origin.parent_id == category.id
            elif origin.source_tool == "list_finding_posts" and self.real_report_mode:
                try:
                    self._real_repository().finding_membership(
                        str(origin.parent_id), post.id
                    )
                    valid_origin = True
                except RepositoryLookupError:
                    valid_origin = False
            elif origin.source_tool == "read_report" and self.real_report_mode:
                valid_origin = (
                    origin.parent_id == self.repository.report.id
                    and self._real_repository().is_standalone_risk_post(post.id)
                )
            elif origin.source_tool == "search_posts":
                valid_origin = (
                    origin.parent_id == self.repository.snapshot.id
                    and is_deterministic_risk_finding(
                        self.repository.finding_for_post(post.id)
                    )
                )
            if valid_origin:
                break
        if not valid_origin:
            raise ReferenceError(
                "invalid_ref_source",
                "Post reference lacks a valid report membership or risk-discovery origin.",
            )

    def _validate_evidence_record(
        self, record: ReportTaskReferenceRecord, item: Evidence
    ) -> None:
        if item.id not in self.repository.snapshot.evidence_ids:
            raise ReferenceError(
                "cross_scope_ref", "Evidence is outside the bound snapshot."
            )
        post = self.repository.post(item.parent_post_id)
        if item.parent_revision_id != post.revision_id:
            raise ReferenceError(
                "stale_revision_ref", "Evidence belongs to another post revision."
            )
        valid_origin = any(
            origin.source_tool == "list_evidence"
            and origin.parent_id == item.parent_post_id
            for origin in record.origins
        )
        if self.real_report_mode and not valid_origin:
            repository = self._real_repository()
            for origin in record.origins:
                if origin.source_tool != "list_finding_posts":
                    continue
                try:
                    membership = repository.finding_membership(
                        str(origin.parent_id), item.parent_post_id
                    )
                except RepositoryLookupError:
                    continue
                if item.id in membership.evidence_ids:
                    valid_origin = True
                    break
        if not valid_origin:
            raise ReferenceError(
                "wrong_parent_ref", "Evidence reference parent does not match the post."
            )

    def _discovery_context(
        self,
        context_ref: str | None,
        record: ReportTaskReferenceRecord | None,
    ) -> dict[str, Any] | None:
        if context_ref is None or record is None:
            return None
        if record.kind == "finding":
            repository = self._real_repository()
            finding = repository.investigation_finding(record.object_id)
            self._validate_finding_record(record, finding.id)
            return {
                "type": "investigation_finding",
                "type_label": enum_label(
                    FINDING_TYPE_LABELS, "investigation_finding"
                ),
                "name": finding.title,
                "ref": context_ref,
                "report_statement": finding.statement,
                "statement_source": "published_report",
                "statement_is_raw_evidence": False,
            }
        if record.kind == "category":
            category = self.repository.case(record.object_id)
            self._validate_category_record(record, category)
            return {
                "type": "report_category",
                "name": category.title,
                "ref": context_ref,
                "summary": category.summary,
                "membership_scope": "report_displayed_posts",
                "membership_complete": False,
            }
        post = self.repository.post(record.object_id)
        self._validate_post_record(record, post)
        finding = self.repository.finding_for_post(post.id)
        return {
            "type": "post",
            "name": post.title,
            "ref": context_ref,
            "verified_finding_preview": {
                "summary": _preview(finding.summary, 160),
                "categories": list(finding.categories),
                "risk_level": finding.risk_level,
                "risk_level_label": enum_label(
                    RISK_LEVEL_LABELS, finding.risk_level
                ),
                "decision": finding.decision,
                "decision_label": enum_label(
                    DECISION_LABELS, finding.decision
                ),
            },
        }

    def _risk_preview(
        self, post: Post, finding: Finding, post_ref: str, position: int
    ) -> dict[str, Any]:
        finding_preview = {
            "summary": _preview(finding.summary, 160),
            "categories": list(finding.categories),
            "risk_level": finding.risk_level,
            "risk_level_label": enum_label(
                RISK_LEVEL_LABELS, finding.risk_level
            ),
            "decision": finding.decision,
            "decision_label": enum_label(DECISION_LABELS, finding.decision),
        }
        if self.real_report_mode:
            finding_preview["primary_risk"] = (
                finding.categories[0] if finding.categories else ""
            )
        return {
            "position": position,
            "type": "post",
            "name": post.title,
            "ref": post_ref,
            "author_display_name": post.author.display_name,
            "post_content_preview": {
                "source": "报告冻结帖子正文片段",
                "text": _preview(post.body, 180),
                "preview_is_full_post": False,
            },
            "finding_preview": finding_preview,
            "preview_only": True,
        }

    def _success(self, **kwargs: Any) -> str:
        if self.real_report_mode:
            scope = {
                "report_name": self.repository.report.title,
                "published_report_bound": True,
                "current_investigation_task_bound": True,
                "frozen_source_bound": True,
            }
            authority_basis = (
                "authorized_published_report_and_immutable_source_projection"
            )
        else:
            scope = {
                "report_name": self.repository.report.title,
                "report_revision": self.repository.report.public_revision,
                "current_investigation_task_bound": True,
                "frozen_snapshot": True,
            }
            authority_basis = "server_bound_task_report_and_frozen_snapshot"
        return success_result(
            scope=scope,
            authority_basis=authority_basis,
            **kwargs,
        )

    def _real_repository(self) -> PublishedReportRepository:
        if not isinstance(self.repository, PublishedReportRepository):
            raise RuntimeError("published ReportVersion repository is not configured")
        return self.repository


def _discovery_fingerprint(
    query_text: str,
    *,
    risk_level: str | None,
    decision: str | None,
    context_record: ReportTaskReferenceRecord | None,
    requested_count: int | None = None,
) -> str:
    payload = {
        "query_text": normalize_search_text(query_text),
        "risk_level": risk_level,
        "decision": decision,
        "context": (
            None
            if context_record is None
            else {
                "kind": context_record.kind,
                "object_id": context_record.object_id,
            }
        ),
    }
    if requested_count is not None:
        payload["requested_count"] = requested_count
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ordered_post_ids_hash(posts: tuple[Post, ...]) -> str:
    return hashlib.sha256("\0".join(post.id for post in posts).encode()).hexdigest()
