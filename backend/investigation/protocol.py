from __future__ import annotations

import json
from typing import Any

from backend.investigation.errors import ToolProtocolError


def validate_model_request_messages(messages: list[dict[str, Any]]) -> None:
    if not messages or messages[0].get("role") != "system":
        raise ToolProtocolError("the first message must be the only system message")
    if sum(1 for item in messages if item.get("role") == "system") != 1:
        raise ToolProtocolError("exactly one system message is allowed")

    index = 1
    seen_call_ids: set[str] = set()
    while index < len(messages):
        message = messages[index]
        role = str(message.get("role") or "")
        if role == "tool":
            raise ToolProtocolError("orphan tool result")
        calls = message.get("tool_calls") if role == "assistant" else None
        if not calls:
            index += 1
            continue
        if not isinstance(calls, list):
            raise ToolProtocolError("assistant tool_calls must be a list")
        expected: list[str] = []
        for call in calls:
            if not isinstance(call, dict):
                raise ToolProtocolError("tool call must be an object")
            call_id = str(call.get("id") or "")
            if not call_id or call_id in seen_call_ids:
                raise ToolProtocolError("tool_call_id must be non-empty and unique")
            seen_call_ids.add(call_id)
            expected.append(call_id)
        results = messages[index + 1 : index + 1 + len(expected)]
        if len(results) != len(expected):
            raise ToolProtocolError("dangling tool call")
        actual = []
        for result in results:
            if result.get("role") != "tool":
                raise ToolProtocolError("tool results must immediately follow their assistant call")
            actual.append(str(result.get("tool_call_id") or ""))
        if actual != expected:
            raise ToolProtocolError("tool results are missing, orphaned, or out of order")
        index += 1 + len(expected)


def assistant_tool_message(tool_calls: list[dict[str, Any]], content: str = "") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": item["id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": json.dumps(
                        item["arguments"], ensure_ascii=False, sort_keys=True
                    ),
                },
            }
            for item in tool_calls
        ],
    }
