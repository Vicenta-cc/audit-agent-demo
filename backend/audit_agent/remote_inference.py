from __future__ import annotations

from pathlib import Path
import mimetypes

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
        with audio_path.open("rb") as f:
            response = requests.post(
                f"{self.asr_base_url}/api/inference/transcribe",
                headers=self._headers(),
                files={"audio": (audio_path.name, f, "audio/wav")},
                timeout=self.timeout,
            )
        return self._json_response(response, "remote ASR failed")

    def mms_transcribe(self, audio_path: Path) -> dict:
        with audio_path.open("rb") as f:
            response = requests.post(
                f"{self.mms_asr_base_url}/api/inference/mms-transcribe",
                headers=self._headers(),
                files={"audio": (audio_path.name, f, "audio/wav")},
                timeout=self.timeout,
            )
        return self._json_response(response, "remote MMS ASR failed")

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

    def analyze_image(self, image_bytes: bytes, prompt: str, filename: str = "image.jpg") -> dict:
        response = requests.post(
            f"{self.base_url}/api/inference/analyze-image",
            headers=self._headers(),
            data={"prompt": prompt},
            files={"image": (filename, image_bytes, "image/jpeg")},
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

    def audit_text(self, prompt: str) -> dict:
        response = requests.post(
            f"{self.base_url}/api/inference/audit-text",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"prompt": prompt},
            timeout=self.timeout,
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
