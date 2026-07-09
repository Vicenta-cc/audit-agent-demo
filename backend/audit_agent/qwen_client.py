from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import requests

from .config import settings
from .remote_inference import RemoteInferenceClient


class QwenClient:
    def __init__(self):
        self.api_key = settings.dashscope_api_key
        self.base_url = settings.dashscope_base_url
        self.chat_url = f"{self.base_url}/chat/completions"
        self.remote = RemoteInferenceClient()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def analyze_image(self, image_path: Path | str, prompt: str, *, max_tokens: int | None = None) -> dict:
        if settings.use_remote_vlm:
            if not self.remote.enabled:
                raise RuntimeError("USE_REMOTE_VLM=true but REMOTE_INFERENCE_BASE_URL is empty")
            path = Path(str(image_path))
            image_bytes = self._compressed_image_bytes(path) if path.exists() else requests.get(str(image_path), timeout=settings.request_timeout).content
            return self.remote.analyze_image(image_bytes, prompt, filename=path.name or "image.jpg")

        if not self.enabled:
            return self._mock_image_result(str(image_path))

        image_url = self._to_image_url(image_path)
        payload = {
            "model": settings.qwen_vl_model,
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
        text = self._post_chat(payload)
        return self._parse_json_object(text, fallback={"raw_response": text})

    def audit_text(self, prompt: str, *, max_tokens: int | None = None, model: str | None = None) -> dict:
        if settings.use_remote_llm:
            if not self.remote.enabled:
                raise RuntimeError("USE_REMOTE_LLM=true but REMOTE_INFERENCE_BASE_URL is empty")
            return self.remote.audit_text(prompt)

        if not self.enabled:
            return self._mock_audit_result()

        payload = {
            "model": model or settings.qwen_text_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens or settings.qwen_max_tokens,
        }
        if settings.qwen_use_response_format:
            payload["response_format"] = {"type": "json_object"}
        text = self._post_chat(payload)
        return self._parse_json_object(text, fallback={"raw_response": text})

    def _post_chat(self, payload: dict) -> str:
        response = requests.post(
            self.chat_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.request_timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = self._format_error_body(response)
            raise RuntimeError(
                f"LLM request failed: HTTP {response.status_code} {response.reason}; "
                f"model={payload.get('model')}; url={self.chat_url}; body={body}"
            ) from exc
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _format_error_body(self, response: requests.Response) -> str:
        text = response.text.strip()
        if not text:
            return "<empty>"
        try:
            return json.dumps(response.json(), ensure_ascii=False)
        except ValueError:
            return text[:2000]

    def _to_image_url(self, image_path: Path | str) -> str:
        value = str(image_path)
        if value.startswith("http://") or value.startswith("https://"):
            return value
        path = Path(value)
        mime = "image/jpeg"
        encoded = base64.b64encode(self._compressed_image_bytes(path)).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _compressed_image_bytes(self, path: Path) -> bytes:
        try:
            from PIL import Image
        except Exception:
            return path.read_bytes()

        with Image.open(path) as image:
            image = image.convert("RGB")
            max_side = max(64, settings.vl_image_max_side)
            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            quality = min(95, max(35, settings.vl_image_quality))
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
