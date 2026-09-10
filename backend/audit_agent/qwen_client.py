from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import time
import threading
from pathlib import Path
from typing import Callable, TypeVar

import requests

from .config import settings
from .remote_inference import RemoteInferenceClient


logger = logging.getLogger(__name__)
T = TypeVar("T")


class ChatCompletionText(str):
    def __new__(cls, value: str, metadata: dict):
        instance = super().__new__(cls, value)
        instance.metadata = metadata
        return instance


class QwenProviderError(RuntimeError):
    pass


class QwenTimeoutError(QwenProviderError):
    """The request and its one timeout retry both timed out."""


class QwenClient:
    def __init__(self):
        self.api_key = settings.dashscope_api_key
        self.base_url = settings.dashscope_base_url
        self.chat_url = f"{self.base_url}/chat/completions"
        self.remote = RemoteInferenceClient()
        self.provider_failure = ""
        self._response_capture = threading.local()

    def last_raw_response(self):
        """Current thread's response for failure diagnostics; never headers."""
        return getattr(getattr(self, "_response_capture", None), "value", None)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def validate_authoritative_configuration(
        cls, configuration: dict | None = None
    ) -> None:
        errors: list[str] = []
        if settings.use_remote_llm:
            if not settings.remote_inference_base_url:
                errors.append("REMOTE_INFERENCE_BASE_URL")
        elif not settings.dashscope_api_key:
            errors.append("DASHSCOPE_API_KEY")
        if not str(settings.qwen_text_model or "").strip():
            errors.append("QWEN_TEXT_MODEL")
        if errors:
            raise RuntimeError(
                "authoritative audit Provider configuration is incomplete: "
                + ", ".join(sorted(set(errors)))
            )

    @classmethod
    def validate_authoritative_vision_configuration(
        cls, *, required_models: dict[str, str]
    ) -> None:
        errors: list[str] = []
        if settings.use_remote_vlm:
            if not settings.remote_inference_base_url:
                errors.append("REMOTE_INFERENCE_BASE_URL for vision")
        elif not settings.dashscope_api_key:
            errors.append("DASHSCOPE_API_KEY for vision")
        for setting_name, model in required_models.items():
            if not str(model or "").strip():
                errors.append(setting_name)
        if errors:
            raise RuntimeError(
                "authoritative vision Provider configuration is incomplete: "
                + ", ".join(sorted(set(errors)))
            )

    def analyze_image(
        self,
        image_path: Path | str,
        prompt: str,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
        compress_image: bool = True,
        image_max_side: int | None = None,
        image_quality: int | None = None,
        enable_thinking: bool | None = None,
    ) -> dict:
        if settings.use_remote_vlm:
            if not self.remote.enabled:
                raise RuntimeError("USE_REMOTE_VLM=true but REMOTE_INFERENCE_BASE_URL is empty")
            path = Path(str(image_path))
            if path.exists():
                image_bytes = self._image_bytes(
                    path,
                    compress_image=compress_image,
                    image_max_side=image_max_side,
                    image_quality=image_quality,
                )
                mime_type = self._image_mime_type(path, compress_image=compress_image)
                remote_compress_image = False
            else:
                image_bytes = requests.get(str(image_path), timeout=settings.request_timeout).content
                mime_type = "image/jpeg"
                remote_compress_image = compress_image
            try:
                return self.remote.analyze_image(
                    image_bytes,
                    prompt,
                    filename=path.name or "image.jpg",
                    mime_type=mime_type,
                    compress_image=remote_compress_image,
                    image_max_side=image_max_side,
                    image_quality=image_quality,
                    max_tokens=max_tokens,
                    model=model,
                    enable_thinking=enable_thinking,
                )
            except Exception as exc:
                raise self._provider_error("vision Provider request failed", exc) from exc

        if not self.enabled:
            return self._mock_image_result(str(image_path))

        image_url = self._to_image_url(
            image_path,
            compress_image=compress_image,
            image_max_side=image_max_side,
            image_quality=image_quality,
        )
        payload = {
            "model": model or settings.qwen_vl_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens or settings.qwen_max_tokens,
        }
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        text = self._post_chat(payload)
        return self._parse_chat_json(text)

    def audit_text(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
        enable_thinking: bool | None = None,
        request_timeout: int | float | None = None,
    ) -> dict:
        if not hasattr(self, "_response_capture"):
            self._response_capture = threading.local()
        self._response_capture.value = None
        if settings.use_remote_llm:
            if not self.remote.enabled:
                raise RuntimeError("USE_REMOTE_LLM=true but REMOTE_INFERENCE_BASE_URL is empty")
            try:
                return self._with_timeout_retry(lambda: self.remote.audit_text(
                    prompt,
                    max_tokens=max_tokens,
                    model=model,
                    enable_thinking=enable_thinking,
                    request_timeout=request_timeout,
                ))
            except QwenTimeoutError:
                raise
            except Exception as exc:
                raise self._provider_error("text Provider request failed", exc) from exc

        if not self.enabled:
            return self._mock_audit_result()

        payload = {
            "model": model or settings.qwen_text_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens or settings.qwen_max_tokens,
        }
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        if settings.qwen_use_response_format:
            payload["response_format"] = {"type": "json_object"}
        text = self._post_chat(payload, request_timeout=request_timeout)
        return self._parse_chat_json(text)

    def _post_chat(self, payload: dict, *, request_timeout: int | float | None = None) -> str:
        try:
            response = self._with_timeout_retry(lambda: requests.post(
                self.chat_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=request_timeout or settings.request_timeout,
            ))
        except QwenTimeoutError:
            raise
        except Exception as exc:
            raise self._provider_error("Provider request failed", exc) from exc
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = self._format_error_body(response)
            raise self._provider_error(
                f"LLM request failed: HTTP {response.status_code} {response.reason}; "
                f"model={payload.get('model')}; body={body}",
                exc,
            ) from exc
        try:
            data = response.json()
            if hasattr(self, "_response_capture"):
                self._response_capture.value = data
            choice = data["choices"][0]
        except Exception as exc:
            raise self._provider_error("Provider response is invalid", exc) from exc
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        metadata = {
            "model": data.get("model") or payload.get("model"),
            "max_tokens": payload.get("max_tokens"),
            "finish_reason": choice.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        metadata = {key: value for key, value in metadata.items() if value is not None}
        return ChatCompletionText(choice["message"]["content"], metadata)

    def _with_timeout_retry(self, request: Callable[[], T]) -> T:
        for attempt in (1, 2):
            try:
                result = request()
            except requests.Timeout as exc:
                if attempt == 2:
                    self.provider_failure = "Provider request timed out after one retry"
                    logger.error("Qwen request timed out on attempt 2/2; retry exhausted")
                    raise QwenTimeoutError(self.provider_failure) from exc
                # Do not log prompts, credentials, URLs, or raw exception messages.
                logger.warning("Qwen request timed out on attempt 1/2; retrying once")
                time.sleep(1)
            else:
                if attempt == 2:
                    logger.info("Qwen request succeeded on timeout retry 2/2")
                return result
        raise AssertionError("unreachable")

    def _provider_error(self, message: str, exc: BaseException) -> QwenProviderError:
        self.provider_failure = message
        return QwenProviderError(message)

    def _parse_chat_json(self, text: str) -> dict:
        result = self._parse_json_object(text, fallback={"raw_response": str(text)})
        metadata = getattr(text, "metadata", None)
        if isinstance(metadata, dict) and metadata:
            result["_llm_meta"] = metadata
        return result

    def _format_error_body(self, response: requests.Response) -> str:
        text = response.text.strip()
        if not text:
            return "<empty>"
        try:
            return json.dumps(response.json(), ensure_ascii=False)
        except ValueError:
            return text[:2000]

    def _to_image_url(
        self,
        image_path: Path | str,
        *,
        compress_image: bool = True,
        image_max_side: int | None = None,
        image_quality: int | None = None,
    ) -> str:
        value = str(image_path)
        if value.startswith("http://") or value.startswith("https://"):
            return value
        path = Path(value)
        mime = self._image_mime_type(path, compress_image=compress_image)
        encoded = base64.b64encode(
            self._image_bytes(
                path,
                compress_image=compress_image,
                image_max_side=image_max_side,
                image_quality=image_quality,
            )
        ).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _image_bytes(
        self,
        path: Path,
        *,
        compress_image: bool = True,
        image_max_side: int | None = None,
        image_quality: int | None = None,
    ) -> bytes:
        if not compress_image:
            return path.read_bytes()
        return self._compressed_image_bytes(
            path,
            image_max_side=image_max_side,
            image_quality=image_quality,
        )

    @staticmethod
    def _image_mime_type(path: Path, *, compress_image: bool = True) -> str:
        if compress_image:
            return "image/jpeg"
        return mimetypes.guess_type(path.name)[0] or "image/png"

    def _compressed_image_bytes(
        self,
        path: Path,
        *,
        image_max_side: int | None = None,
        image_quality: int | None = None,
    ) -> bytes:
        try:
            from PIL import Image
        except Exception:
            return path.read_bytes()

        with Image.open(path) as image:
            image = image.convert("RGB")
            max_side = max(64, image_max_side or settings.vl_image_max_side)
            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            quality = min(95, max(35, image_quality or settings.vl_image_quality))
            image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue()

    def _parse_json_object(self, text: str, fallback: dict) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return fallback

    def _mock_image_result(self, source: str) -> dict:
        return {
            "ocr_text": "",
            "visual_summary": f"未配置 DASHSCOPE_API_KEY，跳过真实图片分析：{source}",
            "risk_items": [],
        }

    def _mock_audit_result(self) -> dict:
        return {
            "summary": "未配置 DASHSCOPE_API_KEY，当前为占位审核结果。",
            "decision": "review",
            "risk_level": "unknown",
            "categories": ["未配置模型"],
            "evidence": [],
        }
