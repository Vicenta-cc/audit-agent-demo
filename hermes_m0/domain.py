"""Immutable domain objects required by the Hermes M0 tools."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Decision = Literal["pass", "review", "reject"]
RiskLevel = Literal["none", "low", "medium", "high"]
EvidenceType = Literal["text", "comment", "asr", "ocr", "keyframe"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FixtureProvenance(FrozenModel):
    source_workspace_head: str
    source_task_id: str
    source_scope_ref: str
    projection_contract: str
    projection_snapshot_id: str
    projection_manifest_hash: str
    projection_counts: dict[str, int]


class FrozenSnapshot(FrozenModel):
    id: str
    captured_through: datetime
    post_ids: tuple[str, ...]
    post_revision_ids: tuple[str, ...]
    finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class ReportStatistics(FrozenModel):
    case_count: int = Field(ge=0)
    post_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    decision_counts: dict[str, int]
    risk_counts: dict[str, int]
    evidence_type_counts: dict[str, int]


class ReportVersion(FrozenModel):
    id: str
    report_id: str
    snapshot_id: str
    revision: str
    public_revision: str
    title: str
    overview: str
    published_at: datetime
    case_ids: tuple[str, ...]
    statistics: ReportStatistics


class ReportCase(FrozenModel):
    id: str
    position: int = Field(ge=1)
    key: Decision
    title: str
    summary: str
    member_post_ids: tuple[str, ...]


class AuthorDisplay(FrozenModel):
    display_name: str
    avatar_url: str
    platform: str
    account_id: str


class PostSource(FrozenModel):
    platform: str
    url: str
    published_at: str
    captured_at: datetime | None
    analyzed_at: datetime | None = None


class Post(FrozenModel):
    id: str
    revision_id: str
    title: str
    body: str
    author: AuthorDisplay
    source: PostSource
    finding_id: str


class Finding(FrozenModel):
    id: str
    post_id: str
    post_revision_id: str
    decision: Decision
    risk_level: RiskLevel
    categories: tuple[str, ...]
    summary: str
    completed_at: datetime


class EvidenceContent(FrozenModel):
    original_text: str
    translated_text: str
    summary: str
    timestamp_start: float | None
    timestamp_end: float | None
    asset_path: str


class EvidenceSource(FrozenModel):
    locator: str
    availability: str
    captured_at: datetime


class Evidence(FrozenModel):
    id: str
    finding_id: str
    parent_post_id: str
    parent_revision_id: str
    type: EvidenceType
    ordinal: int = Field(ge=1)
    content: EvidenceContent
    source: EvidenceSource


class InvestigationFixture(FrozenModel):
    schema_version: Literal["hermes-investigation-fixture/v1"]
    provenance: FixtureProvenance
    snapshot: FrozenSnapshot
    report_version: ReportVersion
    cases: tuple[ReportCase, ...]
    posts: tuple[Post, ...]
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> "InvestigationFixture":
        _require_unique("case", (item.id for item in self.cases))
        _require_unique("post", (item.id for item in self.posts))
        _require_unique("post revision", (item.revision_id for item in self.posts))
        _require_unique("finding", (item.id for item in self.findings))
        _require_unique("evidence", (item.id for item in self.evidence))

        cases = {item.id: item for item in self.cases}
        posts = {item.id: item for item in self.posts}
        findings = {item.id: item for item in self.findings}

        if self.report_version.snapshot_id != self.snapshot.id:
            raise ValueError("ReportVersion belongs to another FrozenSnapshot")
        ordered_cases = tuple(item.id for item in sorted(self.cases, key=lambda x: x.position))
        if self.report_version.case_ids != ordered_cases:
            raise ValueError("ReportVersion case order does not match ReportCase positions")
        if set(self.snapshot.post_ids) != set(posts):
            raise ValueError("FrozenSnapshot post membership does not match fixture posts")
        if set(self.snapshot.post_revision_ids) != {item.revision_id for item in self.posts}:
            raise ValueError("FrozenSnapshot revision membership does not match fixture posts")
        if set(self.snapshot.finding_ids) != set(findings):
            raise ValueError("FrozenSnapshot finding membership does not match fixture findings")
        if set(self.snapshot.evidence_ids) != {item.id for item in self.evidence}:
            raise ValueError("FrozenSnapshot Evidence membership does not match fixture Evidence")

        reported_posts: list[str] = []
        for case in self.cases:
            for post_id in case.member_post_ids:
                if post_id not in posts:
                    raise ValueError(f"ReportCase contains unknown post: {post_id}")
                reported_posts.append(post_id)
        if len(reported_posts) != len(set(reported_posts)):
            raise ValueError("A post belongs to more than one ReportCase")

        for post in self.posts:
            finding = findings.get(post.finding_id)
            if finding is None:
                raise ValueError(f"Post has unknown Finding: {post.id}")
            if finding.post_id != post.id or finding.post_revision_id != post.revision_id:
                raise ValueError(f"Post/Finding ownership mismatch: {post.id}")

        evidence_ordinals: dict[str, list[int]] = {}
        for item in self.evidence:
            finding = findings.get(item.finding_id)
            post = posts.get(item.parent_post_id)
            if finding is None or post is None:
                raise ValueError(f"Evidence has unknown owner: {item.id}")
            if finding.post_id != post.id or post.revision_id != item.parent_revision_id:
                raise ValueError(f"Evidence parent mismatch: {item.id}")
            evidence_ordinals.setdefault(post.id, []).append(item.ordinal)
        for post_id, ordinals in evidence_ordinals.items():
            if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
                raise ValueError(f"Evidence order is not contiguous for post: {post_id}")

        expected = ReportStatistics(
            case_count=len(cases),
            post_count=len(posts),
            finding_count=len(findings),
            evidence_count=len(self.evidence),
            decision_counts=dict(sorted(Counter(x.decision for x in self.findings).items())),
            risk_counts=dict(sorted(Counter(x.risk_level for x in self.findings).items())),
            evidence_type_counts=dict(
                sorted(Counter(x.type for x in self.evidence).items())
            ),
        )
        if self.report_version.statistics != expected:
            raise ValueError("ReportVersion statistics do not match the frozen graph")
        if self.provenance.projection_snapshot_id != self.snapshot.id:
            raise ValueError("Fixture provenance points to another FrozenSnapshot")
        if self.provenance.projection_counts != {
            "posts": len(posts),
            "findings": len(findings),
            "evidence": len(self.evidence),
        }:
            raise ValueError("Fixture provenance counts do not match the frozen graph")
        return self


def _require_unique(label: str, values: Iterable[str]) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"Duplicate {label} identity")
