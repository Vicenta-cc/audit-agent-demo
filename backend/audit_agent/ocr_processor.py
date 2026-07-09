from __future__ import annotations

import json
import html
import hashlib
import re
import time
import types
import threading
import copy
from dataclasses import asdict, is_dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import requests

from .config import settings
from .remote_inference import RemoteInferenceClient

cv2 = None
np = None

_IMAGE_PATH_LINE_RE = re.compile(r"^(?:[A-Za-z]:)?[\\/].+\.(?:png|jpe?g|webp|bmp|tiff?)$", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]*\)")
_PADDLE_OCR_ARTIFACT_LINES = {
    "ocr",
    "number",
    "footnote",
    "header",
    "header_image",
    "footer",
    "footer_image",
    "aside_text",
    "aside_image",
    "数字",
    "脚注",
    "标题",
    "标题图片",
    "页脚",
    "页脚文字",
    "页脚图片",
    "侧边栏文字",
    "侧边文字",
}

_UYGHUR_STRONG_PROMPT_EN = (
    "OCR: The image contains Uyghur text written in Arabic script, read from right to left. "
    "Transcribe the visible Uyghur text exactly. Output only the original Uyghur text. "
    "Do not translate, explain, romanize, or normalize it into Arabic, Persian, Urdu, or another language. "
    "Preserve the original content and line breaks."
)

_UYGHUR_STRONG_PROMPT_ZH = (
    "OCR: 你是一个专业的维吾尔语 OCR 专家。请对图片中的维吾尔文"
    "（Uyghur，采用阿拉伯字母书写，从右向左阅读）进行精准的文字识别。"
    "请直接输出识别到的维吾尔语文本，不要将其误认为是阿拉伯语或波斯语，"
    "不要解释，不要翻译，严格保持原文内容。"
)

_PADDLE_PROMPT_PRESETS = {
    "none": "",
    "hf-ocr": "OCR:",
    "uyghur-strong-en": _UYGHUR_STRONG_PROMPT_EN,
    "uyghur-strong-zh": _UYGHUR_STRONG_PROMPT_ZH,
}

_PADDLE_TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class _TransientPaddleOCRRequestError(RuntimeError):
    pass


def _load_cv_deps() -> None:
    global cv2, np
    if cv2 is None or np is None:
        import cv2 as cv2_module
        import numpy as np_module

        cv2 = cv2_module
        np = np_module


def _serializable(value: Any) -> Any:
    if is_dataclass(value):
        return _serializable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "model_dump"):
        return _serializable(value.model_dump())
    if hasattr(value, "__dict__"):
        return _serializable(vars(value))
    return str(value)


def _extract_text(payload: Any) -> str:
    chunks: list[str] = []

    def walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                chunks.append(stripped)
            return
        if isinstance(value, dict):
            for key in ("markdown", "text", "content", "rec_text", "rec_texts", "description", "html"):
                if key in value:
                    walk(value[key])
            for key, item in value.items():
                if key in {"image", "input_img", "output_img", "page_img"}:
                    continue
                if key not in {"markdown", "text", "content", "rec_text", "rec_texts", "description", "html"}:
                    walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return _clean_extracted_text(chunks)


def _clean_extracted_text(chunks: list[str]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for chunk in chunks:
        for raw_line in chunk.splitlines():
            line = html.unescape(raw_line).strip()
            line = _MARKDOWN_IMAGE_RE.sub("", line)
            line = _HTML_TAG_RE.sub("", line).strip()
            line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line).strip()
            if not line:
                continue
            compact = re.sub(r"\s+", " ", line)
            if _is_paddle_ocr_artifact_line(compact):
                continue
            if compact not in seen:
                seen.add(compact)
                lines.append(compact)
    return "\n".join(lines)


def _is_paddle_ocr_artifact_line(line: str) -> bool:
    lowered = line.lower()
    if lowered in _PADDLE_OCR_ARTIFACT_LINES:
        return True
    return bool(_IMAGE_PATH_LINE_RE.match(line))


def _compose_prompt(base_prompt: str, override_prompt: str, mode: str) -> str:
    if mode == "replace":
        return override_prompt
    if mode == "prepend":
        return f"{override_prompt}\n{base_prompt}".strip()
    if mode == "append":
        return f"{base_prompt}\n{override_prompt}".strip()
    raise ValueError(f"Unsupported OCR_PADDLE_PROMPT_COMPOSE: {mode}")


def _resolve_paddle_prompt_override() -> tuple[str, str]:
    preset = (settings.ocr_paddle_prompt_preset or "none").strip()
    if not preset or preset == "none":
        return preset or "none", ""
    if preset not in _PADDLE_PROMPT_PRESETS:
        supported = ", ".join(sorted(_PADDLE_PROMPT_PRESETS))
        raise ValueError(f"Unsupported OCR_PADDLE_PROMPT_PRESET: {preset}. Supported: {supported}")
    return preset, _PADDLE_PROMPT_PRESETS[preset]


def _install_paddle_prompt_override(pipeline: Any, override_prompt: str, compose_mode: str) -> bool:
    if not override_prompt:
        return False

    inner_pipeline = getattr(pipeline, "paddlex_pipeline", pipeline)
    method_name = "_paddleocr_vl_collect_page_vlm_entries_core"
    original_method = getattr(inner_pipeline, method_name, None)
    if not callable(original_method):
        raise RuntimeError(
            "PaddleOCR-VL does not expose the internal VLM prompt hook required by "
            "OCR_PADDLE_PROMPT_PRESET. Disable the preset with OCR_PADDLE_PROMPT_PRESET=none "
            "or use a PaddleOCR-VL version compatible with the smoke-test hook."
        )

    def patched_method(self, page_idx, blocks_for_img, imgs_in_doc_for_img, layout_prep_cfg):
        entries, page_has_spotting, page_drop_figures = original_method(
            page_idx,
            blocks_for_img,
            imgs_in_doc_for_img,
            layout_prep_cfg,
        )
        patched_entries = []
        for entry in entries:
            entry_page_idx, block_idx, block_img, base_prompt, pixel_limits, figure_token_map = entry
            patched_entries.append(
                (
                    entry_page_idx,
                    block_idx,
                    block_img,
                    _compose_prompt(base_prompt, override_prompt, compose_mode),
                    pixel_limits,
                    figure_token_map,
                )
            )
        return patched_entries, page_has_spotting, page_drop_figures

    setattr(inner_pipeline, method_name, types.MethodType(patched_method, inner_pipeline))
    return True


class PaddleOCRVLProcessor:
    def __init__(self):
        self._job_url = settings.paddleocr_api_job_url
        self._token = settings.paddleocr_api_token
        self._model = settings.paddleocr_api_model
        self._poll_seconds = max(0.5, settings.paddleocr_api_poll_seconds)
        self._timeout_seconds = max(1.0, settings.paddleocr_api_timeout_seconds)
        self._request_retries = max(0, settings.paddleocr_api_request_retries)
        self._retry_backoff_seconds = max(0.1, settings.paddleocr_api_retry_backoff_seconds)

    def ocr_image(self, image_path: Path) -> dict:
        started = perf_counter()
        if not self._token:
            raise RuntimeError("PADDLEOCR_API_TOKEN is empty; set it in .env or the process environment")
        payload = self._submit_file(image_path)
        job_id = ((payload.get("data") or {}).get("jobId") or "").strip()
        if not job_id:
            raise RuntimeError(f"PaddleOCR API did not return data.jobId: {payload}")
        result_payload = self._wait_for_result(job_id)
        jsonl_url = (((result_payload.get("data") or {}).get("resultUrl") or {}).get("jsonUrl") or "").strip()
        if not jsonl_url:
            raise RuntimeError(f"PaddleOCR API job completed without data.resultUrl.jsonUrl: {result_payload}")
        raw = self._download_jsonl(jsonl_url)
        text = self._extract_jsonl_text(raw)
        return {
            "engine": "paddleocr_api",
            "language_hint": settings.ocr_language_hint,
            "model": self._model,
            "prompt_preset": settings.ocr_paddle_prompt_preset,
            "prompt_override_installed": False,
            "text": text,
            "confidence": 0.0,
            "elapsed_seconds": perf_counter() - started,
            "raw": {
                "job_id": job_id,
                "result_url": jsonl_url,
                "pages": len(raw),
            },
            "error": "",
        }

    def _submit_file(self, image_path: Path) -> dict:
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        data = {
            "model": self._model,
            "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
        }
        with image_path.open("rb") as f:
            response = requests.post(
                self._job_url,
                headers=self._headers(),
                data=data,
                files={"file": (image_path.name, f)},
                timeout=settings.request_timeout,
            )
        return self._json_response(response, "PaddleOCR API job submit failed")

    def _wait_for_result(self, job_id: str) -> dict:
        deadline = time.monotonic() + self._timeout_seconds
        last_payload: dict[str, Any] = {}
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = self._get_with_retries(
                    f"{self._job_url}/{job_id}",
                    headers=self._headers(),
                    operation=f"PaddleOCR API job poll job_id={job_id}",
                )
            except _TransientPaddleOCRRequestError as exc:
                last_error = str(exc)
                time.sleep(self._poll_seconds)
                continue
            payload = self._json_response(response, "PaddleOCR API job poll failed")
            last_payload = payload
            data = payload.get("data") or {}
            state = data.get("state")
            if state == "done":
                return payload
            if state == "failed":
                raise RuntimeError(f"PaddleOCR API job failed: {data.get('errorMsg') or payload}")
            time.sleep(self._poll_seconds)
        detail = f"last_error={last_error}; " if last_error else ""
        raise TimeoutError(
            f"PaddleOCR API job timed out after {self._timeout_seconds:.0f}s: "
            f"job_id={job_id}; {detail}last_payload={last_payload}"
        )

    def _download_jsonl(self, jsonl_url: str) -> list[dict]:
        response = self._get_with_retries(jsonl_url, operation="PaddleOCR API result download")
        response.raise_for_status()
        rows: list[dict] = []
        for line in response.text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def _extract_jsonl_text(self, rows: list[dict]) -> str:
        chunks: list[str] = []
        for row in rows:
            result = row.get("result") or {}
            row_has_markdown = False
            for item in result.get("layoutParsingResults") or []:
                markdown = item.get("markdown") or {}
                text = markdown.get("text")
                if text:
                    row_has_markdown = True
                    chunks.append(text)
            if not row_has_markdown:
                fallback = _extract_text(result)
                if fallback:
                    chunks.append(fallback)
        return _clean_extracted_text(chunks)

    def _headers(self) -> dict:
        return {"Authorization": f"bearer {self._token}"}

    def _get_with_retries(self, url: str, *, operation: str, headers: dict | None = None) -> requests.Response:
        attempts = self._request_retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=settings.request_timeout,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if response.status_code not in _PADDLE_TRANSIENT_HTTP_STATUSES:
                    return response
                body = response.text[:500]
                last_error = f"HTTP {response.status_code}; body={body}"

            if attempt < attempts:
                time.sleep(min(self._retry_backoff_seconds * attempt, 8.0))
        raise _TransientPaddleOCRRequestError(f"{operation} failed after {attempts} attempts: {last_error}")

    @staticmethod
    def _json_response(response: requests.Response, message: str) -> dict:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"{message}: HTTP {response.status_code}; body={response.text[:1000]}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{message}: invalid JSON response; body={response.text[:1000]}") from exc


class VLMOCRTranslateProcessor:
    def __init__(self):
        self._qwen = None

    def ocr_image(self, image_path: Path) -> dict:
        started = perf_counter()
        if self._qwen is None:
            from .qwen_client import QwenClient

            self._qwen = QwenClient()
        prompt = (
            "你是图片文字 OCR 与翻译器。请只识别图片中真实可见文字，不要根据场景补写。"
            "如果文字为中文，translation_zh 可以与原文一致；如果是维吾尔语、阿拉伯字母文字或其他语言，"
            "请给出自然中文译文。若没有可见文字，text 和 text_zh 输出空字符串。\n\n"
            "请只输出合法 JSON，字段必须完全一致：\n"
            "{\n"
            '  "text": "识别到的原文，保留换行；没有则为空字符串",\n'
            '  "text_zh": "中文译文；没有可翻译文字则为空字符串",\n'
            '  "language": "zh|ug|mixed|unknown",\n'
            '  "confidence": "high|medium|low"\n'
            "}"
        )
        result = self._qwen.analyze_image(image_path, prompt, max_tokens=512)
        text = str(result.get("text") or result.get("ocr_text") or "").strip()
        text_zh = str(result.get("text_zh") or result.get("translation_zh") or "").strip()
        language = str(result.get("language") or settings.ocr_language_hint or "unknown").strip().lower()
        confidence_label = str(result.get("confidence") or "medium").strip().lower()
        confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(confidence_label, 0.6)
        return {
            "engine": "vlm_ocr_translate",
            "language": language,
            "language_hint": settings.ocr_language_hint,
            "text": text,
            "text_zh": text_zh,
            "confidence": confidence,
            "confidence_label": confidence_label,
            "translation": {
                "translated": bool(text_zh and text_zh != text),
                "provider": "vlm_ocr_translate",
                "text": text_zh,
            } if text_zh else {},
            "elapsed_seconds": perf_counter() - started,
            "raw": result,
            "error": "",
        }


class VideoOCRTracker:
    def __init__(self, translator=None):
        self.remote = RemoteInferenceClient()
        self.translator = translator
        self.local_processor = PaddleOCRVLProcessor()
        self.vlm_processor = VLMOCRTranslateProcessor()
        self._cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()

    def scan_image(
        self,
        image_path: Path,
        *,
        timestamp: float | None = None,
        frame_num: int | None = None,
        state_id: str = "frame_ocr",
        log: Callable[[str], None] | None = None,
    ) -> dict:
        if not settings.ocr_enabled:
            return {
                "text": "",
                "text_zh": "",
                "translation": {},
                "confidence": 0.0,
                "engine": settings.ocr_engine,
                "language_hint": settings.ocr_language_hint,
                "error": "",
                "enabled": False,
            }
        if settings.use_remote_ocr and not self.remote.ocr_enabled:
            return {
                "text": "",
                "text_zh": "",
                "translation": {},
                "confidence": 0.0,
                "engine": settings.ocr_engine,
                "language_hint": settings.ocr_language_hint,
                "error": "USE_REMOTE_OCR=true but REMOTE_OCR_BASE_URL/REMOTE_INFERENCE_BASE_URL is empty",
                "enabled": True,
            }
        return self._ocr_state(
            state_id=state_id,
            frame_path=image_path,
            frame_hash="",
            timestamp=float(timestamp or 0.0),
            frame_num=int(frame_num or 0),
            log=log,
        )

    def scan_video(
        self,
        video_path: Path,
        output_dir: Path,
        log: Callable[[str], None] | None = None,
    ) -> dict:
        metrics = {
            "enabled": settings.ocr_enabled,
            "sampled_frames": 0,
            "ocr_calls": 0,
            "deduped_samples": 0,
            "translation_calls": 0,
            "errors": 0,
        }
        track = {"states": [], "metrics": metrics}
        if not settings.ocr_enabled:
            return track
        if settings.use_remote_ocr and not self.remote.ocr_enabled:
            metrics["errors"] += 1
            track["error"] = "USE_REMOTE_OCR=true but REMOTE_OCR_BASE_URL/REMOTE_INFERENCE_BASE_URL is empty"
            return track
        _load_cv_deps()
        output_dir.mkdir(parents=True, exist_ok=True)
        frames_dir = output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            metrics["errors"] += 1
            track["error"] = f"cannot open video: {video_path}"
            return track

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total_frames <= 0:
            cap.release()
            return track

        duration = total_frames / fps
        sample_fps = max(0.1, settings.ocr_sample_fps)
        sample_count = int(duration * sample_fps) + 1
        if settings.ocr_max_samples > 0:
            sample_count = min(sample_count, settings.ocr_max_samples)

        current_state: dict | None = None
        last_frame_num = -1
        for sample_idx in range(sample_count):
            timestamp = min(duration, sample_idx / sample_fps)
            frame_num = min(total_frames - 1, int(round(timestamp * fps)))
            if frame_num == last_frame_num:
                continue
            last_frame_num = frame_num

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ok, frame = cap.read()
            if not ok:
                continue
            metrics["sampled_frames"] += 1
            frame_hash = self._state_hash(frame)

            if current_state and self._hamming(frame_hash, current_state["hash"]) <= settings.ocr_hash_threshold:
                current_state["end_ts"] = timestamp
                current_state["reused_count"] += 1
                metrics["deduped_samples"] += 1
                continue

            if current_state:
                current_state["end_ts"] = max(float(current_state.get("end_ts") or 0), timestamp)

            state_id = f"ocr_{len(track['states']):04d}"
            frame_path = frames_dir / f"{state_id}.png"
            cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            metrics["ocr_calls"] += 1
            state = self._ocr_state(
                state_id=state_id,
                frame_path=frame_path,
                frame_hash=frame_hash,
                timestamp=timestamp,
                frame_num=frame_num,
                log=log,
            )
            if state.get("error"):
                metrics["errors"] += 1
            if state.get("translation"):
                metrics["translation_calls"] += 1
            track["states"].append(state)
            current_state = state

        cap.release()
        return track

    def match_for_timestamp(self, track: dict, timestamp: float) -> tuple[list[dict], str]:
        states = [
            state for state in (track.get("states") or [])
            if (state.get("text") or state.get("text_zh")) and not state.get("error")
        ]
        for state in states:
            start = float(state.get("start_ts") or 0)
            end = float(state.get("end_ts") or start)
            if start <= timestamp <= end:
                return [self._external_ocr(state, timestamp, 0.94)], "matched"

        nearest: tuple[float, dict] | None = None
        for state in states:
            start = float(state.get("start_ts") or 0)
            end = float(state.get("end_ts") or start)
            distance = min(abs(timestamp - start), abs(timestamp - end))
            if nearest is None or distance < nearest[0]:
                nearest = (distance, state)
        tolerance = max(0.75, 1.0 / max(0.1, settings.ocr_sample_fps))
        if nearest and nearest[0] <= tolerance:
            confidence = max(0.1, 1.0 - nearest[0] / tolerance)
            return [self._external_ocr(nearest[1], timestamp, confidence)], "low_confidence"
        return [], "none"

    def _ocr_state(
        self,
        *,
        state_id: str,
        frame_path: Path,
        frame_hash: str,
        timestamp: float,
        frame_num: int,
        log: Callable[[str], None] | None,
    ) -> dict:
        started = perf_counter()
        try:
            cache_key = self._file_hash(frame_path)
            with self._cache_lock:
                cached = copy.deepcopy(self._cache.get(cache_key)) if cache_key else None
            if cached:
                result = cached
                result["elapsed_seconds"] = 0.0
                if log:
                    log(f"OCR {state_id}：复用缓存结果，engine={result.get('engine') or settings.ocr_engine}")
            elif settings.use_remote_ocr:
                if log:
                    log(f"OCR {state_id}：调用远程 OCR 服务 {self.remote.ocr_base_url}")
                result = self.remote.ocr_image(
                    frame_path,
                    language_hint=settings.ocr_language_hint,
                    engine=settings.ocr_engine,
                )
                with self._cache_lock:
                    if cache_key:
                        self._cache[cache_key] = copy.deepcopy(result)
            elif settings.ocr_engine == "vlm_ocr_translate":
                if log:
                    log(f"OCR {state_id}：调用 VLM OCR+翻译")
                result = self.vlm_processor.ocr_image(frame_path)
                with self._cache_lock:
                    if cache_key:
                        self._cache[cache_key] = copy.deepcopy(result)
            else:
                if log:
                    log(f"OCR {state_id}：调用 PaddleOCR API {self.local_processor._job_url}")
                result = self.local_processor.ocr_image(frame_path)
                with self._cache_lock:
                    if cache_key:
                        self._cache[cache_key] = copy.deepcopy(result)
        except Exception as exc:
            result = {
                "text": "",
                "confidence": 0.0,
                "error": str(exc),
                "engine": settings.ocr_engine,
                "language_hint": settings.ocr_language_hint,
            }
        if log:
            raw = result.get("raw") or {}
            job_id = raw.get("job_id")
            engine = result.get("engine") or settings.ocr_engine
            elapsed = result.get("elapsed_seconds") or (perf_counter() - started)
            suffix = f"，job_id={job_id}" if job_id else ""
            if result.get("error"):
                log(f"OCR {state_id}：{engine} 调用失败{suffix}，错误={result['error']}")
            else:
                log(f"OCR {state_id}：{engine} 调用完成{suffix}，耗时={elapsed:.1f}s")
        text = (result.get("text") or "").strip()
        text_zh = (result.get("text_zh") or "").strip()
        state = {
            "state_id": state_id,
            "text": text,
            "text_zh": text_zh,
            "translation": result.get("translation") or {},
            "confidence": result.get("confidence", 0.0),
            "confidence_label": result.get("confidence_label", ""),
            "start_ts": timestamp,
            "end_ts": timestamp,
            "sample_ts": timestamp,
            "sample_frame_number": frame_num,
            "frame_path": str(frame_path),
            "hash": frame_hash,
            "reused_count": 0,
            "engine": result.get("engine") or settings.ocr_engine,
            "language": result.get("language", ""),
            "language_hint": result.get("language_hint") or settings.ocr_language_hint,
            "prompt_preset": result.get("prompt_preset") or settings.ocr_paddle_prompt_preset,
            "prompt_override_installed": result.get("prompt_override_installed", False),
            "elapsed_seconds": result.get("elapsed_seconds") or (perf_counter() - started),
            "error": result.get("error", ""),
        }
        allow_translation_fallback = state["engine"] != "vlm_ocr_translate"
        if text and not state["text_zh"] and settings.ocr_translate and allow_translation_fallback and self.translator is not None:
            translation = self.translator.translate_if_needed(
                text,
                source_language=settings.ocr_language_hint or "ug",
                context="video_ocr",
            )
            state["translation"] = translation
            if translation.get("text"):
                state["text_zh"] = translation["text"]
                if log:
                    log(f"OCR {state_id}：HY-MT 翻译完成，译文长度={len(translation['text'])}")
            elif translation.get("error") and log:
                log(f"OCR {state_id}：HY-MT 翻译失败：{translation['error']}")
        return state

    @staticmethod
    def _file_hash(path: Path) -> str:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ""

    def _state_hash(self, frame) -> str:
        _load_cv_deps()
        height = frame.shape[0]
        top = frame[: max(1, int(height * 0.25)), :]
        bottom = frame[int(height * 0.55) :, :]
        combined = np.vstack([
            cv2.resize(top, (64, 24), interpolation=cv2.INTER_AREA),
            cv2.resize(bottom, (64, 40), interpolation=cv2.INTER_AREA),
        ])
        gray = cv2.cvtColor(combined, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)
        mean = float(small.mean())
        return "".join("1" if value > mean else "0" for value in small.flatten())

    def _hamming(self, left: str, right: str) -> int:
        return sum(a != b for a, b in zip(left, right)) + abs(len(left) - len(right))

    def _external_ocr(self, state: dict, timestamp: float, confidence: float) -> dict:
        return {
            "state_id": state.get("state_id", ""),
            "text": state.get("text", ""),
            "text_zh": state.get("text_zh", ""),
            "start_ts": state.get("start_ts"),
            "end_ts": state.get("end_ts"),
            "sample_ts": state.get("sample_ts"),
            "matched_frame_ts": timestamp,
            "alignment_confidence": round(confidence, 3),
            "confidence": state.get("confidence", 0.0),
            "confidence_label": state.get("confidence_label", ""),
            "frame_path": state.get("frame_path", ""),
            "frame_asset_rel": state.get("frame_asset_rel", ""),
            "engine": state.get("engine", ""),
            "language": state.get("language", ""),
        }
