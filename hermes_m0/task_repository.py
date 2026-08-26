"""Read-only indexes over the frozen cross-dataset task fixture."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
import unicodedata

from hermes_m0.task_domain import (
    CrossDatasetFixture,
    TaskEvidence,
    TaskPost,
    TaskSnapshot,
)


DEFAULT_TASK_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "cross_dataset_r2_20260820.json"
)


class TaskRepositoryLookupError(LookupError):
    pass


@dataclass(frozen=True)
class TaskPostSearchMatch:
    post: TaskPost
    preview_source: str
    preview_text: str


class TaskRepository:
    def __init__(self, snapshot: TaskSnapshot) -> None:
        self.snapshot = snapshot
        self._posts: Mapping[str, TaskPost] = MappingProxyType(
            {item.id: item for item in snapshot.posts}
        )
        evidence = {item.id: item for post in snapshot.posts for item in post.evidence}
        self._evidence: Mapping[str, TaskEvidence] = MappingProxyType(evidence)
        grouped: dict[str, list[TaskEvidence]] = defaultdict(list)
        for item in evidence.values():
            grouped[item.parent_post_id].append(item)
        self._evidence_by_post: Mapping[str, tuple[TaskEvidence, ...]] = (
            MappingProxyType(
                {
                    post_id: tuple(sorted(items, key=lambda item: item.ordinal))
                    for post_id, items in grouped.items()
                }
            )
        )

    @classmethod
    def load_all(
        cls, path: Path | str = DEFAULT_TASK_FIXTURE_PATH
    ) -> dict[str, "TaskRepository"]:
        fixture = CrossDatasetFixture.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
        return {item.task_id: cls(item) for item in fixture.tasks}

    def ordered_posts(self) -> tuple[TaskPost, ...]:
        return self.snapshot.posts

    def search_posts(
        self,
        query_text: str,
        *,
        risk_level: str | None = None,
        decision: str | None = None,
    ) -> tuple[TaskPostSearchMatch, ...]:
        """Return lexical matches in frozen snapshot order without Finding projection."""
        normalized_query = normalize_search_text(query_text)
        terms = tuple(normalized_query.split())
        matches: list[TaskPostSearchMatch] = []
        for post in self.snapshot.posts:
            if risk_level is not None and post.finding.risk_level != risk_level:
                continue
            if decision is not None and post.finding.decision != decision:
                continue
            fields = _searchable_fields(post)
            normalized_fields = tuple(
                (label, text, normalize_search_text(text)) for label, text in fields
            )
            combined = " ".join(item[2] for item in normalized_fields)
            if not terms or not all(term in combined for term in terms):
                continue
            source = next(
                (
                    (label, text)
                    for label, text, normalized in normalized_fields
                    if normalized_query in normalized
                ),
                None,
            )
            if source is None:
                source = next(
                    (
                        (label, text)
                        for label, text, normalized in normalized_fields
                        if all(term in normalized for term in terms)
                    ),
                    None,
                )
            if source is None:
                source = next(
                    (label, text)
                    for label, text, normalized in normalized_fields
                    if any(term in normalized for term in terms)
                )
            matches.append(
                TaskPostSearchMatch(
                    post=post,
                    preview_source=source[0],
                    preview_text=source[1],
                )
            )
        return tuple(matches)

    def post(self, post_id: str) -> TaskPost:
        return self._get(self._posts, post_id, "Post")

    def evidence_for_post(self, post_id: str) -> tuple[TaskEvidence, ...]:
        self.post(post_id)
        return self._evidence_by_post.get(post_id, ())

    def evidence(self, evidence_id: str) -> TaskEvidence:
        return self._get(self._evidence, evidence_id, "Evidence")

    @staticmethod
    def _get(index: Mapping[str, object], object_id: str, label: str):
        try:
            return index[object_id]
        except KeyError as exc:
            raise TaskRepositoryLookupError(f"Unknown {label}") from exc


def normalize_search_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _searchable_fields(post: TaskPost) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = [("帖子标题", post.name)]
    if post.author_caption:
        fields.append(("博主配文", post.author_caption))
    for video in post.videos:
        if video.translation_translated and video.existing_translation_text:
            fields.append(
                (
                    f"视频{video.ordinal}已有中文语音转写",
                    video.existing_translation_text,
                )
            )
        elif video.original_text:
            fields.append((f"视频{video.ordinal}原语言语音转写", video.original_text))
    fields.append(("作者显示名", post.author.display_name))
    return tuple(fields)
