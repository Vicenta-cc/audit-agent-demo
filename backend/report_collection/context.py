"""Lossless storage, optional smaller model view of older tool results.

Only replayable collection results are projected. User/assistant messages,
tool call arguments, errors and the two most recent turns remain verbatim.
No intent routing, summarizing model, or answer validation is involved.
"""
from copy import deepcopy
import json


RESULT_TOOLS = frozenset({
    "query_conversation_reports", "read_conversation_result", "compare_conversation_config",
})


def project_history(history, *, keep_from):
    projected = deepcopy(history)
    if not projected:
        return projected
    if not 0 <= keep_from <= len(projected):
        raise ValueError("Invalid durable history boundary")
    for message in projected[:keep_from]:
        if message.get("role") != "tool" or message.get("name") not in RESULT_TOOLS:
            continue
        try:
            response = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(response, dict) or response.get("ok") is not True:
            continue
        data = response.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("result_ref"), str) or not data["result_ref"]:
            continue
        # Retain coverage, counts and sources verbatim. Omitted rows are explicitly
        # absent, never an empty array that could be mistaken for zero results.
        omitted = [key for key in ("items", "configs") if key in data]
        if not omitted:
            continue
        index = {key: value for key, value in data.items() if key not in omitted}
        index["history_projection"] = {
            "omitted_fields": omitted,
            "read_tool": "read_conversation_result",
            "result_ref": data["result_ref"],
        }
        content = json.dumps({**response, "data": index}, ensure_ascii=False)
        if len(content) < len(message["content"]):
            message["content"] = content
    return projected


def restore_transcript(transcript, *, model_history, stored_history):
    """Append only the validated new turn to the original durable history."""
    prefix = model_history or []
    if transcript[:len(prefix)] != prefix:
        raise ValueError("Model transcript changed its input history")
    return deepcopy(stored_history or []) + deepcopy(transcript[len(prefix):])
