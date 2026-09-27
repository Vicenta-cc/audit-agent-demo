from __future__ import annotations

import json
import re


_INTERNAL_FIELD = re.compile(
    r"(?i)\b(?:"
    r"schema_version|draft_id|run_id|proposal_id|presentation_id|"
    r"assistant_message_id|receipt_id|tool_call_id|client_message_id|"
    r"idempotency_key|report_version_id|source_snapshot_id|"
    r"content_hash|runtime_content_hash|snapshot_hash|expected_revision|"
    r"expected_task_settings_revision|expected_ruleset_version|"
    r"expected_ruleset_content_hash|application_turn_id|runtime_turn_id"
    r")\b"
)
_INTERNAL_ASSIGNMENT = re.compile(
    r"(?i)[`\"']?(?:"
    r"schema_version|draft_id|run_id|proposal_id|presentation_id|"
    r"assistant_message_id|receipt_id|tool_call_id|client_message_id|"
    r"idempotency_key|report_version_id|source_snapshot_id|"
    r"content_hash|runtime_content_hash|snapshot_hash|expected_revision|"
    r"expected_task_settings_revision|expected_ruleset_version|"
    r"expected_ruleset_content_hash|application_turn_id|runtime_turn_id"
    r")[`\"']?\s*[:=]\s*"
    r"(?:[`\"'][^`\"'\r\n]{0,512}[`\"']|[^,，。；;}\]\s\r\n]{1,512})"
)
_INTERNAL_VALUES = (
    re.compile(
        r"(?i)[`\"']?(?:"
        r"investigation-(?:draft|run|turn|message|session|workspace)|"
        r"ruleset-(?:proposal|presentation|revision)|"
        r"lexicon-(?:edit|revision)|creation-tool-receipt|"
        r"public-answer|investigation-turn-event"
        r"):[0-9a-z][0-9a-z:._-]{5,255}[`\"']?"
    ),
    re.compile(r"(?i)\b(?:call|tool-call|tool_call)[-_:][0-9a-z._:-]{8,255}\b"),
    re.compile(
        r"(?i)(?<![0-9a-f])(?:[0-9a-f]{32}|[0-9a-f]{64})(?![0-9a-f])"
    ),
    re.compile(
        r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
    ),
)
_LOCAL_PATH = re.compile(
    r"(?i)(?:/Users|/home|/var|/tmp)/[^\s`\"'<>]{2,512}"
)
_PARENTHESIZED_ID = re.compile(r"[（(]\s*id\s*=\s*[^）)\r\n]{1,256}[）)]", re.I)
_INTERNAL_RUNTIME_BRAND = re.compile(
    r"(?i)\bHermes(?:\s+Agent|助手)?(?:\s+v?\d+(?:\.\d+)*)?\b"
)

_PUBLIC_TERMS = (
    (re.compile(r"\bcomment_audit\b", re.I), "评论研判"),
    (re.compile(r"\bfusion_audit\b", re.I), "融合研判"),
    (re.compile(r"\bimage_evidence\b", re.I), "图片证据提取"),
    (re.compile(r"\bvideo_frame_evidence\b", re.I), "视频关键帧提取"),
)


def redact_creation_internal_references(value: str) -> tuple[str, bool]:
    """Project creation prose without identifiers or runtime metadata.

    The projection is intentionally cumulative and text-only so it can be used
    both by the live answer streamer and by final-answer persistence. Business
    prose remains readable, while identifiers continue to live only in the
    separately validated public artifact contracts.
    """

    original = str(value or "")
    redacted = _INTERNAL_ASSIGNMENT.sub("内部标识已隐藏", original)
    for pattern in _INTERNAL_VALUES:
        redacted = pattern.sub("内部标识已隐藏", redacted)
    redacted = _LOCAL_PATH.sub("内部路径已隐藏", redacted)
    redacted = _PARENTHESIZED_ID.sub("", redacted)
    redacted = _INTERNAL_FIELD.sub("内部字段", redacted)
    redacted = _INTERNAL_RUNTIME_BRAND.sub("研判助手", redacted)
    redacted = re.sub(r"(?<=我是)\s+(?=研判助手)", "", redacted)
    for pattern, replacement in _PUBLIC_TERMS:
        redacted = pattern.sub(replacement, redacted)
    return redacted, redacted != original


def has_failed_ruleset_update(messages: list[dict]) -> bool:
    """Only inspect explicit update failures in the current turn's tool results.

    Deferred tool_call schema rejections never reach the business service, so
    correlate the original invocation instead of relying on a mutation receipt.
    A later success only resolves failure for that same proposal.
    """
    calls = {}
    failed = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name")
                args = function.get("arguments") or {}
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except (ValueError, TypeError):
                    continue
                if not isinstance(args, dict):
                    continue
                if name == "tool_call":
                    name, args = args.get("name"), args.get("arguments") or {}
                if name == "update_ruleset_proposal" and isinstance(args, dict):
                    calls[call.get("id")] = str(args.get("proposal_id") or call.get("id"))
        elif message.get("role") == "tool" and message.get("tool_call_id") in calls:
            try:
                result = json.loads(message.get("content") or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(result, dict):
                continue
            proposal = calls[message["tool_call_id"]]
            if result.get("status") == "error" or result.get("error"):
                failed[proposal] = True
            elif result.get("status") == "ok" and isinstance(result.get("data"), dict):
                if result["data"].get("proposal_id") == proposal:
                    failed.pop(proposal, None)
    return any(failed.values())
