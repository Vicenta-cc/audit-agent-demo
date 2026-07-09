from __future__ import annotations

import re
from typing import Any

from .config import settings
from .remote_inference import RemoteInferenceClient


ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")


class TranslationProcessor:
    def __init__(self):
        self.remote = RemoteInferenceClient()
        self._tokenizer = None
        self._model = None
        self._load_error = ""

    def should_translate(self, text: str, language: str = "", *, trust_language_label: bool = True) -> bool:
        if not settings.translation_enabled:
            return False
        if not text or not text.strip():
            return False
        normalized_language = (language or "").lower()
        if trust_language_label and (normalized_language.startswith("ug") or "uyghur" in normalized_language):
            return True
        return self.arabic_script_ratio(text) >= settings.translation_arabic_ratio_threshold

    def arabic_script_ratio(self, text: str) -> float:
        visible = [ch for ch in text if not ch.isspace()]
        if not visible:
            return 0.0
        arabic_count = len(ARABIC_SCRIPT_RE.findall(text))
        return arabic_count / len(visible)

    def looks_like_arabic_script_language(self, text: str) -> bool:
        return self.arabic_script_ratio(text) >= settings.translation_arabic_ratio_threshold

    def translate_if_needed(self, text: str, source_language: str = "", context: str = "") -> dict:
        if not settings.translation_enabled:
            return {"translated": False, "text": "", "reason": "translation disabled"}
        if not self.should_translate(text, source_language):
            return {"translated": False, "text": "", "reason": "translation not needed"}
        return self.translate(
            text=text,
            source_language=source_language or "ug",
            target_language=settings.hymt_target_language,
            context=context,
        )

    def translate(
        self,
        text: str,
        source_language: str = "ug",
        target_language: str = "中文",
        context: str = "",
    ) -> dict:
        if settings.use_remote_translation:
            if not self.remote.translation_enabled:
                return {"translated": False, "text": "", "error": "USE_REMOTE_TRANSLATION=true but REMOTE_TRANSLATION_BASE_URL/REMOTE_INFERENCE_BASE_URL is empty"}
            result = self.remote.translate(
                text=text,
                source_language=source_language,
                target_language=target_language,
                context=context,
            )
            result.setdefault("translated", bool(result.get("text")))
            return result

        try:
            translated_text = self._translate_local(text, target_language)
        except Exception as exc:
            return {"translated": False, "text": "", "error": str(exc)}
        return {
            "translated": bool(translated_text),
            "provider": "hymt",
            "model": settings.hymt_model,
            "source_language": source_language,
            "target_language": target_language,
            "text": translated_text,
        }

    def _translate_local(self, text: str, target_language: str) -> str:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if self._tokenizer is None or self._model is None:
            self._tokenizer = AutoTokenizer.from_pretrained(settings.hymt_model)
            self._model = AutoModelForCausalLM.from_pretrained(
                settings.hymt_model,
                device_map=settings.hymt_device_map,
                torch_dtype=self._torch_dtype(torch),
            )
            self._model.eval()

        prompt = (
            f"请将以下维吾尔语 ASR/OCR 识别文本翻译为通顺自然的{target_language}。"
            "如果原文存在少量识别错误，请根据上下文尽量还原含义；"
            "只输出译文，不要解释，不要输出原文：\n\n"
            f"{text.strip()}"
        )
        messages = [{"role": "user", "content": prompt}]
        input_ids = self._tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=False,
            return_tensors="pt",
        )
        input_ids = input_ids.to(self._model.device)
        do_sample = settings.hymt_temperature > 0
        generate_kwargs: dict[str, Any] = {
            "max_new_tokens": settings.hymt_max_new_tokens,
            "repetition_penalty": settings.hymt_repetition_penalty,
            "do_sample": do_sample,
            "eos_token_id": getattr(self._tokenizer, "eos_token_id", None),
            "pad_token_id": getattr(self._tokenizer, "pad_token_id", None),
        }
        if do_sample:
            generate_kwargs.update({
                "temperature": settings.hymt_temperature,
                "top_p": settings.hymt_top_p,
                "top_k": settings.hymt_top_k,
            })
        with torch.inference_mode():
            outputs = self._model.generate(input_ids, **generate_kwargs)
        new_tokens = outputs[0][input_ids.shape[-1] :]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    def _torch_dtype(self, torch):
        if settings.hymt_dtype == "auto":
            return "auto"
        return {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }.get(settings.hymt_dtype, torch.bfloat16)
