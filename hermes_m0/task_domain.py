"""Immutable task-snapshot objects for the cross-dataset M0 diagnostic."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hermes_m0.domain import Decision, RiskLevel


class FrozenTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskAuthor(FrozenTaskModel):
    display_name: str
    avatar_url: str
    platform: str


class TaskFinding(FrozenTaskModel):
    decision: Decision
    risk_level: RiskLevel
    categories: tuple[str, ...]
    summary: str
    risk_basis: str
    completed_at: datetime


class TaskEvidence(FrozenTaskModel):
    id: str
    parent_post_id: str
    parent_revision_id: str
    ordinal: int = Field(ge=1)
    evidence_type: Literal["comment"]
    original_text: str
    translated_text: str
    risk_type: str
    audit_reason: str
    severity: str
    author_display_name: str
    source_label: str


class TaskVideoTranscript(FrozenTaskModel):
    ordinal: int = Field(ge=1)
    source_index: int | None
    original_text: str
    existing_translation_text: str
    translation_translated: bool


class TaskPost(FrozenTaskModel):
    id: str
    revision_id: str
    source_content_key: str
    name: str
    author_caption: str
    videos: tuple[TaskVideoTranscript, ...]
    author: TaskAuthor
    source_url: str
    finding: TaskFinding
    evidence: tuple[TaskEvidence, ...]


class TaskSnapshot(FrozenTaskModel):
    id: str
    revision: str
    task_id: str
    task_name: str
    job_status: Literal["interrupted"]
    frozen_through: datetime
    projection_post_count: int
    projection_comment_count: int
    counted_risk_evidence: int
    readable_risk_evidence: int
    coverage_statement: str
    posts: tuple[TaskPost, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> "TaskSnapshot":
        if len(self.posts) != self.projection_post_count:
            raise ValueError("TaskSnapshot post count does not match projection")
        post_ids = [post.id for post in self.posts]
        revisions = [post.revision_id for post in self.posts]
        evidence_ids = [item.id for post in self.posts for item in post.evidence]
        if len(post_ids) != len(set(post_ids)):
            raise ValueError("Duplicate task post identity")
        if len(revisions) != len(set(revisions)):
            raise ValueError("Duplicate task post revision")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Duplicate task Evidence identity")
        if len(evidence_ids) != self.readable_risk_evidence:
            raise ValueError("Readable risk Evidence count does not match projection")
        for post in self.posts:
            if [video.ordinal for video in post.videos] != list(
                range(1, len(post.videos) + 1)
            ):
                raise ValueError("Task video order is not contiguous")
            for expected, item in enumerate(post.evidence, 1):
                if item.ordinal != expected:
                    raise ValueError("Task Evidence order is not contiguous")
                if item.parent_post_id != post.id or item.parent_revision_id != post.revision_id:
                    raise ValueError("Task Evidence parent mismatch")
        return self


class CrossDatasetFixture(FrozenTaskModel):
    schema_version: Literal["hermes-investigation-task-fixture/v2"]
    source_database_sha256: str
    source_access_contract: Literal["sqlite-mode=ro;immutable=1;query_only=ON"]
    generated_at: datetime
    tasks: tuple[TaskSnapshot, ...]
