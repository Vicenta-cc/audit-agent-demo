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


def validate_hermes_transcript_messages(
    messages: list[dict[str, Any]],
    *,
    require_final_assistant: bool = True,
) -> None:
    """Validate the persisted Hermes 0.20.4 chat-completions message contract."""

    if not isinstance(messages, list):
        raise ToolProtocolError("Hermes transcript must be a list")
    seen_call_ids: set[str] = set()
    index = 0
    while index < len(messages):
        message = messages[index]
        if not isinstance(message, dict):
            raise ToolProtocolError("Hermes transcript messages must be objects")
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            raise ToolProtocolError("Hermes transcript contains an unsupported role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ToolProtocolError("Hermes transcript message content must be text")
        if role == "tool":
            raise ToolProtocolError("orphan Hermes ToolResult")
        if role != "assistant" or not message.get("tool_calls"):
            if role != "assistant" and "tool_calls" in message:
                raise ToolProtocolError("only assistant messages may contain tool_calls")
            if role == "assistant" and "tool_calls" in message:
                calls = message["tool_calls"]
                if not isinstance(calls, list):
                    raise ToolProtocolError("assistant tool_calls must be a list")
            index += 1
            continue

        calls = message["tool_calls"]
        if not isinstance(calls, list):
            raise ToolProtocolError("assistant tool_calls must be a list")
        expected: list[str] = []
        for call in calls:
            if not isinstance(call, dict):
                raise ToolProtocolError("Hermes tool call must be an object")
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ToolProtocolError("Hermes tool call id must be non-empty")
            if call_id in seen_call_ids:
                raise ToolProtocolError("Hermes tool call id must be globally unique")
            call_type = call.get("type", "function")
            if call_type != "function":
                raise ToolProtocolError("Hermes tool call type must be function")
            function = call.get("function")
            if not isinstance(function, dict):
                raise ToolProtocolError("Hermes tool call function must be an object")
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ToolProtocolError("Hermes tool call name must be non-empty")
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ToolProtocolError(
                        "Hermes tool call arguments must contain valid JSON"
                    ) from exc
            if not isinstance(arguments, dict):
                raise ToolProtocolError(
                    "Hermes tool call arguments must be an object or object JSON"
                )
            seen_call_ids.add(call_id)
            expected.append(call_id)

        results = messages[index + 1 : index + 1 + len(expected)]
        if len(results) != len(expected):
            raise ToolProtocolError("dangling Hermes tool call")
        actual: list[str] = []
        for result in results:
            if not isinstance(result, dict) or result.get("role") != "tool":
                raise ToolProtocolError(
                    "Hermes ToolResults must immediately follow their assistant call"
                )
            result_id = result.get("tool_call_id")
            if not isinstance(result_id, str) or not result_id.strip():
                raise ToolProtocolError("Hermes ToolResult id must be non-empty")
            if not isinstance(result.get("content"), str):
                raise ToolProtocolError("Hermes ToolResult content must be text")
            actual.append(result_id)
        if actual != expected:
            raise ToolProtocolError(
                "Hermes ToolResults are missing, duplicated, orphaned, or out of order"
            )
        index += 1 + len(expected)

    if require_final_assistant:
        if not messages or messages[-1].get("role") != "assistant":
            raise ToolProtocolError("completed Hermes transcript needs a final assistant")
        final_calls = messages[-1].get("tool_calls")
        if final_calls:
            raise ToolProtocolError("final Hermes assistant still has pending tool calls")


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
