"""Deterministic current-investigation Account entries for Report R3."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.domain.identity import stable_hash
from backend.reporting.errors import ReportValidationError
from backend.reporting.integration_source import ImmutableReportSnapshot
from hermes_m0.account_corpus import (
    DOUYIN_ACCOUNT_NAMESPACE,
    AccountCorpus,
    account_ref,
    normalize_source_id,
)


REPORT_ACCOUNT_SCHEMA_VERSION = "report-account-entry-r3/v1"
DEFAULT_ACTIVE_COMMENT_ACCOUNT_LIMIT = 5
DEFAULT_ACCOUNT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "demo"
    / "seed"
    / "account_m22_corpus.json.gz"
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _platform(value: Any) -> str:
    normalized = normalize_source_id(value).lower()
    return "douyin" if normalized in {"dy", "douyin"} else normalized


def _source_account_ref(platform: str, source: dict[str, Any]) -> str:
    if platform != "douyin":
        raise ReportValidationError(
            "build_report_account_entries",
            [f"unsupported Account identity platform: {platform or 'empty'}"],
        )
    source_key = normalize_source_id(source.get("sec_uid"))
    if not source_key:
        raise ReportValidationError(
            "build_report_account_entries",
            ["a frozen Post or Comment is missing its stable Account identity"],
        )
    return account_ref(
        platform=platform,
        source_namespace=DOUYIN_ACCOUNT_NAMESPACE,
        source_account_key=source_key,
    )


def _safe_entry_ref(snapshot_hash: str, account_id: str) -> str:
    return "account-entry-" + stable_hash(
        {
            "schema_version": REPORT_ACCOUNT_SCHEMA_VERSION,
            "snapshot_hash": snapshot_hash,
            "account_id": account_id,
        }
    )[:16]


def _current_display_name(corpus: AccountCorpus, account_id: str, task_id: str) -> str:
    aliases = [
        item for item in corpus.aliases_for(account_id) if str(item["task_id"]) == task_id
    ]
    if not aliases:
        return "未记录昵称的账号"
    aliases.sort(
        key=lambda item: (
            str(item["observed_at"]),
            str(item["captured_at"]),
            str(item["alias_observation_ref"]),
        )
    )
    return str(aliases[-1]["nickname"] or "未记录昵称的账号")


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_ref": entry["entry_ref"],
        "display_name": entry["display_name"],
        "roles": list(entry["roles"]),
        "current_investigation_statistics": dict(
            entry["current_investigation_statistics"]
        ),
        "target_display_order": entry["target_display_order"],
        "active_comment_display_order": entry["active_comment_display_order"],
        "default_active_comment_visible": entry["default_active_comment_visible"],
    }


def public_account_projection(projection: dict[str, Any]) -> dict[str, Any]:
    entries = projection["entries"]
    targets = sorted(
        (item for item in entries if item["target_display_order"] is not None),
        key=lambda item: int(item["target_display_order"]),
    )
    active_comments = sorted(
        (
            item
            for item in entries
            if item["default_active_comment_visible"]
            and item["active_comment_display_order"] is not None
        ),
        key=lambda item: int(item["active_comment_display_order"]),
    )
    return {
        "schema_version": projection["schema_version"],
        "projection_hash": projection["projection_hash"],
        "statistics": dict(projection["statistics"]),
        "default_active_comment_limit": projection[
            "default_active_comment_limit"
        ],
        "groups": {
            "target_accounts": [_public_entry(item) for item in targets],
            "active_comment_accounts": [
                _public_entry(item) for item in active_comments
            ],
        },
        "full_account_index_available": True,
        "scope_boundary": (
            "报告账号统计仅覆盖当前调查；进入 Account Overview 后，"
            "默认查询当前用户已授权的全部调查数据。"
        ),
    }


class ReportAccountProjector:
    """Build an immutable report-local Account index without model involvement."""

    def __init__(self, corpus: AccountCorpus, *, fixture_path: Path) -> None:
        self.corpus = corpus
        self.fixture_path = fixture_path.resolve()

    @classmethod
    def load(cls, fixture_path: Path) -> "ReportAccountProjector":
        return cls(AccountCorpus.load(fixture_path), fixture_path=fixture_path)

    def build(self, snapshot: ImmutableReportSnapshot) -> dict[str, Any]:
        task_id = snapshot.task_id
        try:
            task_snapshot = self.corpus.snapshot_for_task(task_id)
        except KeyError as exc:
            raise ReportValidationError(
                "build_report_account_entries",
                ["the Report task is outside the authorized Account corpus"],
            ) from exc

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
        account_occurrences = [
            item
            for item in task_occurrences
            if str(item["post"]["content_key"]) in snapshot_post_keys
        ]
        if corpus_post_keys != snapshot_post_keys:
            raise ReportValidationError(
                "build_report_account_entries",
                ["Report Snapshot Posts do not match the authorized Account task snapshot"],
            )

        expected_occurrences = self._snapshot_occurrence_identities(snapshot)
        actual_occurrences = {
            (
                str(item["kind"]),
                str(item["post"]["content_key"]),
                str((item.get("comment") or {}).get("comment_id") or ""),
                str(item["account_ref"] or ""),
            )
            for item in account_occurrences
        }
        if expected_occurrences != actual_occurrences:
            raise ReportValidationError(
                "build_report_account_entries",
                ["Report Snapshot Account occurrences do not match the authorized Account corpus"],
            )
        if len(actual_occurrences) != len(account_occurrences):
            raise ReportValidationError(
                "build_report_account_entries",
                ["authorized Account occurrences contain duplicate current-task identities"],
            )

        occurrences_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in account_occurrences:
            account_id = str(item["account_ref"] or "")
            if not account_id:
                raise ReportValidationError(
                    "build_report_account_entries",
                    ["the current Report contains an unresolved Account occurrence"],
                )
            self.corpus.account(account_id)
            occurrences_by_account[account_id].append(item)

        staged: list[dict[str, Any]] = []
        for account_id, occurrences in occurrences_by_account.items():
            comments = [item for item in occurrences if item["kind"] == "comment_author"]
            posts = [item for item in occurrences if item["kind"] == "post_author"]
            target_accounts = {
                str(item["parent_post_author_account_ref"])
                for item in comments
                if item["parent_post_author_account_ref"] is not None
            }
            activity_times = [str(item["occurred_at"]) for item in occurrences]
            staged.append(
                {
                    "entry_ref": _safe_entry_ref(snapshot.snapshot_hash, account_id),
                    "internal_account_ref": account_id,
                    "display_name": _current_display_name(
                        self.corpus, account_id, task_id
                    ),
                    "roles": [
                        role
                        for role, values in (
                            ("post_author", posts),
                            ("comment_author", comments),
                        )
                        if values
                    ],
                    "current_investigation_statistics": {
                        "comment_count": len(comments),
                        "commented_post_count": len(
                            {str(item["post"]["content_key"]) for item in comments}
                        ),
                        "commented_post_author_count": len(target_accounts),
                        "earliest_activity_at": min(activity_times, default=None),
                        "latest_activity_at": max(activity_times, default=None),
                        "published_post_count": len(posts),
                    },
                    "target_display_order": None,
                    "active_comment_display_order": None,
                    "default_active_comment_visible": False,
                }
            )

        targets = sorted(
            (item for item in staged if "post_author" in item["roles"]),
            key=lambda item: (
                str(item["display_name"]),
                str(item["internal_account_ref"]),
            ),
        )
        for position, item in enumerate(targets, 1):
            item["target_display_order"] = position

        commenters = sorted(
            (item for item in staged if "comment_author" in item["roles"]),
            key=lambda item: self._comment_sort_key(item),
        )
        for position, item in enumerate(commenters, 1):
            item["active_comment_display_order"] = position
            item["default_active_comment_visible"] = (
                position <= DEFAULT_ACTIVE_COMMENT_ACCOUNT_LIMIT
            )

        staged.sort(
            key=lambda item: (
                0 if item["target_display_order"] is not None else 1,
                item["target_display_order"] or item["active_comment_display_order"] or 0,
                str(item["display_name"]),
                str(item["internal_account_ref"]),
            )
        )
        statistics = {
            "target_account_count": len(targets),
            "comment_account_count": len(commenters),
            "all_account_count": len(staged),
            "default_active_comment_account_count": min(
                len(commenters), DEFAULT_ACTIVE_COMMENT_ACCOUNT_LIMIT
            ),
        }
        identity = {
            "schema_version": REPORT_ACCOUNT_SCHEMA_VERSION,
            "report_snapshot_hash": snapshot.snapshot_hash,
            "account_corpus_schema_version": self.corpus.schema_version,
            "account_corpus_revision": self.corpus.corpus_revision,
            "account_fixture_sha256": _file_sha256(self.fixture_path),
            "account_task_snapshot_ref": str(task_snapshot["snapshot_ref"]),
            "account_task_source_hash": str(task_snapshot["source_hash"]),
            "occurrence_set_hash": stable_hash(sorted(expected_occurrences)),
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
            latest_rank = datetime.fromisoformat(latest.replace("Z", "+00:00")).timestamp()
        except ValueError:
            latest_rank = float("-inf")
        return (
            -int(statistics["comment_count"]),
            -int(statistics["commented_post_count"]),
            -int(statistics["commented_post_author_count"]),
            -latest_rank,
            str(item["display_name"]),
            str(item["internal_account_ref"]),
        )

    @staticmethod
    def _snapshot_occurrence_identities(
        snapshot: ImmutableReportSnapshot,
    ) -> set[tuple[str, str, str, str]]:
        identities: set[tuple[str, str, str, str]] = set()
        for post in snapshot.posts:
            payload = post.payload.get("raw_content_payload") or {}
            if not isinstance(payload, dict):
                raise ReportValidationError(
                    "build_report_account_entries",
                    ["a frozen Post lacks structured source content"],
                )
            platform = _platform(post.payload.get("platform"))
            author = payload.get("author") or {}
            if not isinstance(author, dict):
                author = {}
            post_account = _source_account_ref(platform, author)
            post_identity = ("post_author", post.canonical_key, "", post_account)
            if post_identity in identities:
                raise ReportValidationError(
                    "build_report_account_entries",
                    ["the Report Snapshot contains a duplicate Post occurrence"],
                )
            identities.add(post_identity)
            comments = payload.get("comments") or []
            if not isinstance(comments, list):
                raise ReportValidationError(
                    "build_report_account_entries",
                    ["a frozen Post has a non-list Comment projection"],
                )
            for comment in comments:
                if not isinstance(comment, dict):
                    raise ReportValidationError(
                        "build_report_account_entries",
                        ["a frozen Comment projection is not an object"],
                    )
                comment_id = normalize_source_id(comment.get("comment_id"))
                if not comment_id:
                    raise ReportValidationError(
                        "build_report_account_entries",
                        ["a frozen Comment is missing comment_id"],
                    )
                comment_account = _source_account_ref(platform, comment)
                identity = (
                    "comment_author",
                    post.canonical_key,
                    comment_id,
                    comment_account,
                )
                if identity in identities:
                    raise ReportValidationError(
                        "build_report_account_entries",
                        ["the Report Snapshot contains a duplicate Comment occurrence"],
                    )
                identities.add(identity)
        return identities
