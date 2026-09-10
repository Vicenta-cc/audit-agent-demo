"""Deterministic Account Activity queries over the frozen A2 corpus."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from hermes_m0.account_corpus import AccountCorpus
from hermes_m0.display_labels import (
    DECISION_LABELS,
    PLATFORM_LABELS,
    RISK_LEVEL_LABELS,
    enum_label,
)
from hermes_m0.domain import Post
from hermes_m0.real_report_repository import PublishedReportRepository


DEFAULT_ACCOUNT_CORPUS_PATH = (
    Path(__file__).resolve().parents[1] / "demo" / "seed" / "account_m22_corpus.json.gz"
)
RISK_LEVELS = frozenset({"low", "medium", "high"})
RISK_POST_DECISIONS = frozenset({"review", "reject"})


class AccountActivityLookupError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReportAccountEntry:
    account_id: str
    display_name: str
    published_post_count: int
    comment_count: int
    roles: tuple[str, ...] = ("post_author",)
    current_statistics: dict[str, Any] | None = None
    report_entry_ref: str = ""
    target_display_order: int | None = None
    active_comment_display_order: int | None = None
    default_active_comment_visible: bool = False


class AccountActivityRepository:
    """One validated view of all currently authorized Account data."""

    def __init__(self, corpus: AccountCorpus) -> None:
        self.corpus = corpus
        self.corpus_revision = corpus.corpus_revision
        self._post_occurrences = {
            (str(item["task_id"]), str(item["post"]["content_key"])): item
            for item in corpus.occurrences
            if item["kind"] == "post_author"
        }
        post_values = [
            item for item in corpus.occurrences if item["kind"] == "post_author"
        ]
        if len(self._post_occurrences) != len(post_values):
            raise ValueError("Account corpus contains duplicate Post occurrences")
        self._comment_occurrences = {
            (
                str(item["task_id"]),
                str(item["post"]["content_key"]),
                str(item["comment"]["comment_id"]),
            ): item
            for item in corpus.occurrences
            if item["kind"] == "comment_author"
        }
        comment_values = [
            item for item in corpus.occurrences if item["kind"] == "comment_author"
        ]
        if len(self._comment_occurrences) != len(comment_values):
            raise ValueError("Account corpus contains duplicate Comment occurrences")

    @classmethod
    def load(cls, path: Path | str = DEFAULT_ACCOUNT_CORPUS_PATH) -> "AccountActivityRepository":
        return cls(AccountCorpus.load(Path(path)))

    def report_account_entries(
        self, report_repository: PublishedReportRepository
    ) -> tuple[ReportAccountEntry, ...]:
        persisted = report_repository.report_account_entries()
        if persisted:
            visible = [
                item
                for item in persisted
                if item.target_display_order is not None
                or item.default_active_comment_visible
            ]
            visible.sort(
                key=lambda item: (
                    0 if item.target_display_order is not None else 1,
                    item.target_display_order
                    or item.active_comment_display_order
                    or 0,
                    item.display_name,
                    item.entry_ref,
                )
            )
            output = []
            for item in visible:
                self._require_account(item.account_id)
                statistics = dict(item.current_statistics)
                output.append(
                    ReportAccountEntry(
                        account_id=item.account_id,
                        display_name=item.display_name,
                        published_post_count=int(
                            statistics["published_post_count"]
                        ),
                        comment_count=int(statistics["comment_count"]),
                        roles=item.roles,
                        current_statistics=statistics,
                        report_entry_ref=item.entry_ref,
                        target_display_order=item.target_display_order,
                        active_comment_display_order=item.active_comment_display_order,
                        default_active_comment_visible=item.default_active_comment_visible,
                    )
                )
            return tuple(output)

        task_id = report_repository.fixture.provenance.source_task_id
        snapshot_post_keys = {
            _content_key(post_id) for post_id in report_repository.snapshot.post_ids
        }
        account_ids = {
            str(occurrence["account_ref"])
            for key in snapshot_post_keys
            if (occurrence := self._post_occurrences.get((task_id, key))) is not None
            and occurrence["account_ref"] is not None
        }
        entries = []
        for account_id in account_ids:
            task_occurrences = [
                item
                for item in self.corpus.occurrences_for(account_id)
                if item["task_id"] == task_id
            ]
            entries.append(
                ReportAccountEntry(
                    account_id=account_id,
                    display_name=self.display_name(account_id),
                    published_post_count=sum(
                        item["kind"] == "post_author"
                        and item["post"]["content_key"] in snapshot_post_keys
                        for item in task_occurrences
                    ),
                    comment_count=sum(
                        item["kind"] == "comment_author"
                        and item["post"]["content_key"] in snapshot_post_keys
                        for item in task_occurrences
                    ),
                    roles=("post_author",),
                    current_statistics=None,
                )
            )
        entries.sort(key=lambda item: (item.display_name, item.account_id))
        return tuple(entries)

    def overview(self, account_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        occurrences = list(self.corpus.occurrences_for(account_id))
        comments = [item for item in occurrences if item["kind"] == "comment_author"]
        source_task_ids = sorted({str(item["task_id"]) for item in occurrences})
        target_counts = Counter(
            str(item["parent_post_author_account_ref"])
            for item in comments
            if item["parent_post_author_account_ref"] is not None
        )
        ordered_targets = sorted(
            target_counts.items(),
            key=lambda pair: (-pair[1], self.display_name(pair[0]), pair[0]),
        )
        target_distribution = [
            {
                "position": position,
                "account_id": target_id,
                "author_display_name": self.display_name(target_id),
                "comment_count": count,
                "commented_post_count": len(
                    {
                        (str(item["task_id"]), str(item["post"]["content_key"]))
                        for item in comments
                        if str(item["parent_post_author_account_ref"]) == target_id
                    }
                ),
            }
            for position, (target_id, count) in enumerate(ordered_targets, 1)
        ]
        source_distribution = []
        for task_id in source_task_ids:
            task_occurrences = [
                item for item in occurrences if str(item["task_id"]) == task_id
            ]
            task_statistics = _overview_statistics(task_occurrences)
            source_distribution.append(
                {
                    "task_id": task_id,
                    "source_task": self.task_card(task_id),
                    **task_statistics,
                }
            )
        source_distribution.sort(
            key=lambda item: (
                -int(item["comment_count"]),
                -int(item["published_post_count"]),
                str(item["source_task"]["name"]),
                str(item["task_id"]),
            )
        )
        return {
            "display_name": self.display_name(account_id),
            "statistics": _overview_statistics(occurrences),
            "comment_target_distribution": target_distribution,
            "activity_source_distribution": source_distribution,
            "activity_source_tasks": [self.task_card(task_id) for task_id in source_task_ids],
            "activity_source_count": len(source_task_ids),
            "authorized_task_count": len(self.corpus.authorized_task_ids),
        }

    def comment_investigation_counts(
        self,
        account_ids: Iterable[str],
        *,
        required_task_id: str | None = None,
    ) -> dict[str, int]:
        """Count authorized investigations containing Comment activity per Account."""

        requested = {str(account_id) for account_id in account_ids if str(account_id)}
        task_ids_by_account: dict[str, set[str]] = defaultdict(set)
        for occurrence in self.corpus.occurrences:
            account_id = str(occurrence.get("account_ref") or "")
            if account_id in requested and occurrence.get("kind") == "comment_author":
                task_ids_by_account[account_id].add(str(occurrence["task_id"]))
        return {
            account_id: (
                len(task_ids_by_account.get(account_id, set()))
                if required_task_id is None
                or required_task_id in task_ids_by_account.get(account_id, set())
                else 0
            )
            for account_id in requested
        }

    def ordered_occurrences(
        self,
        account_id: str,
        *,
        kind: str,
        comment_target_account_id: str | None = None,
        risk_filter: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        self._require_account(account_id)
        if kind not in {"comment_author", "post_author"}:
            raise AccountActivityLookupError(
                "invalid_kind", "kind must be comment_author or post_author."
            )
        if comment_target_account_id is not None:
            self._require_account(comment_target_account_id)
            if kind != "comment_author":
                raise AccountActivityLookupError(
                    "invalid_comment_target_filter",
                    "A comment target filter can only be used with comment_author occurrences.",
                )
        if risk_filter not in {None, "risk_only"}:
            raise AccountActivityLookupError(
                "invalid_risk_filter", "risk_filter must be risk_only when provided."
            )
        values = [
            item
            for item in self.corpus.occurrences_for(account_id)
            if item["kind"] == kind
            and (
                comment_target_account_id is None
                or str(item["parent_post_author_account_ref"])
                == comment_target_account_id
            )
            and (
                risk_filter is None
                or (
                    str(item.get("audit_status") or "") == "completed"
                    and str(item.get("risk_level") or "")
                    in {"low", "medium", "high"}
                )
            )
        ]
        values.sort(
            key=lambda item: (
                str(item["occurred_at"] or ""),
                str(item["task_id"]),
                str(item["occurrence_ref"]),
            ),
            reverse=True,
        )
        return tuple(values)

    def post_occurrence(self, task_id: str, content_key: str) -> dict[str, Any]:
        try:
            return self._post_occurrences[(task_id, content_key)]
        except KeyError as exc:
            raise AccountActivityLookupError(
                "unknown_occurrence", "The Post occurrence is not available."
            ) from exc

    def comment_occurrence(
        self, task_id: str, content_key: str, comment_id: str
    ) -> dict[str, Any]:
        try:
            return self._comment_occurrences[(task_id, content_key, comment_id)]
        except KeyError as exc:
            raise AccountActivityLookupError(
                "unknown_occurrence", "The Comment occurrence is not available."
            ) from exc

    def account_post_detail(
        self,
        occurrence: dict[str, Any],
        *,
        report_repositories: Iterable[PublishedReportRepository],
    ) -> dict[str, Any] | None:
        if occurrence["kind"] != "comment_author":
            raise AccountActivityLookupError(
                "wrong_parent_ref",
                "Only a Comment occurrence can expose a Parent Post reference.",
            )
        task_id = str(occurrence["task_id"])
        post_key = str(occurrence["post"]["content_key"])
        matching = [
            item
            for item in report_repositories
            if item.fixture.provenance.source_task_id == task_id
        ]
        if len(matching) > 1:
            raise AccountActivityLookupError(
                "ambiguous_post_source",
                "Multiple authorized reports claim the same source investigation.",
            )
        if not matching:
            return None
        report_repository = matching[0]
        post_id = f"post:{post_key}"
        try:
            post = report_repository.post(post_id)
            content = report_repository.post_content(post_id)
        except RepositoryLookupError:
            return None
        post_occurrence = self._post_occurrences.get((task_id, post_key))
        if post_occurrence is None:
            raise AccountActivityLookupError(
                "missing_parent_post_occurrence",
                "The frozen Account corpus has no matching Parent Post occurrence.",
            )
        parent_author_id = str(occurrence["parent_post_author_account_ref"])
        if str(post_occurrence["account_ref"]) != parent_author_id:
            raise AccountActivityLookupError(
                "wrong_parent_ref",
                "The Comment and frozen Parent Post disagree on the Post author.",
            )
        return {
            "title": _post_title(post_occurrence["post"]),
            "display_title_source": _post_title_source(post_occurrence["post"]),
            "platform": post_occurrence["post"].get("platform"),
            "platform_label": enum_label(
                PLATFORM_LABELS, post_occurrence["post"].get("platform")
            ),
            "full_original_body": _full_post_body(content),
            "author_display_name": post.author.display_name,
            "published_at": post_occurrence["post"]["published_at"],
            "published_at_availability": (
                "available"
                if post_occurrence["post"]["published_at"] is not None
                else "unavailable"
            ),
            "source_task": self.task_card(task_id),
            "original_content_projection": content,
            "content_availability": "complete_frozen_post_payload",
        }

    def occurrence(self, account_id: str, occurrence_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        try:
            occurrence = self.corpus.occurrence(occurrence_id)
        except KeyError as exc:
            raise AccountActivityLookupError(
                "unknown_occurrence", "The Account occurrence is not available."
            ) from exc
        if occurrence["account_ref"] != account_id:
            raise AccountActivityLookupError(
                "wrong_parent_ref", "The occurrence does not belong to this Account."
            )
        return occurrence

    def occurrence_preview(
        self,
        occurrence: dict[str, Any],
        *,
        occurrence_ref: str,
        report_risk_post_keys: frozenset[str],
    ) -> dict[str, Any]:
        base = {
            "type": "account_activity_occurrence",
            "ref": occurrence_ref,
            "kind": occurrence["kind"],
            "platform": occurrence["post"].get("platform"),
            "platform_label": enum_label(
                PLATFORM_LABELS, occurrence["post"].get("platform")
            ),
            "activity_time": occurrence["occurred_at"],
            "source_task": self.task_card(str(occurrence["task_id"])),
            "preview_only": True,
        }
        post = occurrence["post"]
        if occurrence["kind"] == "comment_author":
            base.update(
                {
                    "role": "当前账号是评论者",
                    "comment_preview": _preview(str(occurrence["comment"]["text"]), 160),
                    "audit_status": occurrence.get("audit_status"),
                    "risk_level": occurrence.get("risk_level"),
                    "risk_level_label": enum_label(
                        RISK_LEVEL_LABELS, occurrence.get("risk_level")
                    ),
                    "risk_type": occurrence["comment"].get("risk_type"),
                    "parent_post": {
                        "title": _post_title(post),
                        "display_title_source": _post_title_source(post),
                        "author_display_name": self.display_name(
                            str(occurrence["parent_post_author_account_ref"])
                        ),
                    },
                    "parent_post_in_current_report_risk_summary": (
                        str(post["content_key"]) in report_risk_post_keys
                    ),
                }
            )
        else:
            base.update(
                {
                    "role": "当前账号是帖子作者",
                    "post_preview": {
                        "title": _post_title(post),
                        "display_title_source": _post_title_source(post),
                    },
                    "audit_status": occurrence.get("audit_status"),
                    "decision": occurrence.get("decision"),
                    "decision_label": enum_label(
                        DECISION_LABELS, occurrence.get("decision")
                    ),
                    "risk_level": occurrence.get("risk_level"),
                    "risk_level_label": enum_label(
                        RISK_LEVEL_LABELS, occurrence.get("risk_level")
                    ),
                    "post_in_current_report_risk_summary": (
                        str(post["content_key"]) in report_risk_post_keys
                    ),
                }
            )
        return base

    def occurrence_detail(
        self,
        occurrence: dict[str, Any],
        *,
        occurrence_ref: str,
        report_risk_post_keys: frozenset[str],
        report_post: Post | None,
        parent_post_ref: str | None = None,
    ) -> dict[str, Any]:
        post = occurrence["post"]
        base = {
            "type": "account_activity_occurrence",
            "ref": occurrence_ref,
            "kind": occurrence["kind"],
            "platform": occurrence["post"].get("platform"),
            "platform_label": enum_label(
                PLATFORM_LABELS, occurrence["post"].get("platform")
            ),
            "activity_time": occurrence["occurred_at"],
            "source_task": self.task_card(str(occurrence["task_id"])),
            "detail_loaded": True,
            "historical_audit_availability": (
                "Account Activity 当前只提供活动记录、原始 Comment/Post、父帖、帖子作者、"
                "时间和来源任务；不提供历史 AuditFinding 或 Evidence。"
            ),
        }
        if occurrence["kind"] == "comment_author":
            base.update(
                {
                    "role": "当前账号是评论者；不是父帖作者",
                    "comment": {
                        "full_original_text": str(occurrence["comment"]["text"]),
                        "published_at": occurrence["comment"]["comment_time"],
                        "audit_status": occurrence.get("audit_status"),
                        "risk_level": occurrence.get("risk_level"),
                        "risk_level_label": enum_label(
                            RISK_LEVEL_LABELS, occurrence.get("risk_level")
                        ),
                        "risk_type": occurrence["comment"].get("risk_type"),
                    },
                    "parent_post": {
                        "title": _post_title(post),
                        "display_title_source": _post_title_source(post),
                        "author_display_name": self.display_name(
                            str(occurrence["parent_post_author_account_ref"])
                        ),
                        "ref": parent_post_ref,
                        "content_availability": "detail_available_via_read_account_post",
                        "preview_only": True,
                        "in_current_report_risk_summary": (
                            str(post["content_key"]) in report_risk_post_keys
                        ),
                    },
                }
            )
        else:
            base.update(
                {
                    "role": "当前账号是帖子作者；该记录没有 Parent Post",
                    "post": {
                        "title": _post_title(post),
                        "display_title_source": _post_title_source(post),
                        "published_at": post["published_at"],
                        "published_at_availability": (
                            "available"
                            if post["published_at"] is not None
                            else "unavailable"
                        ),
                        "audit_status": occurrence.get("audit_status"),
                        "decision": occurrence.get("decision"),
                        "decision_label": enum_label(
                            DECISION_LABELS, occurrence.get("decision")
                        ),
                        "risk_level": occurrence.get("risk_level"),
                        "risk_level_label": enum_label(
                            RISK_LEVEL_LABELS, occurrence.get("risk_level")
                        ),
                        "author_display_name": self.display_name(
                            str(occurrence["account_ref"])
                        ),
                        "full_original_body": (
                            report_post.body if report_post is not None else None
                        ),
                        "content_availability": (
                            "complete_frozen_post_body"
                            if report_post is not None
                            else "stored_title_author_and_time_only"
                        ),
                        "in_current_report_risk_summary": (
                            str(post["content_key"]) in report_risk_post_keys
                        ),
                    },
                }
            )
        return base

    def display_name(self, account_id: str) -> str:
        self._require_account(account_id)
        aliases = self.corpus.aliases_for(account_id)
        if not aliases:
            return "未记录昵称的账号"
        return str(aliases[-1]["nickname"] or "未记录昵称的账号")

    def task_card(self, task_id: str) -> dict[str, str]:
        try:
            snapshot = self.corpus.snapshot_for_task(task_id)
        except KeyError as exc:
            raise AccountActivityLookupError(
                "cross_scope", "The source task is outside the authorized Account data."
            ) from exc
        return {
            "type": "investigation_task",
            "name": str(snapshot["task_display_name"]),
        }

    def ordered_occurrence_hash(self, values: tuple[dict[str, Any], ...]) -> str:
        encoded = json.dumps(
            [str(item["occurrence_ref"]) for item in values],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _require_account(self, account_id: str) -> None:
        try:
            self.corpus.account(account_id)
        except KeyError as exc:
            raise AccountActivityLookupError(
                "unknown_account", "The Account is outside the authorized Account data."
            ) from exc


def _content_key(post_id: str) -> str:
    return post_id.split(":", 1)[-1]


def _post_title(post: dict[str, Any]) -> str:
    return str(post.get("display_title") or "标题不可用")


def _post_title_source(post: dict[str, Any]) -> str:
    return str(post.get("display_title_source") or "unavailable")


def _activity_bounds(values: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    timestamps = [
        str(item["occurred_at"])
        for item in values
        if item.get("occurred_at") not in (None, "")
    ]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _overview_statistics(values: Iterable[dict[str, Any]]) -> dict[str, Any]:
    occurrences = list(values)
    comments = [item for item in occurrences if item["kind"] == "comment_author"]
    posts = [item for item in occurrences if item["kind"] == "post_author"]
    earliest, latest = _activity_bounds(occurrences)
    _, latest_comment = _activity_bounds(comments)
    _, latest_published = _activity_bounds(posts)
    return {
        "comment_count": len(comments),
        # A Comment is risky only when its own frozen risk_level is risky.
        "risk_comment_count": sum(
            str(item.get("risk_level") or "").lower() in RISK_LEVELS
            for item in comments
        ),
        "commented_post_count": len(
            {
                (str(item["task_id"]), str(item["post"]["content_key"]))
                for item in comments
            }
        ),
        "commented_post_author_count": len(
            {
                str(item["parent_post_author_account_ref"])
                for item in comments
                if item.get("parent_post_author_account_ref")
            }
        ),
        "earliest_activity_at": earliest,
        "latest_activity_at": latest,
        "latest_comment_at": latest_comment,
        "latest_published_at": latest_published,
        "published_post_count": len(posts),
        "risk_published_post_count": sum(
            str(item.get("decision") or "").lower() in RISK_POST_DECISIONS
            or str(item.get("risk_level") or "").lower() in RISK_LEVELS
            for item in posts
        ),
    }


def _preview(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _full_post_body(content: dict[str, Any]) -> str:
    parts = []
    caption = str(content.get("author_caption") or "")
    if caption:
        parts.append(caption)
    for video in content.get("videos") or []:
        transcript = video.get("speech_transcript") or {}
        text = str(transcript.get("text") or "")
        if text:
            parts.append(text)
    return "\n\n".join(parts)
