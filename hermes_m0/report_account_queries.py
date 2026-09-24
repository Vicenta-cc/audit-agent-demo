"""Bounded, explicitly selected report-account queries using session aliases."""
from collections import Counter, defaultdict
from hashlib import sha256
import json

from .service import ToolInputError, _require_exact_keys, _require_int, _require_string
from .tool_results import success_result


class ReportAccountQueries:
    def _report_statistics(self, repository, spec):
        counts, _ = self._counts(repository, [spec])
        entries = counts[spec["task_id"]]
        totals, unresolved = Counter(), Counter()
        for item in repository.corpus.occurrences:
            kind = item.get("kind")
            if item.get("task_id") == spec["task_id"] and kind in ("post_author", "comment_author"):
                totals[kind] += 1
                if not item.get("account_ref"):
                    unresolved[kind] += 1
        return entries, {
            "publisher_account_count": sum(bool(c["post_author"]) for c in entries.values()),
            "commenter_account_count": sum(bool(c["comment_author"]) for c in entries.values()),
            "distinct_account_count": len(entries),
            "published_post_count": totals["post_author"],
            "comment_count": totals["comment_author"],
            "unresolved_comment_count": unresolved["comment_author"],
            "unresolved_post_count": unresolved["post_author"],
            "account_counts_complete": not any(unresolved.values()),
            "count_basis": "full_report_snapshot_stable_identities",
        }

    def current_report_account_statistics(self):
        repository = self._load_repository()
        specs, _ = self._report_catalog(repository)
        spec = next((s for s in specs if s["is_current_report"]), None)
        if spec is None:
            raise ToolInputError("report_not_authorized", "Current unified report is unavailable.")
        return self._report_statistics(repository, spec)[1]

    def _get_report_account_statistics(self, session_id, args):
        _require_exact_keys(args, {"report_ref", "role", "top_n"})
        role = args.get("role", "comment_author")
        if role not in ("comment_author", "post_author"):
            raise ToolInputError("invalid_arguments", "role must be comment_author or post_author")
        top_n = _require_int(args.get("top_n", 5), "top_n", minimum=1, maximum=20)
        repository = self._load_repository()
        specs, revision = self._report_catalog(repository)
        if "report_ref" in args:
            spec = self._select_reports(session_id, [args["report_ref"]], specs, revision)[0]
        else:
            spec = next((s for s in specs if s["is_current_report"]), None)
            if spec is None:
                raise ToolInputError("report_not_authorized", "Current unified report is unavailable.")
        entries, statistics = self._report_statistics(repository, spec)
        ordered = sorted((key for key, counts in entries.items() if counts[role]),
                         key=lambda key: (-entries[key][role], key))
        frequencies = Counter(entries[key][role] for key in ordered)
        ranks, offset = {}, 1
        for count in sorted(frequencies, reverse=True):
            ranks[count] = offset
            offset += frequencies[count]
        tool = "get_report_account_statistics"
        accounts = []
        for position, identity in enumerate(ordered[:top_n], 1):
            item = self._account_card(session_id, repository, identity, [spec],
                                      {spec["task_id"]: entries}, position, tool)
            count = entries[identity][role]
            item.update(activity_count=count, rank=ranks[count], tied_account_count=frequencies[count])
            accounts.append(item)
        cutoff = entries[ordered[min(top_n, len(ordered)) - 1]][role] if ordered else None
        return self._report_query_result(tool, {
            "report": self._report_card(session_id, spec, revision),
            "statistics": statistics, "role": role,
            "ranking_metric": "comment_count" if role == "comment_author" else "published_post_count",
            "ranking_order": "activity_count_desc_then_stable_identity_asc",
            "top_n": top_n, "account_count": len(ordered), "accounts": accounts,
            "returned_count": len(accounts), "has_more": len(ordered) > top_n,
            "cutoff_tied_account_count": frequencies[cutoff] if cutoff is not None else 0,
            "cutoff_omitted_tied_count": sum(entries[key][role] == cutoff for key in ordered[top_n:]),
        })

    def _report_catalog(self, repository):
        specs = []
        seen = set()
        current = str(self.report_repository.fixture.provenance.source_task_id)
        for report in self.authorized_report_repositories:
            if getattr(report, "template_kind", "") != "unified_audit":
                continue
            task_id = str(report.fixture.provenance.source_task_id)
            if task_id in seen:
                raise ToolInputError("duplicate_authorized_report_task",
                                     "Authorized reports must have unique source investigations.")
            seen.add(task_id)
            specs.append({
                "task_id": task_id, "title": str(report.report.title),
                "position": len(specs) + 1, "is_current_report": task_id == current,
                "snapshot_hash": str(report.snapshot_hash),
                "content_hash": str(report.content_hash),
            })
        revision = self._query_hash([repository.corpus_revision, specs])
        return specs, revision

    @staticmethod
    def _query_hash(value):
        return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _report_card(self, session_id, spec, revision):
        token = self.refs.expose(
            session_id, kind="report", object_id=spec["task_id"],
            parent_account_id=None, corpus_revision=revision,
            source_tool="list_authorized_reports", content_state="authorized_report",
        )
        return {key: spec[key] for key in ("title", "position", "is_current_report")} | {
            "report_ref": token,
        }

    def _select_reports(self, session_id, tokens, specs, revision):
        selected = []
        by_task = {spec["task_id"]: spec for spec in specs}
        for token in tokens:
            _require_string(token, "report_ref")
            record = self.refs.resolve(session_id, token, expected_kind="report",
                                       corpus_revision=revision)
            if record.object_id not in by_task:
                raise ToolInputError("report_not_authorized", "Report is outside the authorized set.")
            if any(spec["task_id"] == record.object_id for spec in selected):
                raise ToolInputError("invalid_arguments", "Select distinct reports.")
            selected.append(by_task[record.object_id])
        # Canonical order lets continuation work even if the caller reorders refs.
        return sorted(selected, key=lambda spec: spec["position"])

    def _query_page(self, session_id, args, identities, *, tool, query, revision):
        limit = _require_int(args.get("limit", 10), "limit", minimum=1, maximum=20)
        signature = self._query_hash(query)
        cursor_fields = {
            "account_id": signature, "kind": tool,
            "comment_target_account_id": None, "risk_filter": None,
            "corpus_revision": revision,
            "ordered_occurrence_hash": self._query_hash(identities),
        }
        offset = 0
        if "cursor" in args:
            token = _require_string(args["cursor"], "cursor")
            offset = self.refs.resolve_cursor(session_id, token, **cursor_fields).next_offset
        end = min(offset + limit, len(identities))
        cursor = self.refs.issue_cursor(
            session_id, **cursor_fields, next_offset=end
        ) if end < len(identities) else None
        return identities[offset:end], offset, {
            "returned_count": end - offset, "has_more": cursor is not None,
            "cursor": cursor, "page_complete": cursor is None,
        }

    def _list_authorized_reports(self, session_id, args):
        _require_exact_keys(args, {"limit", "cursor"})
        repository = self._load_repository()
        specs, revision = self._report_catalog(repository)
        selected, _, page = self._query_page(
            session_id, args, specs, tool="list_authorized_reports",
            query={}, revision=revision,
        )
        return self._report_query_result("list_authorized_reports", {
            "report_count": len(specs),
            "reports": [self._report_card(session_id, spec, revision) for spec in selected],
            **page,
        })

    @staticmethod
    def _role(args):
        role = args.get("role", "any")
        if role not in ("any", "post_author", "comment_author"):
            raise ToolInputError("invalid_arguments", "role must be any, post_author or comment_author")
        return role

    @staticmethod
    def _counts(repository, specs):
        counts = {spec["task_id"]: defaultdict(Counter) for spec in specs}
        unresolved = Counter()
        for item in repository.corpus.occurrences:
            task = str(item.get("task_id") or "")
            kind = item.get("kind")
            if task not in counts or kind not in ("post_author", "comment_author"):
                continue
            identity = str(item.get("account_ref") or "")
            if identity:
                counts[task][identity][kind] += 1
            else:
                unresolved[task] += 1
        return counts, unresolved

    @staticmethod
    def _ordered(repository, identities):
        return sorted(identities, key=lambda key: (repository.display_name(key).casefold(), key))

    def _account_card(self, session_id, repository, identity, specs, counts, position, tool):
        appearances = []
        for spec in specs:
            counter = counts[spec["task_id"]][identity]
            appearances.append({
                "report_position": spec["position"], "report_title": spec["title"],
                "roles": [label for kind, label in (("post_author", "发布者"), ("comment_author", "评论者"))
                          if counter[kind]],
                "published_post_count": counter["post_author"], "comment_count": counter["comment_author"],
            })
        return {
            "position": position, "display_name": repository.display_name(identity),
            "account_ref": self.expose_account(
                session_id, identity, source_tool=tool,
                content_state="report_account_query", repository=repository,
            ),
            "report_appearances": appearances,
        }

    def _compare_authorized_report_accounts(self, session_id, args):
        _require_exact_keys(args, {"report_refs", "role", "limit", "cursor"}, required={"report_refs"})
        tokens = args["report_refs"]
        if not isinstance(tokens, list) or not 2 <= len(tokens) <= 10:
            raise ToolInputError("invalid_arguments", "Select 2 to 10 distinct report refs.")
        role = self._role(args)
        repository = self._load_repository()
        specs, revision = self._report_catalog(repository)
        selected = self._select_reports(session_id, tokens, specs, revision)
        counts, unresolved = self._counts(repository, selected)

        def intersection(kind):
            return set.intersection(*[
                {identity for identity, counter in counts[spec["task_id"]].items()
                 if kind == "any" or counter[kind]}
                for spec in selected
            ])

        common = intersection("any")
        publishers = intersection("post_author")
        commenters = intersection("comment_author")
        matched = {"any": common, "post_author": publishers, "comment_author": commenters}[role]
        ordered = self._ordered(repository, matched)
        tool = "compare_authorized_report_accounts"
        identities, offset, page = self._query_page(
            session_id, args, ordered, tool=tool,
            query={"tasks": [spec["task_id"] for spec in selected], "role": role}, revision=revision,
        )
        return self._report_query_result(tool, {
            "reports_compared": [self._report_card(session_id, spec, revision) for spec in selected],
            "match_semantics": "present_in_every_selected_report",
            "role": role, "has_common_accounts": bool(common),
            "common_account_count": len(common), "common_publisher_count": len(publishers),
            "common_commenter_count": len(commenters), "matched_account_count": len(matched),
            "accounts": [self._account_card(session_id, repository, identity, selected, counts, position, tool)
                         for position, identity in enumerate(identities, offset + 1)],
            "comparison_complete_for_stable_accounts": True,
            "unresolved_activity_count": sum(unresolved.values()),
            **page,
        })

    def _list_report_accounts(self, session_id, args):
        _require_exact_keys(args, {"report_ref", "role", "limit", "cursor"}, required={"report_ref"})
        role = self._role(args)
        repository = self._load_repository()
        specs, revision = self._report_catalog(repository)
        selected = self._select_reports(session_id, [args["report_ref"]], specs, revision)
        counts, unresolved = self._counts(repository, selected)
        entries = counts[selected[0]["task_id"]]
        ordered = self._ordered(repository, {
            identity for identity, counter in entries.items() if role == "any" or counter[role]
        })
        tool = "list_report_accounts"
        identities, offset, page = self._query_page(
            session_id, args, ordered, tool=tool,
            query={"task": selected[0]["task_id"], "role": role}, revision=revision,
        )
        return self._report_query_result(tool, {
            "report": self._report_card(session_id, selected[0], revision),
            "role": role, "account_count": len(ordered),
            "accounts": [self._account_card(session_id, repository, identity, selected, counts, position, tool)
                         for position, identity in enumerate(identities, offset + 1)],
            "unresolved_activity_count": sum(unresolved.values()), **page,
        })

    @staticmethod
    def _report_query_result(tool, data):
        return success_result(
            tool=tool, result_kind=tool, content_state="bounded_directory_with_complete_counts",
            scope={"account_activity_scope": "selected_authorized_unified_reports"},
            authority_basis="current_authorized_unified_report_account_corpus",
            data=data, not_loaded=["account activity details", "historical AuditFinding or Evidence"],
            limitations=[
                "Counts cover the full selected scope; accounts/reports contain only this page.",
                "Only stable identities are compared; unresolved activities are excluded, not merged by nickname.",
                "Session references are for tool navigation only and must not be displayed to users.",
                "Account detail navigation retains the existing authorized activity scope, not only the selected reports.",
            ],
        )
