"""Strict, local-alias contracts for the R2 InvestigationFinding pipeline.

The aliases in these models are deliberately session/run local.  They are
resolved to frozen Snapshot references only on the server side and are never
rendered into the human report.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from backend.reporting.contracts import ClaimType


class RiskEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_alias: str
    evidence_type: str
    original_text: str = ""
    translated_text: str = ""
    summary: str = ""
    reason: str = ""
    source_description: str = ""


class RiskPostInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    post_alias: str
    audit_finding_alias: str
    title: str = ""
    caption: str = ""
    author_display_name: str = ""
    platform: str = ""
    source_content: dict[str, Any] = Field(default_factory=dict)
    audit_finding: dict[str, Any]
    direct_evidence: tuple[RiskEvidenceInput, ...] = ()


class InvestigationFindingMembershipDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    post_ref: str
    audit_finding_ref: str
    is_representative: bool = False
    membership_evidence_refs: tuple[str, ...] = ()


class StandaloneRiskPostDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    post_ref: str
    audit_finding_ref: str
    disposition_note: str = Field(min_length=1)


class InvestigationFindingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_alias: str
    title: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    boundary_notes: tuple[str, ...] = ()
    metric_refs: tuple[str, ...] = ()
    post_memberships: tuple[InvestigationFindingMembershipDraft, ...] = Field(min_length=1)


class InvestigationFindingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    investigation_findings: tuple[InvestigationFindingDraft, ...] = Field(min_length=1, max_length=13)
    standalone_risk_posts: tuple[StandaloneRiskPostDraft, ...] = ()


class RiskClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_type: ClaimType
    text: str = Field(min_length=1)
    investigation_finding_refs: tuple[str, ...] = ()
    audit_finding_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    metric_refs: tuple[str, ...] = ()
    support_type: str = "direct"


class RiskParagraphDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = Field(min_length=1)


class RiskSectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str
    title: str
    paragraphs: tuple[RiskParagraphDraft, ...] = Field(min_length=1)
    claims: tuple[RiskClaimDraft, ...] = Field(min_length=1)
    case_blocks: tuple[dict[str, Any], ...] = ()


def make_investigation_plan_model(
    *,
    post_aliases: tuple[str, ...],
    finding_aliases: tuple[str, ...],
    audit_finding_aliases: tuple[str, ...],
    evidence_aliases: tuple[str, ...],
    metric_aliases: tuple[str, ...],
) -> type[BaseModel]:
    """Build a provider schema whose every reference is a finite local enum."""

    post_enum = Literal[post_aliases]  # type: ignore[valid-type]
    finding_enum = Literal[finding_aliases]  # type: ignore[valid-type]
    audit_finding_enum = Literal[audit_finding_aliases]  # type: ignore[valid-type]
    evidence_enum = Literal[evidence_aliases]  # type: ignore[valid-type]
    metric_enum = Literal[metric_aliases] if metric_aliases else None
    membership = create_model(
        "InvestigationFindingMembershipR22",
        __config__=ConfigDict(extra="forbid", frozen=True),
        post_ref=(post_enum, ...),
        audit_finding_ref=(audit_finding_enum, ...),
        is_representative=(bool, False),
        membership_evidence_refs=(tuple[evidence_enum, ...], ()),
    )
    standalone = create_model(
        "StandaloneRiskPostR22",
        __config__=ConfigDict(extra="forbid", frozen=True),
        post_ref=(post_enum, ...),
        audit_finding_ref=(audit_finding_enum, ...),
        disposition_note=(str, Field(min_length=1)),
    )
    draft = create_model(
        "InvestigationFindingR22",
        __config__=ConfigDict(extra="forbid", frozen=True),
        finding_alias=(finding_enum, ...),
        title=(str, Field(min_length=1)),
        statement=(str, Field(min_length=1)),
        boundary_notes=(tuple[str, ...], ()),
        post_memberships=(tuple[membership, ...], Field(min_length=1)),
        metric_refs=(tuple[metric_enum, ...] if metric_enum else tuple[()], ()),
    )
    plan = create_model(
        "ReportFindingPlanR22",
        __config__=ConfigDict(extra="forbid", frozen=True),
        investigation_findings=(tuple[draft, ...], Field(min_length=1, max_length=13)),
        standalone_risk_posts=(tuple[standalone, ...], ()),
    )
    plan.__name__ = "ReportFindingPlan"
    return plan


def make_risk_section_model(
    *,
    section_id: str,
    title: str,
    finding_aliases: tuple[str, ...],
    evidence_aliases: tuple[str, ...],
    audit_finding_aliases: tuple[str, ...] = (),
    metric_aliases: tuple[str, ...] = (),
) -> type[BaseModel]:
    """Build a chapter schema constrained to the current finding's aliases."""

    finding_enum = Literal[finding_aliases]  # type: ignore[valid-type]
    audit_finding_enum = Literal[audit_finding_aliases] if audit_finding_aliases else None
    # An empty allow-list must remain an empty tuple contract, rather than
    # silently widening to ``str`` and permitting an arbitrary reference.
    evidence_enum = Literal[evidence_aliases] if evidence_aliases else None
    metric_enum = Literal[metric_aliases] if metric_aliases else None
    claim = create_model(
        "RiskClaimDraftR21",
        __config__=ConfigDict(extra="forbid", frozen=True),
        claim_id=(str, ...),
        claim_type=(ClaimType, ...),
        text=(str, Field(min_length=1)),
        investigation_finding_refs=(tuple[finding_enum, ...], ()),
        audit_finding_refs=(tuple[audit_finding_enum, ...] if audit_finding_enum else tuple[()], ()),
        evidence_refs=(tuple[evidence_enum, ...] if evidence_enum else tuple[()], ()),
        metric_refs=(tuple[metric_enum, ...] if metric_enum else tuple[()], ()),
        support_type=(str, "direct"),
    )
    paragraph = create_model(
        "RiskParagraphDraftR21",
        __config__=ConfigDict(extra="forbid", frozen=True),
        text=(str, Field(min_length=1)),
        claim_ids=(tuple[str, ...], Field(min_length=1)),
    )
    section = create_model(
        "RiskSectionDraftR21",
        __config__=ConfigDict(extra="forbid", frozen=True),
        section_id=(Literal[section_id], ...),  # type: ignore[valid-type]
        title=(Literal[title], ...),  # type: ignore[valid-type]
        paragraphs=(tuple[paragraph, ...], Field(min_length=1)),
        claims=(tuple[claim, ...], Field(min_length=1)),
        case_blocks=(tuple[dict[str, Any], ...], ()),
    )
    section.__name__ = "RiskSectionDraft"
    return section


def make_synthesis_conclusion_model(
    *,
    audit_finding_aliases: tuple[str, ...],
    evidence_aliases: tuple[str, ...],
    metric_aliases: tuple[str, ...],
) -> type[BaseModel]:
    """Build the bounded paragraph-only model step for the final chapters.

    The model supplies prose and local source aliases only.  The server creates
    one ReportClaim per paragraph after validating those aliases, so the model
    cannot duplicate paragraph text in a second, independently validated Claim
    list.
    """

    audit_enum = Literal[audit_finding_aliases] if audit_finding_aliases else None
    evidence_enum = Literal[evidence_aliases] if evidence_aliases else None
    metric_enum = Literal[metric_aliases] if metric_aliases else None
    paragraph = create_model(
        "SourcedParagraphDraftR22R1",
        __config__=ConfigDict(extra="forbid", frozen=True),
        text=(str, Field(min_length=1)),
        audit_finding_refs=(tuple[audit_enum, ...] if audit_enum else tuple[()], ()),
        evidence_refs=(tuple[evidence_enum, ...] if evidence_enum else tuple[()], ()),
        metric_refs=(tuple[metric_enum, ...] if metric_enum else tuple[()], ()),
    )
    result = create_model(
        "SynthesisConclusionDraft",
        __config__=ConfigDict(extra="forbid", frozen=True),
        synthesis_paragraphs=(tuple[paragraph, ...], Field(min_length=1, max_length=3)),
        conclusion_paragraphs=(tuple[paragraph, ...], Field(min_length=1, max_length=2)),
    )
    result.__name__ = "SynthesisConclusionDraft"
    return result


def public_risk_post_input(
    *,
    post_alias: str,
    audit_finding_alias: str,
    post: dict[str, Any],
    finding: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> RiskPostInput:
    """Project a frozen post without canonical IDs or raw database fields."""

    raw_content = post.get("source_content") or {}
    # Keep complete human-readable post material while excluding frame bytes,
    # local paths, timing diagnostics and other non-content payloads.
    videos = []
    for video in raw_content.get("video_results") or []:
        transcript = video.get("transcript") if isinstance(video, dict) else {}
        translation = transcript.get("translation") if isinstance(transcript, dict) else {}
        videos.append(
            {
                "index": video.get("index") if isinstance(video, dict) else None,
                "transcript": {
                    "text": str((transcript or {}).get("text") or ""),
                    "text_zh": str((transcript or {}).get("text_zh") or ""),
                    "translation": {
                        "translated": bool((translation or {}).get("translated")),
                        "text": str((translation or {}).get("text") or ""),
                    },
                },
            }
        )
    post_content = {
        "title": str(raw_content.get("title") or ""),
        "title_zh": str(raw_content.get("title_zh") or ""),
        "desc": str(raw_content.get("desc") or ""),
        "desc_zh": str(raw_content.get("desc_zh") or ""),
        "video_results": videos,
    }
    safe_finding = {
        key: finding.get(key)
        for key in (
            "decision",
            "risk_level",
            "categories",
            "summary",
            "risk_basis",
            "primary_risk",
            "completed_at",
        )
    }
    evidence_inputs = tuple(
        RiskEvidenceInput(
            evidence_alias=item["evidence_alias"],
            evidence_type=str(item.get("evidence_type") or ""),
            original_text=str(item.get("original_text") or ""),
            translated_text=str(item.get("translated_text") or ""),
            summary=str(item.get("summary") or ""),
            reason=str(item.get("reason") or item.get("summary") or ""),
            # JSON paths and local asset paths are server provenance, not
            # model-facing source descriptions.
            source_description=str(item.get("source_description") or {
                "comment": "评论区",
                "visual": "画面资料",
                "audio": "语音转写",
                "ocr": "画面文字",
            }.get(str(item.get("evidence_type") or ""), "已审核资料")),
        )
        for item in evidence
    )
    return RiskPostInput(
        post_alias=post_alias,
        audit_finding_alias=audit_finding_alias,
        title=str(post.get("title") or ""),
        caption=str(post.get("caption") or ""),
        author_display_name=str(
            (post.get("author_display") or {}).get("nickname")
            or post.get("author")
            or ""
        ),
        platform=str(post.get("platform") or ""),
        source_content=post_content,
        audit_finding=safe_finding,
        direct_evidence=evidence_inputs,
    )
