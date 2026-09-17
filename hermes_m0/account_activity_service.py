"""Account Overview and occurrence navigation for M2."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
import json
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
            "search_accounts": self._search_accounts,
            "get_account_overview": self._get_account_overview,
            "list_account_occurrences": self._list_account_occurrences,
            "read_account_occurrence": self._read_account_occurrence,
            "read_account_post": self._read_account_post,
            "compare_authorized_report_accounts": (
                self._compare_authorized_report_accounts
            ),
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

    def expose_comment_evidence(
        self,
        session_id: str,
        *,
        task_id: str,
        content_key: str,
        comment_id: str,
        text: str,
        published_at: str,
    ) -> dict[str, str | None]:
        """Bridge one exact Comment Evidence to existing Account Activity refs.

        This lookup is deliberately independent of comment risk filtering. The
        caller has already selected a precise frozen Evidence relation.
        """
        repository = self._load_repository()
        occurrence = repository.comment_occurrence(task_id, content_key, comment_id)
        frozen = occurrence["comment"]
        if (
            frozen.get("text") != text
            or frozen.get("comment_time") != published_at
            or str(occurrence.get("task_id")) != task_id
            or str(occurrence.get("post", {}).get("content_key")) != content_key
            or str(occurrence.get("source_locator", {}).get("comment_id")) != comment_id
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
                source_tool="read_evidence",
                content_state="comment_evidence_author",
                repository=repository,
            )
            occurrence_token = self.refs.expose(
                session_id,
                kind="occurrence",
                object_id=str(occurrence["occurrence_ref"]),
                parent_account_id=str(account_id),
                corpus_revision=repository.corpus_revision,
                source_tool="read_evidence",
                content_state="comment_evidence",
            )
        return {
            "account_ref": account_token,
            "account_occurrence_ref": occurrence_token,
        }

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

    def _search_accounts(self, session_id: str, args: dict[str, Any]) -> str:
        _require_exact_keys(args, {'nickname', 'match_mode', 'limit', 'offset'}, required={'nickname'})
        nickname = _require_string(args['nickname'], 'nickname').strip()
        if not nickname or len(nickname) > 200:
            raise ToolInputError('invalid_arguments', 'nickname must contain 1 to 200 characters')
        match_mode = args.get('match_mode', 'exact')
        if match_mode not in ('exact', 'contains'):
            raise ToolInputError('invalid_arguments', 'match_mode must be exact or contains')
        limit = _require_int(args.get('limit', 20), 'limit', minimum=1, maximum=20)
        offset = _require_int(args.get('offset', 0), 'offset', minimum=0, maximum=1_000_000)
        repository = self._load_repository()
        matches = repository.search_accounts(nickname, match_mode=match_mode)
        selected = matches[offset:offset + limit]
        candidates = []
        overview = None
        for position, match in enumerate(selected, offset + 1):
            account_id = match['account_id']
            token = self.expose_account(session_id, account_id, source_tool='search_accounts',
                                        content_state='nickname_candidate', repository=repository)
            occurrences = repository.corpus.occurrences_for(account_id)
            source_names = sorted({repository.task_card(str(o['task_id']))['name'] for o in occurrences})
            candidates.append({'position': position, 'account_ref': token,
                               'display_name': match['display_name'],
                               'matched_nicknames': match['matched_nicknames'],
                               'source_investigations': source_names,
                               'comment_count': sum(o['kind'] == 'comment_author' for o in occurrences),
                               'published_post_count': sum(o['kind'] == 'post_author' for o in occurrences)})
            if match_mode == 'exact' and len(matches) == 1:
                record, token = self._resolve_account(session_id, token, repository)
                overview = json.loads(self._account_overview_result(session_id, repository, record, token))['data']
        total = len(matches)
        return self._success(
            tool='search_accounts', result_kind='authorized_account_search',
            content_state='unique_account_overview' if overview else 'account_candidates',
            data={'query': nickname, 'match_mode': match_mode, 'total_count': total,
                  'returned_count': len(candidates), 'offset': offset,
                  'next_offset': offset + len(candidates) if offset + len(candidates) < total else None,
                  'match_status': 'not_found' if not total else 'unique' if overview else 'needs_selection',
                  'candidates': candidates, 'overview': overview,
                  'authorization_scope': {'mode': 'all_currently_authorized_investigations',
                      'authorized_investigation_count': len(repository.corpus.authorized_task_ids)},
                  'next_action': ('Use the returned overview directly; use its account ref for activity details.'
                      if overview else 'Ask the user to select a candidate; do not merge names or choose the first.'
                      if total else 'No matching nickname in authorized stored data; do not infer platform-wide absence.')},
            not_loaded=['individual occurrence detail', 'historical AuditFinding or Evidence'],
            limitations=['Nicknames select candidates only. Stable platform account identifiers define identity.',
                         'All authorized accounts are searched; report preview cards do not limit this search.'],
        )

    def _get_account_overview(
        self, session_id: str, args: dict[str, Any]
    ) -> str:
        _require_exact_keys(args, {"account_ref"}, required={"account_ref"})
        account_ref = _require_string(args["account_ref"], "account_ref")
        repository = self._load_repository()
        account_record, session_account_ref = self._resolve_account(
            session_id, account_ref, repository
        )
        return self._account_overview_result(session_id, repository, account_record, session_account_ref)

    def _compare_authorized_report_accounts(
        self, session_id: str, args: dict[str, Any]
    ) -> str:
        """Compare every stable publisher/commenter in authorized unified reports.

        The stable account identity remains server-side.  The result deliberately
        contains no account references because it is intended for exhaustive
        natural-language comparison rather than follow-up object navigation.
        """

        _require_exact_keys(args, set())
        repository = self._load_repository()
        report_repositories = tuple(
            item
            for item in self.authorized_report_repositories
            if str(getattr(item, "template_kind", "")) == "unified_audit"
        )
        report_specs = []
        seen_tasks: set[str] = set()
        current_task_id = str(
            self.report_repository.fixture.provenance.source_task_id
        )
        for position, report_repository in enumerate(report_repositories, 1):
            task_id = str(report_repository.fixture.provenance.source_task_id)
            if task_id in seen_tasks:
                raise AccountActivityLookupError(
                    "duplicate_authorized_report_task",
                    "Authorized unified reports must have unique source investigations.",
                )
            seen_tasks.add(task_id)
            report_specs.append(
                {
                    "position": position,
                    "task_id": task_id,
                    "title": str(report_repository.report.title),
                    "is_current_report": task_id == current_task_id,
                }
            )

        counts_by_task: dict[str, dict[str, Counter[str]]] = {
            item["task_id"]: defaultdict(Counter) for item in report_specs
        }
        unresolved_by_task: dict[str, Counter[str]] = {
            item["task_id"]: Counter() for item in report_specs
        }
        for occurrence in repository.corpus.occurrences:
            task_id = str(occurrence.get("task_id") or "")
            if task_id not in counts_by_task:
                continue
            kind = str(occurrence.get("kind") or "")
            if kind not in {"post_author", "comment_author"}:
                continue
            stable_identity = str(occurrence.get("account_ref") or "")
            if not stable_identity:
                unresolved_by_task[task_id][kind] += 1
                continue
            counts_by_task[task_id][stable_identity][kind] += 1

        role_label = {
            "post_author": "发布者",
            "comment_author": "评论者",
        }

        def ordered_identities(values: set[str]) -> list[str]:
            return sorted(
                values,
                key=lambda identity: (
                    repository.display_name(identity).casefold(),
                    identity,
                ),
            )

        def roles_for(counter: Counter[str]) -> list[str]:
            return [
                role_label[kind]
                for kind in ("post_author", "comment_author")
                if counter.get(kind, 0)
            ]

        def natural_account(
            identity: str, counter: Counter[str], position: int
        ) -> dict[str, Any]:
            return {
                "position": position,
                "display_name": repository.display_name(identity),
                "roles": roles_for(counter),
                "published_post_count": int(counter.get("post_author", 0)),
                "comment_count": int(counter.get("comment_author", 0)),
            }

        accounts_by_report = []
        for report_spec in report_specs:
            task_id = report_spec["task_id"]
            task_counts = counts_by_task[task_id]
            identities = ordered_identities(set(task_counts))
            unresolved = unresolved_by_task[task_id]
            accounts_by_report.append(
                {
                    "report_position": report_spec["position"],
                    "report_title": report_spec["title"],
                    "is_current_report": report_spec["is_current_report"],
                    "account_count": len(identities),
                    "publisher_count": sum(
                        bool(task_counts[item].get("post_author"))
                        for item in identities
                    ),
                    "commenter_count": sum(
                        bool(task_counts[item].get("comment_author"))
                        for item in identities
                    ),
                    "accounts": [
                        natural_account(identity, task_counts[identity], position)
                        for position, identity in enumerate(identities, 1)
                    ],
                    "unresolved_activity": {
                        "publisher_occurrence_count": int(
                            unresolved.get("post_author", 0)
                        ),
                        "commenter_occurrence_count": int(
                            unresolved.get("comment_author", 0)
                        ),
                    },
                }
            )

        tasks_by_identity: dict[str, set[str]] = defaultdict(set)
        for task_id, account_counts in counts_by_task.items():
            for identity in account_counts:
                tasks_by_identity[identity].add(task_id)
        shared_identities = ordered_identities(
            {
                identity
                for identity, task_ids in tasks_by_identity.items()
                if len(task_ids) >= 2
            }
        )
        report_by_task = {item["task_id"]: item for item in report_specs}
        shared_accounts = []
        for position, identity in enumerate(shared_identities, 1):
            appearances = []
            for task_id in sorted(
                tasks_by_identity[identity],
                key=lambda value: report_by_task[value]["position"],
            ):
                counter = counts_by_task[task_id][identity]
                appearances.append(
                    {
                        "report_position": report_by_task[task_id]["position"],
                        "report_title": report_by_task[task_id]["title"],
                        "roles": roles_for(counter),
                        "published_post_count": int(
                            counter.get("post_author", 0)
                        ),
                        "comment_count": int(counter.get("comment_author", 0)),
                    }
                )
            shared_accounts.append(
                {
                    "position": position,
                    "display_name": repository.display_name(identity),
                    "report_count": len(appearances),
                    "report_appearances": appearances,
                }
            )

        pairwise_comparisons = []
        for first, second in combinations(report_specs, 2):
            first_counts = counts_by_task[first["task_id"]]
            second_counts = counts_by_task[second["task_id"]]
            first_ids = set(first_counts)
            second_ids = set(second_counts)
            common_ids = first_ids & second_ids
            common_publishers = {
                identity
                for identity in common_ids
                if first_counts[identity].get("post_author")
                and second_counts[identity].get("post_author")
            }
            common_commenters = {
                identity
                for identity in common_ids
                if first_counts[identity].get("comment_author")
                and second_counts[identity].get("comment_author")
            }

            def pair_entries(values: set[str]) -> list[dict[str, Any]]:
                return [
                    {
                        "position": position,
                        "display_name": repository.display_name(identity),
                        "first_report_roles": roles_for(first_counts[identity]),
                        "second_report_roles": roles_for(second_counts[identity]),
                    }
                    for position, identity in enumerate(
                        ordered_identities(values), 1
                    )
                ]

            pairwise_comparisons.append(
                {
                    "first_report": {
                        "position": first["position"],
                        "title": first["title"],
                    },
                    "second_report": {
                        "position": second["position"],
                        "title": second["title"],
                    },
                    "common_account_count": len(common_ids),
                    "common_accounts": pair_entries(common_ids),
                    "common_publisher_count": len(common_publishers),
                    "common_publishers": pair_entries(common_publishers),
                    "common_commenter_count": len(common_commenters),
                    "common_commenters": pair_entries(common_commenters),
                }
            )

        unresolved_activity_count = sum(
            sum(counter.values()) for counter in unresolved_by_task.values()
        )
        return success_result(
            tool="compare_authorized_report_accounts",
            result_kind="authorized_unified_report_account_comparison",
            content_state="complete_stable_identity_comparison",
            scope={
                "report_name": self.report_repository.report.title,
                "account_activity_scope": (
                    "authorized_published_unified_audit_reports"
                ),
                "source_task_is_metadata_only": True,
            },
            authority_basis="current_authorized_unified_report_account_corpus",
            data={
                "scope_label": "当前用户已授权的新增统一审核报告",
                "identity_basis": "抖音稳定账号标识；同昵称不会自动合并",
                "report_count": len(report_specs),
                "reports_compared": [
                    {
                        "position": item["position"],
                        "title": item["title"],
                        "is_current_report": item["is_current_report"],
                    }
                    for item in report_specs
                ],
                "accounts_by_report": accounts_by_report,
                "shared_account_count": len(shared_accounts),
                "shared_accounts": shared_accounts,
                "pairwise_comparisons": pairwise_comparisons,
                "comparison_complete_for_stable_accounts": True,
                "unresolved_activity_count": unresolved_activity_count,
            },
            not_loaded=[
                "historical AuditFinding or Evidence",
                "activities without a stable platform account identity",
            ],
            limitations=[
                "Only published unified-audit reports in the current authorized set are compared.",
                "Every stable publisher and commenter is enumerated; activities without stable identity are counted but never merged by nickname.",
                "The result intentionally exposes no internal account reference or platform identity value.",
            ],
        )

    def _account_overview_result(self, session_id, repository, account_record, session_account_ref):
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
        allowed_origins = {
            ("list_account_occurrences", "preview"),
            ("list_post_risk_comments", "preview"),
            ("read_evidence", "comment_evidence"),
        }
        if not any(
            (origin.source_tool, origin.content_state) in allowed_origins
            for origin in occurrence_record.origins
        ) or (
            occurrence_record.source_tool,
            occurrence_record.content_state,
        ) not in allowed_origins:
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
