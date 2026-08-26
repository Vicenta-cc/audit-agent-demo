"""Read-only repository over the frozen M0 report fixture."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from hermes_m0.domain import Evidence, Finding, InvestigationFixture, Post, ReportCase


DEFAULT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "report_2272c3692807.json"


class RepositoryLookupError(LookupError):
    pass


class InvestigationRepository:
    """An immutable in-memory index with no write API."""

    def __init__(self, fixture: InvestigationFixture) -> None:
        self.fixture = fixture
        self._cases: Mapping[str, ReportCase] = MappingProxyType(
            {item.id: item for item in fixture.cases}
        )
        self._posts: Mapping[str, Post] = MappingProxyType(
            {item.id: item for item in fixture.posts}
        )
        self._findings: Mapping[str, Finding] = MappingProxyType(
            {item.id: item for item in fixture.findings}
        )
        self._evidence: Mapping[str, Evidence] = MappingProxyType(
            {item.id: item for item in fixture.evidence}
        )
        self._case_by_post: Mapping[str, str] = MappingProxyType(
            {
                post_id: case.id
                for case in fixture.cases
                for post_id in case.member_post_ids
            }
        )
        grouped: dict[str, list[Evidence]] = defaultdict(list)
        for item in fixture.evidence:
            grouped[item.parent_post_id].append(item)
        self._evidence_by_post: Mapping[str, tuple[Evidence, ...]] = MappingProxyType(
            {
                post_id: tuple(sorted(items, key=lambda item: (item.ordinal, item.id)))
                for post_id, items in grouped.items()
            }
        )

    @classmethod
    def load(cls, path: Path | str = DEFAULT_FIXTURE_PATH) -> "InvestigationRepository":
        payload = Path(path).read_text(encoding="utf-8")
        return cls(InvestigationFixture.model_validate_json(payload))

    @property
    def report(self):
        return self.fixture.report_version

    @property
    def snapshot(self):
        return self.fixture.snapshot

    def ordered_cases(self) -> tuple[ReportCase, ...]:
        return tuple(self._cases[item_id] for item_id in self.report.case_ids)

    def ordered_posts(self) -> tuple[Post, ...]:
        return tuple(self._posts[item_id] for item_id in self.snapshot.post_ids)

    def risk_posts(self) -> tuple[Post, ...]:
        """Return deterministic risk posts from existing frozen Finding fields."""
        return tuple(
            post
            for post in self.ordered_posts()
            if is_deterministic_risk_finding(self.finding_for_post(post.id))
        )

    def case(self, case_id: str) -> ReportCase:
        return self._get(self._cases, case_id, "ReportCase")

    def case_posts(self, case_id: str) -> tuple[Post, ...]:
        case = self.case(case_id)
        return tuple(self.post(item_id) for item_id in case.member_post_ids)

    def post(self, post_id: str) -> Post:
        return self._get(self._posts, post_id, "Post")

    def case_for_post(self, post_id: str) -> ReportCase:
        self.post(post_id)
        try:
            return self.case(self._case_by_post[post_id])
        except KeyError as exc:
            raise RepositoryLookupError("Post is not a displayed report member") from exc

    def finding_for_post(self, post_id: str) -> Finding:
        post = self.post(post_id)
        return self._get(self._findings, post.finding_id, "Finding")

    def evidence_for_post(self, post_id: str) -> tuple[Evidence, ...]:
        self.post(post_id)
        return self._evidence_by_post.get(post_id, ())

    def evidence(self, evidence_id: str) -> Evidence:
        return self._get(self._evidence, evidence_id, "Evidence")

    @staticmethod
    def _get(index: Mapping[str, object], object_id: str, label: str):
        try:
            return index[object_id]
        except KeyError as exc:
            raise RepositoryLookupError(f"Unknown {label}") from exc


def is_deterministic_risk_finding(finding: Finding) -> bool:
    return finding.decision in {"review", "reject"} and finding.risk_level in {
        "low",
        "medium",
        "high",
    }
