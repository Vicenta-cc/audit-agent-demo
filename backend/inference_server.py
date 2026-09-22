from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from .audit_agent.config import settings
from .audit_agent.asr_chunks import is_asr_oom
from .audit_agent.prompts import IMAGE_PROMPT


settings.use_remote_asr = False
settings.use_remote_whisper = False
settings.use_remote_vlm = False
settings.use_remote_llm = False
settings.use_remote_translation = False
settings.use_remote_mms_asr = False

app = FastAPI(title="XHS Audit Remote Inference")
logger = logging.getLogger(__name__)
qwen_client = None
audio_processor = None
mms_audio_processor = None
translator = None
ocr_processor = None
vlm_ocr_processor = None


class AuditTextRequest(BaseModel):
    prompt: str
    max_tokens: int | None = None
    model: str | None = None
    enable_thinking: bool | None = None
    request_timeout: int | float | None = None


class TranslateRequest(BaseModel):
    text: str
    source_language: str = "ug"
    target_language: str = "中文"
    context: str = ""


def get_qwen_client():
    global qwen_client
    if qwen_client is None:
        from .audit_agent.qwen_client import QwenClient

        qwen_client = QwenClient()
    return qwen_client


def get_audio_processor():
    global audio_processor
    if audio_processor is None:
        from .audit_agent.video_processor import DemoAudioProcessor

        audio_processor = DemoAudioProcessor()
    return audio_processor


def get_mms_audio_processor():
    global mms_audio_processor
    if mms_audio_processor is None:
        from .audit_agent.video_processor import MMSAudioProcessor

        mms_audio_processor = MMSAudioProcessor()
    return mms_audio_processor


def get_translator():
    global translator
    if translator is None:
        from .audit_agent.translation import TranslationProcessor

        translator = TranslationProcessor()
    return translator


def get_ocr_processor():
    global ocr_processor
    if ocr_processor is None:
        from .audit_agent.ocr_processor import PaddleOCRVLProcessor

        ocr_processor = PaddleOCRVLProcessor()
    return ocr_processor


def get_vlm_ocr_processor():
    global vlm_ocr_processor
    if vlm_ocr_processor is None:
        from .audit_agent.ocr_processor import VLMOCRTranslateProcessor

        vlm_ocr_processor = VLMOCRTranslateProcessor()
    return vlm_ocr_processor


def require_key(x_inference_key: str | None = Header(default=None)) -> None:
    if settings.inference_api_key and x_inference_key != settings.inference_api_key:
        raise HTTPException(status_code=401, detail="Invalid inference key")


@app.get("/api/inference/health")
def health():
    return {
        "ok": True,
        "asr_engine": settings.asr_engine,
        "asr_language": settings.asr_language,
        "asr_device": settings.asr_device,
        "whisper_model": settings.whisper_model,
        "dolphin_model": settings.dolphin_model,
        "dolphin_model_dir": settings.dolphin_model_dir,
        "dolphin_word_timestamp": settings.dolphin_word_timestamp,
        "dolphin_predict_time": settings.dolphin_predict_time,
        "mms_model": settings.mms_model,
        "mms_target_lang": settings.mms_target_lang,
        "mms_local_files_only": settings.mms_local_files_only,
        "hymt_model": settings.hymt_model,
        "qwen_text_model": settings.qwen_text_model,
        "qwen_vl_model": settings.qwen_vl_model,
        "qwen_image_audit_model": settings.qwen_image_audit_model,
        "ocr_engine": settings.ocr_engine,
        "ocr_language_hint": settings.ocr_language_hint,
        "ocr_paddle_device": settings.ocr_paddle_device,
        "ocr_paddle_pipeline_version": settings.ocr_paddle_pipeline_version,
        "ocr_paddle_prompt_label": settings.ocr_paddle_prompt_label,
        "ocr_paddle_prompt_preset": settings.ocr_paddle_prompt_preset,
        "ocr_paddle_prompt_compose": settings.ocr_paddle_prompt_compose,
    }


@app.post("/api/inference/transcribe")
async def transcribe(audio: UploadFile = File(...), x_inference_key: str | None = Header(default=None)):
    require_key(x_inference_key)
    suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = Path(tmp) / f"input{suffix}"
        with audio_path.open("wb") as f:
            while chunk := await audio.read(1024 * 1024):
                f.write(chunk)
        try:
            return get_audio_processor().transcribe(audio_path)
        except Exception as exc:
            logger.exception("remote ASR failed")
            if is_asr_oom(exc):
                raise HTTPException(status_code=422, detail={
                    "code": "asr_gpu_out_of_memory", "retryable": False,
                    "message": "语音转写 GPU 显存不足",
                    "subdivision_exhausted": bool(getattr(exc, "subdivision_exhausted", False)),
                }) from exc
            raise HTTPException(status_code=500, detail=f"remote ASR failed: {exc}") from exc


@app.post("/api/inference/mms-transcribe")
async def mms_transcribe(audio: UploadFile = File(...), x_inference_key: str | None = Header(default=None)):
    require_key(x_inference_key)
    suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = Path(tmp) / f"input{suffix}"
        with audio_path.open("wb") as f:
            while chunk := await audio.read(1024 * 1024):
                f.write(chunk)
        try:
            return get_mms_audio_processor().transcribe_local(audio_path)
        except Exception as exc:
            logger.exception("remote MMS ASR failed")
            raise HTTPException(status_code=500, detail=f"remote MMS ASR failed: {exc}") from exc


@app.post("/api/inference/analyze-image")
async def analyze_image(
    image: UploadFile = File(...),
    prompt: str = Form(default=IMAGE_PROMPT),
    model: str | None = Form(default=None),
    compress_image: bool = Form(default=True),
    image_max_side: int | None = Form(default=None),
    image_quality: int | None = Form(default=None),
    max_tokens: int | None = Form(default=None),
    enable_thinking: bool | None = Form(default=None),
    x_inference_key: str | None = Header(default=None),
):
    require_key(x_inference_key)
    suffix = Path(image.filename or "image.jpg").suffix or ".jpg"
    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / f"input{suffix}"
        image_path.write_bytes(await image.read())
        try:
            return get_qwen_client().analyze_image(
                image_path,
                prompt,
                model=model,
                compress_image=compress_image,
                image_max_side=image_max_side,
                image_quality=image_quality,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )
        except Exception as exc:
            logger.exception("remote VLM failed")
            raise HTTPException(status_code=500, detail=f"remote VLM failed: {exc}") from exc


@app.post("/api/inference/audit-text")
def audit_text(request: AuditTextRequest, x_inference_key: str | None = Header(default=None)):
    require_key(x_inference_key)
    try:
        return get_qwen_client().audit_text(
            request.prompt,
            max_tokens=request.max_tokens,
            model=request.model,
            enable_thinking=request.enable_thinking,
            request_timeout=request.request_timeout,
        )
    except Exception as exc:
        logger.exception("remote LLM failed")
        raise HTTPException(status_code=500, detail=f"remote LLM failed: {exc}") from exc


@app.post("/api/inference/ocr")
async def ocr(
    image: UploadFile = File(...),
    engine: str = Form(default="vlm_ocr_translate"),
    language_hint: str = Form(default="ug"),
    x_inference_key: str | None = Header(default=None),
):
    require_key(x_inference_key)
    if engine not in {"paddleocr_vl", "vlm_ocr_translate"}:
        raise HTTPException(status_code=400, detail=f"Unsupported OCR engine: {engine}")
    suffix = Path(image.filename or "image.png").suffix or ".png"
    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / f"input{suffix}"
        image_path.write_bytes(await image.read())
        try:
            if engine == "vlm_ocr_translate":
                result = get_vlm_ocr_processor().ocr_image(image_path)
            else:
                result = get_ocr_processor().ocr_image(image_path)
            result["language_hint"] = language_hint
            return result
        except Exception as exc:
            logger.exception("remote OCR failed")
            raise HTTPException(status_code=500, detail=f"remote OCR failed: {exc}") from exc


@app.post("/api/inference/translate")
def translate(request: TranslateRequest, x_inference_key: str | None = Header(default=None)):
    require_key(x_inference_key)
    try:
        return get_translator().translate(
            text=request.text,
            source_language=request.source_language,
            target_language=request.target_language,
            context=request.context,
        )
    except Exception as exc:
        logger.exception("remote translation failed")
        raise HTTPException(status_code=500, detail=f"remote translation failed: {exc}") from exc
