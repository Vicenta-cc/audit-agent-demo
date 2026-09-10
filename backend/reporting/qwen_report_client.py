from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from backend.audit_agent.config import settings
from backend.reporting.contracts import ModelStepResult
from backend.reporting.errors import ReportModelError


T = TypeVar("T", bound=BaseModel)


class QwenReportClient:
    """Dedicated structured-output adapter for report planning and section drafting."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        timeout: int | float | None = None,
        max_tokens: int | None = None,
        exchange_recorder: Callable[[dict[str, Any], dict[str, Any], str, dict[str, Any]], None] | None = None,
    ):
        self.api_key = settings.dashscope_api_key if api_key is None else api_key
        self.base_url = (base_url or settings.dashscope_base_url).rstrip("/")
        self.chat_url = f"{self.base_url}/chat/completions"
        self.model = model or settings.qwen_report_model
        self.prompt_version = prompt_version or settings.report_prompt_version
        self.timeout = timeout or settings.report_request_timeout
        self.max_tokens = max_tokens or settings.report_max_tokens
        self.exchange_recorder = exchange_recorder
        self.exchange_context: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        response_model: type[T],
        max_tokens: int | None = None,
        allow_repair: bool = True,
        allow_truncation_repair: bool = False,
    ) -> ModelStepResult:
        if not self.enabled:
            raise ReportModelError(
                "configuration",
                "DASHSCOPE_API_KEY is required for real report generation",
                retryable=False,
            )
        schema = response_model.model_json_schema()
        schema_message = {
            "role": "system",
            "content": (
                "Return exactly one JSON object matching this JSON Schema. "
                "Do not include markdown fences or explanatory text.\n"
                + json.dumps(schema, ensure_ascii=False)
            ),
        }
        request_messages = [schema_message, *messages]
        attempts = 2 if (allow_repair or allow_truncation_repair) else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            request_limit = max_tokens or self.max_tokens
            if attempt and allow_truncation_repair and not allow_repair:
                request_limit = max(512, int(request_limit * 0.75))
            raw, metadata = self._request(request_messages, max_tokens=request_limit)
            if metadata.get("finish_reason") == "length":
                last_error = ReportModelError(
                    "truncated_model_response",
                    f"{response_model.__name__} response reached max_tokens before a complete JSON object",
                    retryable=True,
                )
                if attempt + 1 < attempts:
                    request_messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "上一响应达到长度上限且未形成完整JSON。请只返回更短的完整JSON，"
                                "严格遵守字段和段落数量限制，不要解释或重复无关内容。"
                            ),
                        },
                    ]
                    continue
                break
            try:
                payload = self._parse_json_object(raw)
                validated = response_model.model_validate(payload)
                return ModelStepResult(
                    output=validated.model_dump(mode="json"),
                    model=str(metadata.get("model") or self.model),
                    usage=metadata,
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                if not allow_repair or attempt + 1 >= attempts:
                    break
                request_messages.extend(
                    (
                        {"role": "assistant", "content": raw[:12000]},
                        {
                            "role": "user",
                            "content": (
                                "The previous response failed schema validation. Correct only the JSON "
                                f"structure and return a complete replacement. Validation error: {exc}"
                            ),
                        },
                    )
                )
        raise ReportModelError(
            "structured_output",
            f"Qwen response does not satisfy {response_model.__name__}: {last_error}",
            retryable=True,
        )

    def _request(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None,
    ) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens or self.max_tokens,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
        }
        try:
            response = requests.post(
                self.chat_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            self._record_exchange(payload, {"error": str(exc)}, "timeout", {})
            raise ReportModelError("timeout", f"Qwen report request timed out: {exc}", retryable=True) from exc
        except requests.RequestException as exc:
            self._record_exchange(payload, {"error": str(exc)}, "transport_error", {})
            raise ReportModelError("transport", f"Qwen report request failed: {exc}", retryable=True) from exc
        if not response.ok:
            body = response.text.strip()[:2000]
            retryable = response.status_code == 429 or response.status_code >= 500
            self._record_exchange(
                payload,
                {"status_code": response.status_code, "body": body},
                "http_error",
                {"status_code": response.status_code},
            )
            raise ReportModelError(
                "http",
                f"Qwen report request HTTP {response.status_code}: {body or '<empty>'}",
                retryable=retryable,
            )
        try:
            data = response.json()
            choice = data["choices"][0]
            content = str(choice["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if "data" not in locals():
                self._record_exchange(
                    payload,
                    {"error": str(exc), "body": response.text[:12000]},
                    "response_format_error",
                    {"status_code": response.status_code},
                )
            raise ReportModelError(
                "response_format", f"Qwen response envelope is invalid: {exc}", retryable=True
            ) from exc
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        finish_reason = choice.get("finish_reason")
        provider_metadata = {
            "status_code": response.status_code,
            "finish_reason": finish_reason,
        }
        if finish_reason == "length":
            provider_metadata["truncated_model_response"] = True
        self._record_exchange(
            payload,
            data if isinstance(data, dict) else {"raw": data},
            "provider_returned",
            provider_metadata,
        )
        metadata = {
            "model": data.get("model") or self.model,
            "finish_reason": finish_reason,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        return content, {key: value for key, value in metadata.items() if value is not None}

    def _record_exchange(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.exchange_recorder is None:
            return
        combined = {**metadata, **self.exchange_context}
        self.exchange_recorder(request, response, status, combined)

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise
            parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("structured response is not a JSON object")
        return parsed
