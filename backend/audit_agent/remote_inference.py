from __future__ import annotations

from pathlib import Path
import mimetypes
import time

import requests

from .config import settings


class RemoteInferenceClient:
    def __init__(self):
        self.base_url = settings.remote_inference_base_url
        self.asr_base_url = settings.remote_asr_base_url
        self.mms_asr_base_url = settings.remote_mms_asr_base_url
        self.translation_base_url = settings.remote_translation_base_url
        self.ocr_base_url = settings.remote_ocr_base_url
        self.api_key = settings.remote_inference_api_key
        self.timeout = settings.request_timeout
        self.asr_request_retries = settings.remote_asr_request_retries
        self.asr_retry_backoff_seconds = settings.remote_asr_retry_backoff_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @property
    def asr_enabled(self) -> bool:
        return bool(self.asr_base_url)

    @property
    def mms_asr_enabled(self) -> bool:
        return bool(self.mms_asr_base_url)

    @property
    def translation_enabled(self) -> bool:
        return bool(self.translation_base_url)

    @property
    def ocr_enabled(self) -> bool:
        return bool(self.ocr_base_url)

    def transcribe(self, audio_path: Path) -> dict:
        return self._post_audio_with_retry(
            f"{self.asr_base_url}/api/inference/transcribe",
            audio_path,
            "remote ASR failed",
        )

    def mms_transcribe(self, audio_path: Path) -> dict:
        return self._post_audio_with_retry(
            f"{self.mms_asr_base_url}/api/inference/mms-transcribe",
            audio_path,
            "remote MMS ASR failed",
        )

    def _post_audio_with_retry(
        self,
        url: str,
        audio_path: Path,
        error_message: str,
    ) -> dict:
        attempts = self.asr_request_retries + 1
        for attempt in range(attempts):
            try:
                with audio_path.open("rb") as audio_file:
                    response = requests.post(
                        url,
                        headers=self._headers(),
                        files={"audio": (audio_path.name, audio_file, "audio/wav")},
                        timeout=self.timeout,
                    )
            except (requests.ConnectionError, requests.Timeout):
                if attempt + 1 >= attempts:
                    raise
                self._wait_before_asr_retry(attempt)
                continue

            if not self._retryable_asr_status(response.status_code) or attempt + 1 >= attempts:
                return self._json_response(response, error_message)
            self._wait_before_asr_retry(attempt)

        raise RuntimeError(f"{error_message}: retry loop exhausted")

    def _wait_before_asr_retry(self, attempt: int) -> None:
        delay = self.asr_retry_backoff_seconds * (2**attempt)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _retryable_asr_status(status_code: int) -> bool:
        return status_code in {408, 429} or status_code >= 500

    def translate(
        self,
        text: str,
        source_language: str = "ug",
        target_language: str = "中文",
        context: str = "",
    ) -> dict:
        response = requests.post(
            f"{self.translation_base_url}/api/inference/translate",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "text": text,
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
            },
            timeout=self.timeout,
        )
        return self._json_response(response, "remote translation failed")

    def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str,
        filename: str = "image.jpg",
        mime_type: str = "image/jpeg",
        compress_image: bool = True,
        image_max_side: int | None = None,
        image_quality: int | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        enable_thinking: bool | None = None,
    ) -> dict:
        data = {
            "prompt": prompt,
            "compress_image": "true" if compress_image else "false",
        }
        if model:
            data["model"] = model
        if image_max_side is not None:
            data["image_max_side"] = str(image_max_side)
        if image_quality is not None:
            data["image_quality"] = str(image_quality)
        if max_tokens is not None:
            data["max_tokens"] = str(max_tokens)
        if enable_thinking is not None:
            data["enable_thinking"] = "true" if enable_thinking else "false"
        response = requests.post(
            f"{self.base_url}/api/inference/analyze-image",
            headers=self._headers(),
            data=data,
            files={"image": (filename, image_bytes, mime_type)},
            timeout=self.timeout,
        )
        return self._json_response(response, "remote VLM failed")

    def ocr_image(
        self,
        image_path: Path,
        language_hint: str = "ug",
        engine: str = "paddleocr_vl",
    ) -> dict:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        with image_path.open("rb") as f:
            response = requests.post(
                f"{self.ocr_base_url}/api/inference/ocr",
                headers=self._headers(),
                data={
                    "engine": engine,
                    "language_hint": language_hint,
                },
                files={"image": (image_path.name, f, mime_type)},
                timeout=self.timeout,
            )
        return self._json_response(response, "remote OCR failed")

    def audit_text(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
        enable_thinking: bool | None = None,
        request_timeout: int | float | None = None,
    ) -> dict:
        payload = {"prompt": prompt}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if model:
            payload["model"] = model
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        if request_timeout is not None:
            payload["request_timeout"] = request_timeout
        response = requests.post(
            f"{self.base_url}/api/inference/audit-text",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=(request_timeout + 5) if request_timeout is not None else self.timeout,
        )
        return self._json_response(response, "remote LLM failed")

    def _headers(self) -> dict:
        if not self.api_key:
            return {}
        return {"X-Inference-Key": self.api_key}

    @staticmethod
    def _json_response(response: requests.Response, message: str) -> dict:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"{message}: HTTP {response.status_code}; body={response.text[:1000]}") from exc
        return response.json()
