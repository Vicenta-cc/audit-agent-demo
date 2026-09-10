"""R3.1 report-local Account activity projection from one frozen Snapshot."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.domain.identity import stable_hash
from backend.reporting.account_entries import (
    DEFAULT_ACCOUNT_FIXTURE_PATH,
    DEFAULT_ACTIVE_COMMENT_ACCOUNT_LIMIT,
    _current_display_name,
    _platform,
    _source_account_ref,
)
from backend.reporting.errors import ReportValidationError
from backend.reporting.integration_source import ImmutableReportSnapshot
from hermes_m0.account_corpus import (
    DOUYIN_ACCOUNT_NAMESPACE,
    AccountCorpus,
    account_ref,
    normalize_source_id,
)


REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION = "report-account-entry-r3.1/v1"
RISK_LEVELS = {"low", "medium", "high"}
RISK_DECISIONS = {"review", "reject"}
_COMMENT_REQUIRED_FIELDS = {
    "comment_id",
    "sec_uid",
    "audit_status",
    "risk_level",
    "risk_score",
    "risk_type",
    "content",
    "create_time",
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_entry_ref(snapshot_hash: str, account_id: str) -> str:
    return "account-entry-" + stable_hash(
        {
            "schema_version": REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION,
            "snapshot_hash": snapshot_hash,
            "account_id": account_id,
        }
    )[:16]


def _timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if raw.lstrip("-").isdigit():
            parsed = datetime.fromtimestamp(int(raw), UTC)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            parsed = parsed.astimezone(UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ReportValidationError(
            "build_report_account_overview",
            ["a frozen Account occurrence has an invalid timestamp"],
        ) from exc
    return parsed.isoformat().replace("+00:00", "Z")


def _is_risk_finding(payload: dict[str, Any]) -> bool:
    return (
        str(payload.get("decision") or "").lower() in RISK_DECISIONS
        or str(payload.get("risk_level") or "").lower() in RISK_LEVELS
    )


def _presentation_lines(entry: dict[str, Any], *, group: str) -> list[str]:
    stats = entry["current_investigation_statistics"]
    lines: list[str] = []
    if group == "target" and int(stats["published_post_count"]):
        lines.append(
            f"已审核发布 {stats['published_post_count']} 篇 · "
            f"风险或复审 {stats['risk_published_post_count']} 篇"
        )
    if int(stats["comment_count"]):
        lines.append(
            f"已审核评论 {stats['comment_count']} 条 · "
            f"风险评论 {stats['risk_comment_count']} 条"
        )
        if group == "active_comment":
            lines.append(
                f"涉及 {stats['commented_post_count']} 篇帖子 · "
                f"涉及 {stats['commented_post_author_count']} 位帖子作者"
            )
    if stats.get("latest_activity_at"):
        lines.append(f"最近活动 {stats['latest_activity_at']}")
    return lines


def _public_entry(entry: dict[str, Any], *, group: str) -> dict[str, Any]:
    statistics = dict(entry["current_investigation_statistics"])
    return {
        "entry_ref": entry["entry_ref"],
        "display_name": entry["display_name"],
        "roles": list(entry["roles"]),
        "is_target_account": bool(entry["is_target_account"]),
        **statistics,
        "target_display_ordinal": entry["target_display_ordinal"],
        "active_comment_rank": entry["active_comment_rank"],
        "active_comment_display_ordinal": entry[
            "active_comment_display_ordinal"
        ],
        "default_visible": bool(entry["default_visible"]),
        "presentation_lines": _presentation_lines(entry, group=group),
    }


def public_account_overview_projection(
    projection: dict[str, Any],
) -> dict[str, Any]:
    entries = projection["entries"]
    targets = sorted(
        (item for item in entries if item["is_target_account"]),
        key=lambda item: int(item["target_display_ordinal"]),
    )
    active_comments = sorted(
        (
            item
            for item in entries
            if item["active_comment_display_ordinal"] is not None
        ),
        key=lambda item: int(item["active_comment_display_ordinal"]),
    )
    first_page_size = min(50, len(entries))
    return {
        "schema_version": projection["schema_version"],
        "projection_hash": projection["projection_hash"],
        "account_coverage_statistics": dict(projection["statistics"]),
        "default_active_comment_limit": projection[
            "default_active_comment_limit"
        ],
        "target_account_entries": [
            _public_entry(item, group="target") for item in targets
        ],
        "default_active_comment_entries": [
            _public_entry(item, group="active_comment")
            for item in active_comments
        ],
        "full_account_index": {
            "available": True,
            "total_count": len(entries),
            "comment_author_count": projection["statistics"][
                "comment_author_account_count"
            ],
            "default_page_size": 50,
            "maximum_page_size": 100,
            "initial_cursor": None,
            "first_page_next_cursor": (
                entries[first_page_size - 1]["entry_ref"]
                if first_page_size < len(entries)
                else None
            ),
            "cursor_semantics": "last_displayed_entry_ref",
        },
        "scope_boundary": (
            "报告账号统计仅覆盖当前调查；进入 Account Overview 后，"
            "默认查询当前用户已授权的全部调查数据。"
        ),
    }


class ReportAccountOverviewProjector:
    """Compute R3.1 Account statistics from frozen Posts and Comments."""

    def __init__(self, corpus: AccountCorpus, *, fixture_path: Path) -> None:
        self.corpus = corpus
        self.fixture_path = fixture_path.resolve()

    @classmethod
    def load(
        cls, fixture_path: Path = DEFAULT_ACCOUNT_FIXTURE_PATH
    ) -> "ReportAccountOverviewProjector":
        return cls(AccountCorpus.load(fixture_path), fixture_path=fixture_path)

    def build(
        self,
        snapshot: ImmutableReportSnapshot,
        *,
        target_account_identity: dict[str, str] | None,
    ) -> dict[str, Any]:
        task_id = snapshot.task_id
        try:
            task_snapshot = self.corpus.snapshot_for_task(task_id)
        except KeyError as exc:
            raise ReportValidationError(
                "build_report_account_overview",
                ["the Report task is outside the authorized Account corpus"],
            ) from exc
        if target_account_identity is None:
            raise ReportValidationError(
                "build_report_account_overview",
                ["the Report task has no explicit target Account projection"],
            )
        if set(target_account_identity) != {
            "platform",
            "source_namespace",
            "source_account_key",
        }:
            raise ReportValidationError(
                "build_report_account_overview",
                ["the Report task creator Account identity is incomplete"],
            )
        platform = _platform(target_account_identity["platform"])
        source_namespace = normalize_source_id(
            target_account_identity["source_namespace"]
        )
        source_account_key = normalize_source_id(
            target_account_identity["source_account_key"]
        )
        if (
            platform != "douyin"
            or source_namespace != DOUYIN_ACCOUNT_NAMESPACE
            or not source_account_key
        ):
            raise ReportValidationError(
                "build_report_account_overview",
                ["the Report task creator Account identity is unsupported or empty"],
            )
        configured_target_ref = account_ref(
            platform=platform,
            source_namespace=source_namespace,
            source_account_key=source_account_key,
        )

        snapshot_post_keys = {item.canonical_key for item in snapshot.posts}
        task_occurrences = [
            item
            for item in self.corpus.occurrences
            if str(item["task_id"]) == task_id
        ]
        corpus_post_keys = {
            str(item["post"]["content_key"])
            for item in task_occurrences
            if item["kind"] == "post_author"
        }
        if corpus_post_keys != snapshot_post_keys:
            raise ReportValidationError(
                "build_report_account_overview",
                ["Report Snapshot Posts do not match the authorized Account task snapshot"],
            )

        expected = self._frozen_occurrences(snapshot)
        expected_keys = {
            (item["kind"], item["content_key"], item["comment_id"])
            for item in expected
        }
        matching_occurrences = [
            item
            for item in task_occurrences
            if (
                str(item["kind"]),
                str(item["post"]["content_key"]),
                str((item.get("comment") or {}).get("comment_id") or ""),
            )
            in expected_keys
        ]
        actual_by_key = {
            (
                str(item["kind"]),
                str(item["post"]["content_key"]),
                str((item.get("comment") or {}).get("comment_id") or ""),
            ): item
            for item in matching_occurrences
        }
        if set(actual_by_key) != expected_keys or len(actual_by_key) != len(
            matching_occurrences
        ):
            raise ReportValidationError(
                "build_report_account_overview",
                ["Report Snapshot Account occurrences do not match the authorized Account corpus"],
            )

        occurrences_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for frozen in expected:
            key = (frozen["kind"], frozen["content_key"], frozen["comment_id"])
            corpus_occurrence = actual_by_key[key]
            account_id = str(corpus_occurrence.get("account_ref") or "")
            if not account_id or account_id != frozen["expected_account_ref"]:
                raise ReportValidationError(
                    "build_report_account_overview",
                    ["a frozen Account occurrence has an unresolved or mismatched identity"],
                )
            self.corpus.account(account_id)
            item = {**frozen, "internal_account_ref": account_id}
            occurrences_by_account[account_id].append(item)

        target_occurrences = occurrences_by_account.get(configured_target_ref) or []
        if not target_occurrences:
            raise ReportValidationError(
                "build_report_account_overview",
                ["the configured creator Account does not resolve in the frozen Report data"],
            )
        if not any(item["kind"] == "post_author" for item in target_occurrences):
            raise ReportValidationError(
                "build_report_account_overview",
                ["the configured creator Account is not a frozen Post author"],
            )
        target_account_refs = {configured_target_ref}

        staged: list[dict[str, Any]] = []
        for account_id, occurrences in occurrences_by_account.items():
            comments = [item for item in occurrences if item["kind"] == "comment_author"]
            posts = [item for item in occurrences if item["kind"] == "post_author"]
            activity_times = [
                item["occurred_at"] for item in occurrences if item["occurred_at"]
            ]
            display_observations = sorted(
                (
                    item["occurred_at"],
                    str(item.get("display_name") or ""),
                )
                for item in occurrences
                if item.get("display_name")
            )
            display_name = (
                display_observations[-1][1]
                if display_observations
                else _current_display_name(self.corpus, account_id, task_id)
            )
            statistics = {
                "comment_count": len(comments),
                "risk_comment_count": sum(
                    item["risk_level"] in RISK_LEVELS for item in comments
                ),
                "commented_post_count": len(
                    {item["content_key"] for item in comments}
                ),
                "commented_post_author_count": len(
                    {item["parent_post_author_account_ref"] for item in comments}
                ),
                "earliest_activity_at": min(activity_times, default=None),
                "latest_activity_at": max(activity_times, default=None),
                "published_post_count": len(posts),
                "risk_published_post_count": sum(
                    bool(item["is_risk_post"]) for item in posts
                ),
            }
            staged.append(
                {
                    "entry_ref": _safe_entry_ref(snapshot.snapshot_hash, account_id),
                    "internal_account_ref": account_id,
                    "display_name": display_name or "未记录昵称的账号",
                    "roles": [
                        role
                        for role, values in (
                            ("post_author", posts),
                            ("comment_author", comments),
                        )
                        if values
                    ],
                    "is_target_account": account_id in target_account_refs,
                    "current_investigation_statistics": statistics,
                    "target_display_ordinal": None,
                    "active_comment_rank": None,
                    "active_comment_display_ordinal": None,
                    "default_visible": False,
                }
            )

        targets = sorted(
            (item for item in staged if item["is_target_account"]),
            key=lambda item: str(item["internal_account_ref"]),
        )
        for ordinal, item in enumerate(targets, 1):
            item["target_display_ordinal"] = ordinal
            item["default_visible"] = True

        commenters = sorted(
            (
                item
                for item in staged
                if "comment_author" in item["roles"]
            ),
            key=self._comment_sort_key,
        )
        for rank, item in enumerate(commenters, 1):
            item["active_comment_rank"] = rank
        default_commenters = [
            item for item in commenters if not item["is_target_account"]
        ][:DEFAULT_ACTIVE_COMMENT_ACCOUNT_LIMIT]
        for ordinal, item in enumerate(default_commenters, 1):
            item["active_comment_display_ordinal"] = ordinal
            item["default_visible"] = True

        staged.sort(
            key=lambda item: (
                item["active_comment_rank"] is None,
                item["active_comment_rank"] or 0,
                str(item["internal_account_ref"]),
            )
        )
        statistics = {
            "target_account_count": len(targets),
            "post_author_account_count": sum(
                "post_author" in item["roles"] for item in staged
            ),
            "comment_author_account_count": len(commenters),
            "distinct_account_count": len(staged),
            "default_active_comment_account_count": len(default_commenters),
            "full_account_index_available": True,
        }
        identity = {
            "schema_version": REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION,
            "report_snapshot_hash": snapshot.snapshot_hash,
            "account_corpus_schema_version": self.corpus.schema_version,
            "account_corpus_revision": self.corpus.corpus_revision,
            "account_fixture_sha256": _file_sha256(self.fixture_path),
            "account_task_snapshot_ref": str(task_snapshot["snapshot_ref"]),
            "account_task_source_hash": str(task_snapshot["source_hash"]),
            "occurrence_set_hash": stable_hash(
                sorted(
                    (
                        item["kind"],
                        item["content_key"],
                        item["comment_id"],
                        item["expected_account_ref"],
                    )
                    for item in expected
                )
            ),
            "default_active_comment_limit": DEFAULT_ACTIVE_COMMENT_ACCOUNT_LIMIT,
            "statistics": statistics,
            "entries": staged,
        }
        return {**identity, "projection_hash": stable_hash(identity)}

    @staticmethod
    def _comment_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        statistics = item["current_investigation_statistics"]
        latest = str(statistics["latest_activity_at"] or "")
        try:
            latest_rank = datetime.fromisoformat(
                latest.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            latest_rank = float("-inf")
        return (
            -int(statistics["comment_count"]),
            -int(statistics["commented_post_count"]),
            -int(statistics["commented_post_author_count"]),
            -latest_rank,
            str(item["internal_account_ref"]),
        )

    @staticmethod
    def _frozen_occurrences(
        snapshot: ImmutableReportSnapshot,
    ) -> list[dict[str, Any]]:
        finding_by_post = {
            item.post_ref: item.payload for item in snapshot.findings
        }
        if set(finding_by_post) != {item.ref for item in snapshot.posts}:
            raise ReportValidationError(
                "build_report_account_overview",
                ["Frozen Posts and effective Findings are not one-to-one"],
            )
        occurrences: list[dict[str, Any]] = []
        identities: set[tuple[str, str, str]] = set()
        for post in snapshot.posts:
            payload = post.payload.get("raw_content_payload") or {}
            if not isinstance(payload, dict):
                raise ReportValidationError(
                    "build_report_account_overview",
                    ["a frozen Post lacks structured source content"],
                )
            platform = _platform(post.payload.get("platform"))
            author = payload.get("author") or {}
            if not isinstance(author, dict):
                author = {}
            post_identity = ("post_author", post.canonical_key, "")
            identities.add(post_identity)
            occurrences.append(
                {
                    "kind": "post_author",
                    "content_key": post.canonical_key,
                    "comment_id": "",
                    "expected_account_ref": _source_account_ref(platform, author),
                    "display_name": str(author.get("nickname") or ""),
                    "occurred_at": _timestamp(post.payload.get("published_at")),
                    "risk_level": "",
                    "parent_post_author_account_ref": _source_account_ref(
                        platform, author
                    ),
                    "is_risk_post": _is_risk_finding(
                        finding_by_post[post.ref]
                    ),
                }
            )
            comments = post.payload.get("comments")
            if comments is None:
                comments = payload.get("comments") or []
            if not isinstance(comments, list):
                raise ReportValidationError(
                    "build_report_account_overview",
                    ["a frozen Post has a non-list Comment projection"],
                )
            for comment in comments:
                if not isinstance(comment, dict):
                    raise ReportValidationError(
                        "build_report_account_overview",
                        ["a frozen Comment projection is not an object"],
                    )
                missing = sorted(_COMMENT_REQUIRED_FIELDS - set(comment))
                if missing:
                    raise ReportValidationError(
                        "build_report_account_overview",
                        ["a frozen Comment lacks required review fields"],
                    )
                if str(comment["audit_status"] or "") != "completed":
                    continue
                comment_id = normalize_source_id(comment["comment_id"])
                if not comment_id:
                    raise ReportValidationError(
                        "build_report_account_overview",
                        ["a frozen completed Comment is missing comment_id"],
                    )
                identity = ("comment_author", post.canonical_key, comment_id)
                if identity in identities:
                    raise ReportValidationError(
                        "build_report_account_overview",
                        ["the Report Snapshot contains a duplicate occurrence"],
                    )
                identities.add(identity)
                occurrences.append(
                    {
                        "kind": "comment_author",
                        "content_key": post.canonical_key,
                        "comment_id": comment_id,
                        "expected_account_ref": _source_account_ref(
                            platform, comment
                        ),
                        "display_name": str(comment.get("nickname") or ""),
                        "occurred_at": _timestamp(comment["create_time"]),
                        "risk_level": str(comment["risk_level"] or "").lower(),
                        "parent_post_author_account_ref": _source_account_ref(
                            platform, author
                        ),
                        "is_risk_post": False,
                    }
                )
        return occurrences
