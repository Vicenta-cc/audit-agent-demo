"""Verified, public stage labels for a provider-blocked, ungraded audit."""

BLOCKED_STAGE_LABELS = {
    "fusion_audit": "整帖汇总审核",
    "image_audit": "图片审核",
    "video_segment_audit": "视频片段审核",
    "ocr_extraction": "图像文字识别",
    "asr_transcription": "语音转写",
    "asr_translation": "语音翻译",
    "post_translation": "作品翻译",
}


def blocked_review_fields(result: dict) -> dict:
    if not (
        result.get("decision") == "review"
        and result.get("risk_level") == "unknown"
        and result.get("risk_score") is None
        and result.get("audit_completion_status") == "partial"
        and result.get("manual_review_required") is True
        and isinstance(result.get("audit_gaps"), list)
    ):
        return {}
    stages = []
    for gap in result["audit_gaps"]:
        if not isinstance(gap, dict) or not (
            gap.get("status") == "unreviewed"
            and gap.get("manual_review_required") is True
            and gap.get("error_code") == "audit_content_blocked"
        ):
            continue
        stage = gap.get("stage")
        if isinstance(stage, str) and stage in BLOCKED_STAGE_LABELS:
            label = BLOCKED_STAGE_LABELS[stage]
            if label not in stages:
                stages.append(label)
    return {"pending_manual_review": True, "audit_gap_stages": stages} if stages else {}


def is_pending_report_review(finding: dict) -> bool:
    """Recognize the marker emitted from persisted evidence by blocked_review_fields."""
    return (
        finding.get("decision") == "review"
        and finding.get("risk_level") == "unknown"
        and finding.get("pending_manual_review") is True
        and bool(finding.get("audit_gap_stages"))
    )
