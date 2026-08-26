from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

import requests

from backend.audit_agent.config import settings
from backend.investigation.contracts import QwenChatResult, QwenUsage, ToolCall
from backend.investigation.errors import QwenChatError, ToolProtocolError
from backend.investigation.protocol import validate_model_request_messages


class QwenChatToolClient:
    """Independent OpenAI-compatible Qwen client for chat and function calling."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | float = 120,
        max_tokens: int = 2_000,
        max_retries: int = 2,
        allowed_tool_names: frozenset[str] = frozenset(),
    ):
        self.api_key = settings.dashscope_api_key if api_key is None else api_key
        self.base_url = (base_url or settings.dashscope_base_url).rstrip("/")
        self.chat_url = f"{self.base_url}/chat/completions"
        self.model = (model or settings.qwen_text_model).strip()
        self.timeout = timeout
        self.max_tokens = max(128, int(max_tokens))
        self.max_retries = max(0, int(max_retries))
        self.allowed_tool_names = allowed_tool_names

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: LiteralToolChoice = "auto",
        timeout: int | float | None = None,
    ) -> QwenChatResult:
        if not self.enabled:
            raise QwenChatError(
                "configuration",
                "DASHSCOPE_API_KEY 未配置，无法执行真实调查对话。",
                retryable=False,
            )
        validate_model_request_messages(messages)
        if tool_choice not in {"auto", "none", "required"}:
            raise ToolProtocolError("unsupported tool_choice")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            "enable_thinking": False,
        }
        if tools and tool_choice != "none":
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        attempts = self.max_retries + 1
        last_error: QwenChatError | None = None
        for attempt in range(attempts):
            try:
                response = requests.post(
                    self.chat_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout or self.timeout,
                )
                return self._parse_response(response)
            except requests.Timeout:
                last_error = QwenChatError(
                    "timeout", "千问对话请求超时。", retryable=True
                )
            except requests.RequestException:
                last_error = QwenChatError(
                    "transport", "千问对话服务暂时不可达。", retryable=True
                )
            except QwenChatError as exc:
                last_error = exc
            if last_error is None or not last_error.retryable or attempt + 1 >= attempts:
                break
            retry_after = 0.5 * (2**attempt)
            if "response" in locals() and response is not None:
                try:
                    retry_after = min(5.0, max(retry_after, float(response.headers.get("Retry-After", 0))))
                except (TypeError, ValueError):
                    pass
            time.sleep(retry_after)
        raise last_error or QwenChatError(
            "unknown", "千问对话请求失败。", retryable=False
        )

    def _parse_response(self, response: requests.Response) -> QwenChatResult:
        if not response.ok:
            status = int(response.status_code)
            retryable = status in {408, 409, 429} or status >= 500
            if status in {401, 403}:
                safe = "千问 API 鉴权失败，请检查服务配置。"
                kind = "authentication"
            elif status == 429:
                safe = "千问 API 当前请求过多或额度受限。"
                kind = "rate_limit"
            elif status >= 500:
                safe = "千问服务暂时不可用。"
                kind = "server"
            else:
                safe = f"千问请求未被接受（HTTP {status}）。"
                kind = "http"
            raise QwenChatError(kind, safe, retryable=retryable, status_code=status)
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise QwenChatError(
                "response_format", "千问返回了无法识别的响应结构。", retryable=True
            ) from exc
        calls = self._parse_tool_calls(message.get("tool_calls") or [])
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_tokens = self._non_negative_int(
            usage.get("prompt_tokens", usage.get("input_tokens", 0))
        )
        output_tokens = self._non_negative_int(
            usage.get("completion_tokens", usage.get("output_tokens", 0))
        )
        total_tokens = self._non_negative_int(usage.get("total_tokens", 0))
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        request_id = str(
            data.get("id")
            or response.headers.get("x-request-id")
            or response.headers.get("X-Request-Id")
            or f"qwen-request:{uuid4().hex}"
        )
        return QwenChatResult(
            content=str(message.get("content") or ""),
            tool_calls=tuple(calls),
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=QwenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            model=str(data.get("model") or self.model),
            request_id=request_id,
        )

    def _parse_tool_calls(self, raw_calls: Any) -> list[ToolCall]:
        if not isinstance(raw_calls, list):
            raise QwenChatError(
                "tool_protocol", "千问返回的工具调用结构无效。", retryable=True
            )
        if len(raw_calls) > 8:
            raise QwenChatError(
                "tool_protocol", "千问单次返回的工具调用过多。", retryable=True
            )
        calls = []
        seen: set[str] = set()
        for raw in raw_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                raise QwenChatError(
                    "tool_protocol", "千问返回的工具调用结构无效。", retryable=True
                )
            call_id = str(raw.get("id") or "").strip()
            function = raw["function"]
            name = str(function.get("name") or "").strip()
            if not call_id or call_id in seen:
                raise QwenChatError(
                    "tool_protocol", "千问返回了空或重复的 tool_call_id。", retryable=True
                )
            if self.allowed_tool_names and name not in self.allowed_tool_names:
                raise QwenChatError(
                    "tool_protocol", "千问请求了未开放的工具。", retryable=False
                )
            seen.add(call_id)
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments or "{}")
                except json.JSONDecodeError as exc:
                    raise QwenChatError(
                        "tool_protocol", "千问返回的工具参数不是有效 JSON。", retryable=True
                    ) from exc
            if not isinstance(arguments, dict):
                raise QwenChatError(
                    "tool_protocol", "千问返回的工具参数必须是对象。", retryable=True
                )
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return calls

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0


LiteralToolChoice = str
