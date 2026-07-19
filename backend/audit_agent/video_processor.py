from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from time import perf_counter

from .config import settings
from .remote_inference import RemoteInferenceClient

cv2 = None
np = None


def _load_frame_deps() -> None:
    global cv2, np
    if cv2 is None or np is None:
        import cv2 as cv2_module
        import numpy as np_module

        cv2 = cv2_module
        np = np_module


class DemoAudioProcessor:
    def __init__(self, model_size: str | None = None):
        self.engine = settings.asr_engine
        self.model_size = model_size or settings.whisper_model
        self._model = None
        self.device = settings.whisper_device
        self.compute_type = settings.whisper_compute_type
        self._load_error = ""
        self.last_extract_error = ""
        self.remote = RemoteInferenceClient()

    def extract_audio(self, video_path: Path, output_dir: Path) -> Path | None:
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / "audio.wav"
        self.last_extract_error = ""

        ffmpeg_path = self._resolve_ffmpeg()
        if not ffmpeg_path:
            self.last_extract_error = "ffmpeg not found; install ffmpeg or set FFMPEG_PATH"
            return None
        if not video_path.exists():
            self.last_extract_error = f"video file not found: {video_path}"
            return None

        try:
            completed = subprocess.run(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-y",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode != 0:
                details = (completed.stderr or completed.stdout or "").strip()
                self.last_extract_error = self._compact_error(
                    f"ffmpeg exited with code {completed.returncode}: {details}"
                )
                return None
            if not audio_path.exists() or audio_path.stat().st_size == 0:
                self.last_extract_error = "ffmpeg produced no audio; the video may not contain an audio track"
                return None
            return audio_path
        except FileNotFoundError:
            self.last_extract_error = f"ffmpeg command not found: {ffmpeg_path}"
            return None
        except Exception as exc:
            self.last_extract_error = self._compact_error(str(exc))
            return None

    def _resolve_ffmpeg(self) -> str:
        configured = settings.ffmpeg_path
        if configured:
            configured_path = Path(configured).expanduser()
            if configured_path.exists():
                return str(configured_path)
            found = shutil.which(configured)
            if found:
                return found

        found = shutil.which("ffmpeg")
        if found:
            return found

        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
            if Path(candidate).exists():
                return candidate
        return ""

    def _compact_error(self, message: str, limit: int = 800) -> str:
        message = " ".join((message or "").split())
        if len(message) <= limit:
            return message
        return "..." + message[-limit:]

    def transcribe(self, audio_path: Path) -> dict:
        if settings.use_remote_asr:
            if not self.remote.asr_enabled:
                return {"text": "", "segments": [], "error": "USE_REMOTE_ASR=true but REMOTE_ASR_BASE_URL/REMOTE_INFERENCE_BASE_URL is empty"}
            return self.remote.transcribe(audio_path)

        if self.engine == "dolphin":
            return self._transcribe_dolphin(audio_path)
        if self.engine != "whisper":
            return {"text": "", "segments": [], "error": f"Unsupported ASR_ENGINE: {self.engine}"}
        return self._transcribe_whisper(audio_path)

    def _transcribe_whisper(self, audio_path: Path) -> dict:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            return {"text": "", "segments": [], "error": f"faster-whisper unavailable: {exc}"}

        if self._model is None:
            self._model = self._load_model(WhisperModel)
        segments, info = self._model.transcribe(
            str(audio_path),
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            language=settings.asr_language or "zh",
        )
        segment_list = list(segments)
        result = {
            "provider": "whisper",
            "asr_engine": "whisper",
            "text": " ".join(seg.text for seg in segment_list),
            "language": info.language,
            "device": self.device if not self._load_error else "cpu",
            "compute_type": self.compute_type if not self._load_error else "float32",
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "words": [
                        {
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability,
                        }
                        for word in (seg.words or [])
                    ],
                }
                for seg in segment_list
            ],
        }
        if self._load_error:
            result["fallback_reason"] = self._load_error
        return result

    def _transcribe_dolphin(self, audio_path: Path) -> dict:
        try:
            import dolphin
            import torch
            from dolphin import transcribe
        except Exception as exc:
            return {"text": "", "segments": [], "error": f"Dolphin unavailable: {exc}"}

        if settings.dolphin_audio_loader == "soundfile":
            self._patch_torchaudio_load_with_soundfile()

        if self._model is None:
            model_dir = settings.dolphin_model_dir or None
            if model_dir:
                Path(model_dir).mkdir(parents=True, exist_ok=True)
                try:
                    self._model = dolphin.load_model(settings.dolphin_model, model_dir, settings.asr_device)
                except TypeError:
                    self._model = dolphin.load_model(settings.dolphin_model, device=settings.asr_device)
            else:
                self._model = dolphin.load_model(settings.dolphin_model, device=settings.asr_device)

        lang_sym = settings.dolphin_lang_sym or settings.asr_language or "auto"
        region_sym = settings.dolphin_region_sym or ""
        decode_kwargs = {}
        if lang_sym and lang_sym.lower() != "auto":
            decode_kwargs["lang_sym"] = lang_sym
            if region_sym and region_sym.upper() != "AUTO":
                decode_kwargs["region_sym"] = region_sym
        decode_kwargs["predict_time"] = settings.dolphin_predict_time
        decode_kwargs["word_timestamp"] = settings.dolphin_word_timestamp
        timestamp_warning = ""
        try:
            with torch.inference_mode():
                result = transcribe(self._model, str(audio_path), **decode_kwargs)
        except TypeError as exc:
            if "predict_time" not in str(exc) and "word_timestamp" not in str(exc):
                raise
            timestamp_warning = f"Dolphin timestamp options unsupported by installed package: {exc}"
            decode_kwargs.pop("predict_time", None)
            decode_kwargs.pop("word_timestamp", None)
            with torch.inference_mode():
                result = transcribe(self._model, str(audio_path), **decode_kwargs)
        payload = self._serializable(result)
        text = self._extract_dolphin_text(result)
        output = {
            "provider": "dolphin",
            "asr_engine": "dolphin",
            "text": text,
            "language": self._payload_value(payload, ("lang", "language", "lang_sym")) or lang_sym,
            "region": self._payload_value(payload, ("region", "region_sym")) or region_sym,
            "device": settings.asr_device,
            "model": settings.dolphin_model,
            "model_dir": settings.dolphin_model_dir,
            "predict_time": settings.dolphin_predict_time,
            "word_timestamp": settings.dolphin_word_timestamp,
            "segments": self._dolphin_segments(payload),
            "raw": payload,
        }
        if timestamp_warning:
            output["timestamp_warning"] = timestamp_warning
        return output

    def _patch_torchaudio_load_with_soundfile(self) -> None:
        try:
            import soundfile as sf
            import torch
            import torchaudio
        except Exception:
            return

        def load(path: str | Path, *args, **kwargs):
            data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
            waveform = torch.from_numpy(data.T.copy())
            return waveform, sample_rate

        torchaudio.load = load

    def _extract_dolphin_text(self, result) -> str:
        if isinstance(result, list):
            chunks = []
            for item in result:
                if hasattr(item, "text_nospecial"):
                    chunks.append(str(getattr(item, "text_nospecial", "") or "").strip())
                else:
                    chunks.append(str(getattr(item, "text", "") or str(item)).strip())
            return "\n".join(chunk for chunk in chunks if chunk)
        if hasattr(result, "text_nospecial"):
            return str(getattr(result, "text_nospecial", "") or "").strip()
        return (
            str(getattr(result, "text", "") or str(result)).strip()
        )

    def _serializable(self, value):
        from dataclasses import asdict, is_dataclass

        if is_dataclass(value):
            return self._serializable(asdict(value))
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(k): self._serializable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serializable(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._serializable(value.model_dump())
        if hasattr(value, "__dict__"):
            return self._serializable(vars(value))
        return str(value)

    def _dolphin_segments(self, payload) -> list[dict]:
        items = payload if isinstance(payload, list) else [payload]
        segments = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = (
                item.get("text_nospecial")
                if "text_nospecial" in item
                else item.get("text") or item.get("sentence") or ""
            )
            text = str(text or "").strip()
            word_segments = self._dolphin_segments_from_words(text, item.get("word_timestamps"))
            if word_segments and not any(key in item for key in ("start", "end", "start_time", "end_time")):
                segments.extend(word_segments)
                continue
            if not text:
                continue
            segment = {"text": text}
            for source_key, target_key in (("start", "start"), ("end", "end"), ("start_time", "start"), ("end_time", "end")):
                if source_key in item:
                    segment[target_key] = item[source_key]
            segments.append(segment)
        return segments

    def _dolphin_segments_from_words(self, text: str, word_timestamps) -> list[dict]:
        words = self._normalize_dolphin_words(word_timestamps)
        if not words:
            return []

        segments = []
        current = []
        max_gap_seconds = settings.asr_segment_max_gap_seconds
        max_segment_seconds = settings.asr_segment_max_seconds
        max_words = settings.asr_segment_max_tokens

        def flush() -> None:
            if not current:
                return
            segment_words = [dict(word) for word in current]
            segment_text = self._join_dolphin_words([word.get("word", "") for word in segment_words])
            segments.append({
                "start": segment_words[0]["start"],
                "end": segment_words[-1]["end"],
                "text": segment_text,
            })
            current.clear()

        for word in words:
            if current:
                gap = word["start"] - current[-1]["end"]
                duration = word["end"] - current[0]["start"]
                if gap > max_gap_seconds or duration > max_segment_seconds or len(current) >= max_words:
                    flush()
            current.append(word)
        flush()

        if len(segments) == 1 and text and not segments[0].get("text"):
            segments[0]["text"] = text
        return segments

    @staticmethod
    def _normalize_dolphin_words(word_timestamps) -> list[dict]:
        if not isinstance(word_timestamps, list):
            return []
        words = []
        for item in word_timestamps:
            if not isinstance(item, dict):
                continue
            raw_word = item.get("word") or item.get("text") or item.get("token") or item.get("char") or ""
            try:
                start = float(item.get("start", item.get("start_time")))
                end = float(item.get("end", item.get("end_time")))
            except (TypeError, ValueError):
                continue
            if end < start:
                end = start
            words.append({
                "word": str(raw_word),
                "start": start,
                "end": end,
                **({
                    "probability": item.get("probability")
                } if item.get("probability") is not None else {}),
            })
        return words

    @staticmethod
    def _join_dolphin_words(words: list[str]) -> str:
        clean_words = [str(word).strip() for word in words if str(word).strip()]
        if not clean_words:
            return ""
        cjk_chars = sum(
            1
            for word in clean_words
            for char in word
            if "\u4e00" <= char <= "\u9fff"
        )
        total_chars = sum(len(word) for word in clean_words)
        if total_chars and cjk_chars / total_chars > 0.6:
            return "".join(clean_words)
        return " ".join(clean_words)

    def _payload_value(self, payload, keys: tuple[str, ...]) -> str:
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = item.get(key)
                if value:
                    return str(value)
        return ""

    def _load_model(self, whisper_model_cls):
        try:
            return whisper_model_cls(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            self._load_error = f"load {self.device}/{self.compute_type} failed: {exc}"
            if not settings.whisper_cpu_fallback or self.device == "cpu":
                raise
            return whisper_model_cls(self.model_size, device="cpu", compute_type="float32")


class MMSAudioProcessor:
    def __init__(self):
        self.remote = RemoteInferenceClient()
        self._processor = None
        self._model = None
        self._pipeline = None
        self._device_name = ""
        self._dtype_name = ""

    def transcribe(self, audio_path: Path) -> dict:
        if settings.use_remote_mms_asr:
            if not self.remote.mms_asr_enabled:
                return {
                    "text": "",
                    "segments": [],
                    "error": "USE_REMOTE_MMS_ASR=true but REMOTE_MMS_ASR_BASE_URL is empty",
                    "provider": "mms",
                    "asr_engine": "mms",
                }
            return self.remote.mms_transcribe(audio_path)
        return self.transcribe_local(audio_path)

    def transcribe_local(self, audio_path: Path) -> dict:
        started = perf_counter()
        if settings.mms_hf_endpoint:
            os.environ["HF_ENDPOINT"] = settings.mms_hf_endpoint
        try:
            import soundfile as sf
            import torch
            from transformers import AutoModelForCTC, AutoProcessor, pipeline
        except Exception as exc:
            return {
                "text": "",
                "segments": [],
                "error": f"MMS dependencies unavailable: {exc}",
                "provider": "mms",
                "asr_engine": "mms",
            }

        if self._pipeline is None:
            device_name = self._pick_device(torch)
            torch_dtype = self._pick_dtype(torch, device_name)
            self._device_name = device_name
            self._dtype_name = str(torch_dtype).replace("torch.", "")
            self._processor = AutoProcessor.from_pretrained(
                settings.mms_model,
                target_lang=settings.mms_target_lang,
                local_files_only=settings.mms_local_files_only,
            )
            self._model = AutoModelForCTC.from_pretrained(
                settings.mms_model,
                target_lang=settings.mms_target_lang,
                ignore_mismatched_sizes=True,
                torch_dtype=torch_dtype,
                low_cpu_mem_usage=True,
                local_files_only=settings.mms_local_files_only,
            )
            self._model.to(device_name)
            self._model.eval()
            self._pipeline = pipeline(
                "automatic-speech-recognition",
                model=self._model,
                tokenizer=self._processor.tokenizer,
                feature_extractor=self._processor.feature_extractor,
                torch_dtype=torch_dtype,
                device=0 if device_name == "cuda" else -1,
            )

        data, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        audio_input = {"array": data, "sampling_rate": sample_rate}
        kwargs: dict = {"batch_size": settings.mms_batch_size}
        if settings.mms_chunk_length_s > 0:
            kwargs["chunk_length_s"] = settings.mms_chunk_length_s
        if settings.mms_stride_length_s > 0:
            kwargs["stride_length_s"] = settings.mms_stride_length_s
        if settings.mms_return_timestamps:
            kwargs["return_timestamps"] = "word"

        try:
            result = self._pipeline(audio_input, **kwargs)
        except TypeError as exc:
            if "return_timestamps" not in str(exc):
                raise
            kwargs.pop("return_timestamps", None)
            result = self._pipeline(audio_input, **kwargs)

        payload = DemoAudioProcessor()._serializable(result)
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        chunks = payload.get("chunks", []) if isinstance(payload, dict) else []
        return {
            "provider": "mms",
            "asr_engine": "mms",
            "text": str(text or "").strip(),
            "language": "ug",
            "model": settings.mms_model,
            "target_lang": settings.mms_target_lang,
            "device": self._device_name,
            "dtype": self._dtype_name,
            "chunks": chunks,
            "elapsed_seconds": perf_counter() - started,
            "raw": payload,
        }

    @staticmethod
    def _pick_device(torch) -> str:
        requested = (settings.mms_device or "auto").lower()
        if requested == "cuda":
            return "cuda"
        if requested == "cpu":
            return "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"

    @staticmethod
    def _pick_dtype(torch, device: str):
        requested = (settings.mms_dtype or "auto").lower()
        if requested == "float16":
            return torch.float16
        if requested == "bfloat16":
            return torch.bfloat16
        if requested == "float32":
            return torch.float32
        return torch.float16 if device == "cuda" else torch.float32


class DemoFrameExtractor:
    threshold = 10.0

    def extract_keyframes(self, video_path: Path, output_dir: Path, max_frames: int) -> list[dict]:
        return self.extract_timeline_frames(video_path, output_dir, max_frames)

    def extract_timeline_frames(self, video_path: Path, output_dir: Path, max_frames: int) -> list[dict]:
        _load_frame_deps()
        output_dir.mkdir(parents=True, exist_ok=True)
        fps, total_frames, duration = self._video_meta(video_path)
        if total_frames <= 0:
            return []

        candidates = self._extract_ffmpeg_candidates(
            video_path=video_path,
            output_dir=output_dir / "candidates",
            fps=fps,
            scene_threshold=settings.video_scene_threshold,
            fps_floor_seconds=settings.video_fps_floor_seconds,
        )
        if not candidates:
            candidates = self._extract_cv2_candidates(
                video_path=video_path,
                output_dir=output_dir / "candidates",
                fps=fps,
                total_frames=total_frames,
                scene_threshold=settings.video_scene_threshold,
                fps_floor_seconds=settings.video_fps_floor_seconds,
            )

        candidates.sort(key=lambda item: (float(item.get("timestamp") or 0.0), int(item.get("frame_number") or 0)))
        deduped = self._dedupe_frame_files(
            candidates,
            threshold=settings.video_dedup_threshold,
            window=settings.video_dedup_window,
        )
        deduped = self._ensure_boundary_candidates(candidates, deduped)
        selected = self._thin_by_time(deduped, max_frames)

        frames: list[dict] = []
        for idx, item in enumerate(selected):
            frame_id = f"f{idx + 1:04d}"
            target = output_dir / f"{frame_id}.png"
            source = Path(str(item["path"]))
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            frames.append({
                "index": idx,
                "frame_id": frame_id,
                "path": str(target),
                "timestamp": float(item.get("timestamp") or 0.0),
                "frame_number": int(item.get("frame_number") or round(float(item.get("timestamp") or 0.0) * fps)),
                "score": float(item.get("score") or 0.0),
                "dedup_dist": item.get("dedup_dist"),
                "kind": "timeline_frame",
            })

        (output_dir / "timeline_index.json").write_text(
            json.dumps(
                {
                    "fps": fps,
                    "duration": duration,
                    "scene_threshold": settings.video_scene_threshold,
                    "fps_floor_seconds": settings.video_fps_floor_seconds,
                    "candidate_count": len(candidates),
                    "deduped_count": len(deduped),
                    "selected_count": len(frames),
                    "frames": frames,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return frames

    def video_meta(self, video_path: Path) -> tuple[float, int, float]:
        return self._video_meta(video_path)

    @staticmethod
    def _ensure_boundary_candidates(candidates: list[dict], selected: list[dict]) -> list[dict]:
        if not candidates:
            return selected
        by_path = {str(item.get("path") or ""): item for item in selected}
        for boundary in (candidates[0], candidates[-1]):
            key = str(boundary.get("path") or "")
            if key and key not in by_path:
                by_path[key] = boundary
        return sorted(
            by_path.values(),
            key=lambda item: (float(item.get("timestamp") or 0.0), int(item.get("frame_number") or 0)),
        )

    def _extract_cv2_candidates(
        self,
        video_path: Path,
        output_dir: Path,
        fps: float,
        total_frames: int,
        scene_threshold: float,
        fps_floor_seconds: float,
    ) -> list[dict]:
        output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        floor_interval = max(1, int(round(fps * max(0.1, fps_floor_seconds))))
        candidates = []
        prev = None
        frame_num = 0
        last_selected = -floor_interval
        while frame_num < total_frames:
            ok, frame = cap.read()
            if not ok:
                break
            score = self._diff(frame, prev)
            should_keep = prev is None or score >= scene_threshold * 100 or frame_num - last_selected >= floor_interval
            if should_keep:
                path = output_dir / f"candidate_{len(candidates) + 1:05d}.png"
                cv2.imwrite(str(path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
                candidates.append({
                    "path": str(path),
                    "timestamp": frame_num / fps,
                    "frame_number": frame_num,
                    "score": float(score),
                    "source": "cv2_scene_or_floor",
                })
                last_selected = frame_num
            prev = frame.copy()
            frame_num += 1
        cap.release()
        return candidates

    def _extract_ffmpeg_candidates(
        self,
        video_path: Path,
        output_dir: Path,
        fps: float,
        scene_threshold: float,
        fps_floor_seconds: float,
    ) -> list[dict]:
        ffmpeg_path = self._resolve_ffmpeg()
        if not ffmpeg_path:
            return []
        output_dir.mkdir(parents=True, exist_ok=True)
        pattern = output_dir / "candidate_%05d.png"
        expr = (
            "select='isnan(prev_selected_t)"
            f"+gt(scene\\,{scene_threshold:.4f})"
            f"+gte(t-prev_selected_t\\,{max(0.1, fps_floor_seconds):.4f})',showinfo"
        )
        try:
            completed = subprocess.run(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-i",
                    str(video_path),
                    "-vf",
                    expr,
                    "-vsync",
                    "vfr",
                    "-y",
                    str(pattern),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return []
        if completed.returncode != 0:
            return []

        timestamps = [
            float(match.group(1))
            for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", completed.stderr or "")
        ]
        files = sorted(output_dir.glob("candidate_*.png"))
        candidates: list[dict] = []
        for idx, path in enumerate(files):
            timestamp = timestamps[idx] if idx < len(timestamps) else idx * max(0.1, fps_floor_seconds)
            candidates.append({
                "path": str(path),
                "timestamp": timestamp,
                "frame_number": int(round(timestamp * fps)),
                "score": 0.0,
                "source": "ffmpeg_scene_or_floor",
            })
        return candidates

    def create_contact_sheet(
        self,
        frames: list[dict],
        output_path: Path,
        *,
        columns: int = 3,
        rows: int = 3,
    ) -> Path:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception as exc:
            raise RuntimeError(f"Pillow unavailable for contact sheet: {exc}") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cell_w, cell_h = 360, 260
        label_h = 34
        sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), (248, 250, 252))
        draw = ImageDraw.Draw(sheet)
        font = self._load_sheet_font(ImageFont)

        for idx in range(columns * rows):
            col = idx % columns
            row = idx // columns
            x = col * cell_w
            y = row * (cell_h + label_h)
            draw.rectangle((x, y, x + cell_w - 1, y + cell_h + label_h - 1), outline=(203, 213, 225), width=1)
            if idx >= len(frames):
                draw.text((x + 12, y + cell_h + 8), "empty", fill=(148, 163, 184), font=font)
                continue
            frame = frames[idx]
            try:
                with Image.open(frame["path"]) as image:
                    image = image.convert("RGB")
                    image.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
                    ox = x + (cell_w - image.width) // 2
                    oy = y + (cell_h - image.height) // 2
                    sheet.paste(image, (ox, oy))
            except Exception:
                draw.text((x + 12, y + 20), "image unavailable", fill=(239, 68, 68), font=font)
            frame_id = str(frame.get("frame_id") or f"f{idx + 1:04d}")
            timestamp = self._format_seconds(float(frame.get("timestamp") or 0.0))
            draw.rectangle((x, y + cell_h, x + cell_w, y + cell_h + label_h), fill=(15, 23, 42))
            draw.text((x + 12, y + cell_h + 8), f"{frame_id}  {timestamp}", fill=(255, 255, 255), font=font)

        sheet.save(output_path, format="JPEG", quality=88)
        return output_path

    def extract_precise_window(
        self,
        video_path: Path,
        output_dir: Path,
        center_timestamp: float,
        sheet_id: str,
        *,
        window_seconds: float,
        frame_count: int,
    ) -> list[dict]:
        _load_frame_deps()
        output_dir.mkdir(parents=True, exist_ok=True)
        fps, total_frames, duration = self._video_meta(video_path)
        if total_frames <= 0:
            return []
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []
        count = max(1, frame_count)
        half = max(0.1, window_seconds) / 2
        start = max(0.0, center_timestamp - half)
        end = min(duration, center_timestamp + half)
        if count == 1:
            timestamps = [min(max(center_timestamp, 0.0), duration)]
        else:
            span = max(0.001, end - start)
            timestamps = [start + span * i / (count - 1) for i in range(count)]

        frames: list[dict] = []
        for idx, timestamp in enumerate(timestamps):
            frame_num = min(max(0, int(round(timestamp * fps))), max(0, total_frames - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ok, frame = cap.read()
            if not ok:
                continue
            path = output_dir / f"{sheet_id}_p{idx + 1:02d}.jpg"
            cv2.imwrite(str(path), frame)
            frames.append({
                "index": idx,
                "frame_id": f"{sheet_id}:p{idx + 1:02d}",
                "path": str(path),
                "timestamp": frame_num / fps,
                "frame_number": frame_num,
                "kind": "precise_frame",
            })
        cap.release()
        return frames

    def _dedupe_frame_files(self, candidates: list[dict], threshold: float, window: int) -> list[dict]:
        deduped: list[dict] = []
        signatures: list = []
        for item in candidates:
            signature = self._image_signature(Path(str(item["path"])))
            if signature is None:
                item["dedup_dist"] = None
                deduped.append(item)
                signatures.append(signature)
                continue
            recent = [sig for sig in signatures[-max(1, window):] if sig is not None]
            distances = [float(np.mean(np.abs(signature.astype("float32") - sig.astype("float32")))) for sig in recent]
            min_dist = min(distances) if distances else 999.0
            item["dedup_dist"] = min_dist
            if not recent or min_dist > threshold:
                deduped.append(item)
                signatures.append(signature)
        return deduped

    def _image_signature(self, path: Path):
        image = cv2.imread(str(path))
        if image is None:
            return None
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        return cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _thin_by_time(frames: list[dict], max_frames: int) -> list[dict]:
        if max_frames <= 0:
            return []
        if len(frames) <= max_frames:
            return frames
        if max_frames == 1:
            return [frames[0]]
        last = len(frames) - 1
        indexes = [round(i * last / (max_frames - 1)) for i in range(max_frames)]
        selected = []
        seen = set()
        for index in indexes:
            if index in seen:
                continue
            seen.add(index)
            selected.append(frames[index])
        return selected

    def _video_meta(self, video_path: Path) -> tuple[float, int, float]:
        _load_frame_deps()
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return 25.0, 0, 0.0
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        duration = total_frames / fps if total_frames > 0 and fps > 0 else 0.0
        return float(fps), total_frames, float(duration)

    def _resolve_ffmpeg(self) -> str:
        configured = settings.ffmpeg_path
        if configured:
            configured_path = Path(configured).expanduser()
            if configured_path.exists():
                return str(configured_path)
            found = shutil.which(configured)
            if found:
                return found
        return shutil.which("ffmpeg") or ""

    @staticmethod
    def _load_sheet_font(image_font_module):
        for path in (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            if Path(path).exists():
                try:
                    return image_font_module.truetype(path, 18)
                except Exception:
                    pass
        return image_font_module.load_default()

    def _write_keyframe_curve_artifacts(
        self,
        output_dir: Path,
        fps: float,
        diffs: list[dict],
        selected_frame_nums: set[int],
    ) -> None:
        if not diffs:
            return
        payload = {
            "fps": fps,
            "diffs": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                }
                for item in diffs
            ],
            "selected_frames": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                }
                for item in diffs
                if item["frame_num"] in selected_frame_nums
            ],
        }
        (output_dir / "keyframe_curve.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_keyframe_curve_png(
            output_dir / "keyframe_curve.png",
            fps=fps,
            diffs=diffs,
            selected_frame_nums=selected_frame_nums,
        )

    def _write_keyframe_curve_png(
        self,
        path: Path,
        fps: float,
        diffs: list[dict],
        selected_frame_nums: set[int],
    ) -> None:
        _load_frame_deps()
        if not diffs:
            return

        width = 1200
        height = 420
        margin_left = 56
        margin_right = 24
        margin_top = 24
        margin_bottom = 44
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        image = np.full((height, width, 3), 255, dtype=np.uint8)
        max_score = max(max(item["score"] for item in diffs), 1.0)

        def point(frame_num: int, score: float) -> tuple[int, int]:
            if len(diffs) <= 1 or diffs[-1]["frame_num"] == diffs[0]["frame_num"]:
                x = margin_left
            else:
                x = margin_left + int((frame_num - diffs[0]["frame_num"]) / (diffs[-1]["frame_num"] - diffs[0]["frame_num"]) * plot_w)
            y = margin_top + plot_h - int(score / max_score * plot_h)
            return x, y

        cv2.rectangle(image, (margin_left, margin_top), (margin_left + plot_w, margin_top + plot_h), (220, 220, 220), 1)
        duration = diffs[-1]["frame_num"] / fps
        tick_step = self._time_tick_step(duration)
        tick = 0.0
        while tick <= duration + 1e-9:
            frame_num = int(round(tick * fps))
            x, _ = point(frame_num, 0)
            cv2.line(image, (x, margin_top + plot_h), (x, margin_top + plot_h + 6), (120, 120, 120), 1)
            cv2.line(image, (x, margin_top), (x, margin_top + plot_h), (238, 238, 238), 1)
            cv2.putText(image, self._format_seconds(tick), (max(margin_left - 12, x - 14), margin_top + plot_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 80, 80), 1, cv2.LINE_AA)
            tick += tick_step

        points = [point(item["frame_num"], item["score"]) for item in diffs]
        for start, end in zip(points, points[1:]):
            cv2.line(image, start, end, (45, 45, 45), 1)

        for item in diffs:
            if item["frame_num"] not in selected_frame_nums:
                continue
            x, y = point(item["frame_num"], item["score"])
            cv2.line(image, (x, margin_top), (x, margin_top + plot_h), (40, 40, 230), 1)
            cv2.circle(image, (x, y), 5, (0, 0, 255), -1)

        cv2.putText(image, "Keyframe diff curve", (margin_left, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1, cv2.LINE_AA)
        cv2.putText(image, "red=selected keyframes", (margin_left + 210, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (70, 70, 70), 1, cv2.LINE_AA)
        cv2.imwrite(str(path), image)

    def detect_flash_spikes(self, video_path: Path, output_dir: Path, max_spikes: int) -> list[dict]:
        """Scan adjacent frame triples for isolated flash insertions."""
        _load_frame_deps()
        output_dir.mkdir(parents=True, exist_ok=True)

        diff_spikes = self._detect_diff_peak_segments(video_path, output_dir, max_spikes)
        if diff_spikes:
            return diff_spikes

        if settings.pyscenedetect_enabled:
            scene_spikes = self._detect_pyscenedetect_short_scenes(video_path, output_dir, max_spikes)
            if scene_spikes:
                return scene_spikes

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        spike_threshold = settings.video_spike_threshold
        return_threshold = max(3.0, spike_threshold * 0.45)
        dedupe_window = max(1, settings.video_spike_max_len)
        short_segment_max_frames = settings.video_short_segment_max_frames
        if short_segment_max_frames <= 0:
            short_segment_max_frames = max(1, int(fps * settings.video_short_segment_max_seconds))

        ok, prev_frame = cap.read()
        if not ok:
            cap.release()
            return []
        ok, cur_frame = cap.read()
        if not ok:
            cap.release()
            return []

        prev_gray = self._gray_small(prev_frame)
        cur_gray = self._gray_small(cur_frame)
        cur_frame_num = 1
        last_spike_frame = -dedupe_window - 1
        spikes: list[dict] = []
        fallback_candidates: list[dict] = []
        current_segment: dict | None = None

        while True:
            ok, next_frame = cap.read()
            if not ok:
                break
            next_gray = self._gray_small(next_frame)

            pc_global, pc_block = self._diff_scores(prev_gray, cur_gray)
            cn_global, cn_block = self._diff_scores(cur_gray, next_gray)
            pn_global, pn_block = self._diff_scores(prev_gray, next_gray)

            pc_spike = max(pc_global, pc_block)
            cn_spike = max(cn_global, cn_block)
            pn_return = max(pn_global, pn_block * 0.5)
            score = min(pc_spike, cn_spike) - pn_return
            fallback_score = min(pc_spike, cn_spike) - pn_return * 0.1

            if pc_spike > 0 or cn_spike > 0:
                fallback_candidates.append({
                    "frame_num": cur_frame_num,
                    "frame": cur_frame.copy(),
                    "score": float(fallback_score),
                    "prev_curr_global": float(pc_global),
                    "curr_next_global": float(cn_global),
                    "prev_next_global": float(pn_global),
                    "prev_curr_block": float(pc_block),
                    "curr_next_block": float(cn_block),
                    "prev_next_block": float(pn_block),
                    "detection_mode": "fallback_candidate",
                })

            if pc_spike >= spike_threshold and current_segment is None:
                current_segment = {
                    "start_frame": cur_frame_num,
                    "best_frame_num": cur_frame_num,
                    "best_frame": cur_frame.copy(),
                    "best_score": float(pc_spike),
                    "prev_curr_global": float(pc_global),
                    "curr_next_global": float(cn_global),
                    "prev_next_global": float(pn_global),
                    "prev_curr_block": float(pc_block),
                    "curr_next_block": float(cn_block),
                    "prev_next_block": float(pn_block),
                }
            elif current_segment is not None:
                segment_score = max(pc_spike, cn_spike)
                if segment_score > current_segment["best_score"]:
                    current_segment.update({
                        "best_frame_num": cur_frame_num,
                        "best_frame": cur_frame.copy(),
                        "best_score": float(segment_score),
                        "prev_curr_global": float(pc_global),
                        "curr_next_global": float(cn_global),
                        "prev_next_global": float(pn_global),
                        "prev_curr_block": float(pc_block),
                        "curr_next_block": float(cn_block),
                        "prev_next_block": float(pn_block),
                    })
                segment_len = cur_frame_num - current_segment["start_frame"] + 1
                if cn_spike >= spike_threshold:
                    if (
                        segment_len <= short_segment_max_frames
                        and cur_frame_num - last_spike_frame > dedupe_window
                    ):
                        spikes.append({
                            "frame_num": current_segment["best_frame_num"],
                            "frame": current_segment["best_frame"],
                            "score": current_segment["best_score"],
                            "prev_curr_global": current_segment["prev_curr_global"],
                            "curr_next_global": current_segment["curr_next_global"],
                            "prev_next_global": current_segment["prev_next_global"],
                            "prev_curr_block": current_segment["prev_curr_block"],
                            "curr_next_block": current_segment["curr_next_block"],
                            "prev_next_block": current_segment["prev_next_block"],
                            "segment_start_frame": current_segment["start_frame"],
                            "segment_end_frame": cur_frame_num,
                            "segment_duration": segment_len / fps,
                            "detection_mode": "short_segment",
                        })
                        last_spike_frame = cur_frame_num
                    current_segment = None
                elif segment_len > short_segment_max_frames:
                    current_segment = None

            if (
                pc_spike >= spike_threshold
                and cn_spike >= spike_threshold
                and pn_return <= return_threshold
                and score > 0
                and cur_frame_num - last_spike_frame > dedupe_window
            ):
                spikes.append({
                    "frame_num": cur_frame_num,
                    "frame": cur_frame.copy(),
                    "score": float(score),
                    "prev_curr_global": float(pc_global),
                    "curr_next_global": float(cn_global),
                    "prev_next_global": float(pn_global),
                    "prev_curr_block": float(pc_block),
                    "curr_next_block": float(cn_block),
                    "prev_next_block": float(pn_block),
                    "detection_mode": "strict_return",
                })
                last_spike_frame = cur_frame_num

            prev_frame, prev_gray = cur_frame, cur_gray
            cur_frame, cur_gray = next_frame, next_gray
            cur_frame_num += 1

        cap.release()

        if not spikes:
            fallback_candidates.sort(key=lambda item: item["score"], reverse=True)
            fallback_limit = max_spikes if max_spikes > 0 else len(fallback_candidates)
            for candidate in fallback_candidates:
                if len(spikes) >= fallback_limit:
                    break
                if any(abs(candidate["frame_num"] - item["frame_num"]) <= dedupe_window for item in spikes):
                    continue
                spikes.append(candidate)

        spikes.sort(key=lambda item: item["score"], reverse=True)
        if max_spikes > 0:
            spikes = spikes[:max_spikes]
        spikes.sort(key=lambda item: item["frame_num"])

        results = []
        for idx, spike in enumerate(spikes):
            path = output_dir / f"spike_{idx:02d}.jpg"
            cv2.imwrite(str(path), spike["frame"])
            results.append({
                "index": idx,
                "path": str(path),
                "timestamp": spike["frame_num"] / fps,
                "frame_number": spike["frame_num"],
                "score": spike["score"],
                "prev_curr_global": spike["prev_curr_global"],
                "curr_next_global": spike["curr_next_global"],
                "prev_next_global": spike["prev_next_global"],
                "prev_curr_block": spike["prev_curr_block"],
                "curr_next_block": spike["curr_next_block"],
                "prev_next_block": spike["prev_next_block"],
                "segment_start_frame": spike.get("segment_start_frame"),
                "segment_end_frame": spike.get("segment_end_frame"),
                "segment_duration": spike.get("segment_duration"),
                "detection_mode": spike.get("detection_mode", "strict_return"),
                "kind": "flash_spike",
            })
        return results

    def _detect_diff_peak_segments(
        self,
        video_path: Path,
        output_dir: Path,
        max_spikes: int,
    ) -> list[dict]:
        _load_frame_deps()
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        threshold = settings.video_spike_threshold
        min_gap = max(1, int(fps * settings.video_diff_pair_min_seconds))
        max_gap = settings.video_short_segment_max_frames
        if max_gap <= 0:
            max_gap = max(1, int(fps * settings.video_short_segment_max_seconds))
        dedupe_window = max(1, settings.video_spike_max_len)
        peak_limit = settings.video_diff_peak_top_k if settings.video_diff_peak_top_k > 0 else max_spikes

        ok, prev_frame = cap.read()
        if not ok:
            cap.release()
            return []

        prev_gray = self._gray_small(prev_frame)
        frame_idx = 1
        diffs: list[dict] = []
        frames_by_num: dict[int, np.ndarray] = {}

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray = self._gray_small(frame)
            global_score, block_score = self._diff_scores(prev_gray, gray)
            score = max(global_score, block_score)
            diffs.append({
                "frame_num": frame_idx,
                "score": float(score),
                "global_score": float(global_score),
                "block_score": float(block_score),
            })
            if score >= threshold:
                frames_by_num[frame_idx] = frame.copy()
            prev_gray = gray
            frame_idx += 1

        cap.release()
        if not diffs:
            return []

        peaks: list[dict] = []
        for i, item in enumerate(diffs):
            prev_score = diffs[i - 1]["score"] if i > 0 else -1.0
            next_score = diffs[i + 1]["score"] if i + 1 < len(diffs) else -1.0
            if item["score"] >= threshold and item["score"] >= prev_score and item["score"] >= next_score:
                peaks.append(item)

        segments: list[dict] = []
        used_frames: set[int] = set()
        for idx, start in enumerate(peaks[:-1]):
            for end in peaks[idx + 1:]:
                gap = end["frame_num"] - start["frame_num"]
                if gap < min_gap:
                    continue
                if gap > max_gap:
                    break

                start_frame = frames_by_num.get(start["frame_num"])
                if start_frame is None:
                    start_frame = self._read_frame(video_path, start["frame_num"])
                end_frame = frames_by_num.get(end["frame_num"])
                if end_frame is None:
                    end_frame = self._read_frame(video_path, end["frame_num"])
                if start_frame is None or end_frame is None:
                    continue

                segments.append({
                    "frame_num": start["frame_num"],
                    "frame": start_frame,
                    "score": float(start["score"] + end["score"]),
                    "peak_start_frame": start["frame_num"],
                    "peak_end_frame": end["frame_num"],
                    "peak_role": "start",
                    "segment_start_frame": start["frame_num"],
                    "segment_end_frame": end["frame_num"],
                    "segment_duration": gap / fps,
                    "prev_curr_global": start["global_score"],
                    "prev_curr_block": start["block_score"],
                    "detection_mode": "short_segment_diff_pair_start",
                })
                segments.append({
                    "frame_num": end["frame_num"],
                    "frame": end_frame,
                    "score": float(start["score"] + end["score"]),
                    "peak_start_frame": start["frame_num"],
                    "peak_end_frame": end["frame_num"],
                    "peak_role": "end",
                    "segment_start_frame": start["frame_num"],
                    "segment_end_frame": end["frame_num"],
                    "segment_duration": gap / fps,
                    "prev_curr_global": end["global_score"],
                    "prev_curr_block": end["block_score"],
                    "detection_mode": "short_segment_diff_pair_end",
                })
                used_frames.add(start["frame_num"])
                used_frames.add(end["frame_num"])
                break

        self._write_diff_debug_artifacts(
            output_dir=output_dir,
            fps=fps,
            threshold=threshold,
            min_gap=min_gap,
            max_gap=max_gap,
            diffs=diffs,
            peaks=peaks,
            segments=segments,
        )

        results_source = segments
        if not results_source:
            peak_candidates = sorted(peaks, key=lambda item: item["score"], reverse=True)
            if peak_limit > 0:
                peak_candidates = peak_candidates[:peak_limit]
            results_source = []
            for peak in peak_candidates:
                if any(abs(peak["frame_num"] - used) <= dedupe_window for used in used_frames):
                    continue
                frame = frames_by_num.get(peak["frame_num"])
                if frame is None:
                    frame = self._read_frame(video_path, peak["frame_num"])
                if frame is None:
                    continue
                results_source.append({
                    "frame_num": peak["frame_num"],
                    "frame": frame,
                    "score": peak["score"],
                    "prev_curr_global": peak["global_score"],
                    "prev_curr_block": peak["block_score"],
                    "detection_mode": "diff_peak",
                })
                used_frames.add(peak["frame_num"])

        results_source.sort(key=lambda item: item["score"], reverse=True)
        if max_spikes > 0:
            results_source = results_source[:max_spikes]
        results_source.sort(key=lambda item: item["frame_num"])

        results = []
        for idx, item in enumerate(results_source):
            path = output_dir / f"diff_{idx:02d}.jpg"
            cv2.imwrite(str(path), item["frame"])
            results.append({
                "index": idx,
                "path": str(path),
                "timestamp": item["frame_num"] / fps,
                "frame_number": item["frame_num"],
                "score": item["score"],
                "prev_curr_global": item.get("prev_curr_global"),
                "prev_curr_block": item.get("prev_curr_block"),
                "peak_role": item.get("peak_role"),
                "peak_start_frame": item.get("peak_start_frame"),
                "peak_end_frame": item.get("peak_end_frame"),
                "segment_start_frame": item.get("segment_start_frame"),
                "segment_end_frame": item.get("segment_end_frame"),
                "segment_duration": item.get("segment_duration"),
                "detection_mode": item["detection_mode"],
                "diff_curve_path": str(output_dir / "diff_curve.png"),
                "diff_curve_json_path": str(output_dir / "diff_curve.json"),
                "kind": "flash_spike",
            })
        return results

    def _write_diff_debug_artifacts(
        self,
        output_dir: Path,
        fps: float,
        threshold: float,
        min_gap: int,
        max_gap: int,
        diffs: list[dict],
        peaks: list[dict],
        segments: list[dict],
    ) -> None:
        debug_json = output_dir / "diff_curve.json"
        debug_png = output_dir / "diff_curve.png"
        payload = {
            "fps": fps,
            "threshold": threshold,
            "min_gap_frames": min_gap,
            "max_gap_frames": max_gap,
            "min_gap_seconds": min_gap / fps,
            "max_gap_seconds": max_gap / fps,
            "diffs": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                    "global_score": item["global_score"],
                    "block_score": item["block_score"],
                }
                for item in diffs
            ],
            "peaks": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                    "global_score": item["global_score"],
                    "block_score": item["block_score"],
                }
                for item in peaks
            ],
            "segments": [
                {
                    "peak_start_frame": item.get("peak_start_frame"),
                    "peak_end_frame": item.get("peak_end_frame"),
                    "peak_start_time": item.get("peak_start_frame") / fps if item.get("peak_start_frame") is not None else None,
                    "peak_end_time": item.get("peak_end_frame") / fps if item.get("peak_end_frame") is not None else None,
                    "segment_start_frame": item.get("segment_start_frame"),
                    "segment_end_frame": item.get("segment_end_frame"),
                    "segment_start_time": item.get("segment_start_frame") / fps if item.get("segment_start_frame") is not None else None,
                    "segment_end_time": item.get("segment_end_frame") / fps if item.get("segment_end_frame") is not None else None,
                    "segment_duration": item.get("segment_duration"),
                    "score": item.get("score"),
                    "detection_mode": item.get("detection_mode"),
                }
                for item in segments
            ],
        }
        debug_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_diff_curve_png(debug_png, fps, diffs, peaks, segments, threshold)

    def _write_diff_curve_png(
        self,
        path: Path,
        fps: float,
        diffs: list[dict],
        peaks: list[dict],
        segments: list[dict],
        threshold: float,
    ) -> None:
        _load_frame_deps()
        if not diffs:
            return

        width = 1200
        height = 420
        margin_left = 56
        margin_right = 24
        margin_top = 24
        margin_bottom = 44
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        image = np.full((height, width, 3), 255, dtype=np.uint8)

        max_score = max(max(item["score"] for item in diffs), threshold, 1.0)

        def point(frame_num: int, score: float) -> tuple[int, int]:
            if len(diffs) <= 1:
                x = margin_left
            else:
                x = margin_left + int((frame_num - diffs[0]["frame_num"]) / (diffs[-1]["frame_num"] - diffs[0]["frame_num"]) * plot_w)
            y = margin_top + plot_h - int(score / max_score * plot_h)
            return x, y

        cv2.rectangle(image, (margin_left, margin_top), (margin_left + plot_w, margin_top + plot_h), (220, 220, 220), 1)

        duration = diffs[-1]["frame_num"] / fps
        tick_step = self._time_tick_step(duration)
        tick = 0.0
        while tick <= duration + 1e-9:
            frame_num = int(round(tick * fps))
            x, _ = point(frame_num, 0)
            cv2.line(image, (x, margin_top + plot_h), (x, margin_top + plot_h + 6), (120, 120, 120), 1)
            cv2.line(image, (x, margin_top), (x, margin_top + plot_h), (238, 238, 238), 1)
            label = self._format_seconds(tick)
            cv2.putText(image, label, (max(margin_left - 12, x - 14), margin_top + plot_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 80, 80), 1, cv2.LINE_AA)
            tick += tick_step

        threshold_y = point(diffs[0]["frame_num"], threshold)[1]
        cv2.line(image, (margin_left, threshold_y), (margin_left + plot_w, threshold_y), (80, 80, 220), 1)
        cv2.putText(image, f"threshold={threshold:.1f}", (margin_left + 8, max(14, threshold_y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 80, 220), 1, cv2.LINE_AA)

        points = [point(item["frame_num"], item["score"]) for item in diffs]
        for start, end in zip(points, points[1:]):
            cv2.line(image, start, end, (45, 45, 45), 1)

        for segment in segments:
            start_frame = segment.get("segment_start_frame")
            end_frame = segment.get("segment_end_frame")
            if start_frame is None or end_frame is None:
                continue
            x1, _ = point(start_frame, 0)
            x2, _ = point(end_frame, 0)
            overlay = image.copy()
            cv2.rectangle(overlay, (x1, margin_top), (x2, margin_top + plot_h), (210, 245, 210), -1)
            image[:] = cv2.addWeighted(overlay, 0.35, image, 0.65, 0)

        for peak in peaks:
            x, y = point(peak["frame_num"], peak["score"])
            cv2.circle(image, (x, y), 4, (0, 80, 255), -1)

        cv2.putText(image, "Diff curve", (margin_left, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1, cv2.LINE_AA)
        cv2.putText(image, "orange=peaks green=paired short segment blue=threshold", (margin_left + 150, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (70, 70, 70), 1, cv2.LINE_AA)
        cv2.imwrite(str(path), image)

    def _time_tick_step(self, duration_seconds: float) -> float:
        if duration_seconds <= 10:
            return 1.0
        if duration_seconds <= 30:
            return 2.0
        if duration_seconds <= 120:
            return 10.0
        if duration_seconds <= 600:
            return 30.0
        return 60.0

    def _format_seconds(self, seconds: float) -> str:
        total = int(round(seconds))
        minutes = total // 60
        secs = total % 60
        if minutes:
            return f"{minutes}:{secs:02d}"
        return f"{secs}s"

    def _detect_pyscenedetect_short_scenes(
        self,
        video_path: Path,
        output_dir: Path,
        max_spikes: int,
    ) -> list[dict]:
        _load_frame_deps()
        try:
            from scenedetect import ContentDetector, detect
        except Exception:
            return []

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        short_segment_max_frames = settings.video_short_segment_max_frames
        if short_segment_max_frames <= 0:
            short_segment_max_frames = max(1, int(fps * settings.video_short_segment_max_seconds))

        try:
            scenes = detect(
                str(video_path),
                ContentDetector(
                    threshold=settings.pyscenedetect_threshold,
                    min_scene_len=settings.pyscenedetect_min_scene_len,
                ),
                show_progress=False,
            )
        except Exception:
            cap.release()
            return []

        candidates = []
        for start_time, end_time in scenes:
            start_frame = int(start_time.get_frames())
            end_frame = int(end_time.get_frames())
            duration_frames = max(1, end_frame - start_frame)
            if duration_frames > short_segment_max_frames:
                continue

            middle_frame = start_frame + duration_frames // 2
            cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame)
            ok, frame = cap.read()
            if not ok:
                continue

            candidates.append({
                "frame_num": middle_frame,
                "frame": frame.copy(),
                "score": float(short_segment_max_frames - duration_frames + 1),
                "segment_start_frame": start_frame,
                "segment_end_frame": end_frame,
                "segment_duration": duration_frames / fps,
                "detection_mode": "pyscenedetect_short_scene",
            })

        cap.release()

        candidates.sort(key=lambda item: item["score"], reverse=True)
        if max_spikes > 0:
            candidates = candidates[:max_spikes]
        candidates.sort(key=lambda item: item["frame_num"])

        results = []
        for idx, candidate in enumerate(candidates):
            path = output_dir / f"scene_{idx:02d}.jpg"
            cv2.imwrite(str(path), candidate["frame"])
            results.append({
                "index": idx,
                "path": str(path),
                "timestamp": candidate["frame_num"] / fps,
                "frame_number": candidate["frame_num"],
                "score": candidate["score"],
                "segment_start_frame": candidate["segment_start_frame"],
                "segment_end_frame": candidate["segment_end_frame"],
                "segment_duration": candidate["segment_duration"],
                "detection_mode": candidate["detection_mode"],
                "kind": "flash_spike",
            })
        return results

    def _gray_small(self, frame, target_width: int = 320):
        _load_frame_deps()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        if width > target_width:
            scale = target_width / width
            gray = cv2.resize(gray, (target_width, max(1, int(height * scale))))
        return gray

    def _diff_scores(self, gray1, gray2, grid: int = 4) -> tuple[float, float]:
        delta = cv2.absdiff(gray1, gray2)
        global_score = float(np.mean(delta))
        height, width = delta.shape[:2]
        block_score = global_score
        cell_h = max(1, height // grid)
        cell_w = max(1, width // grid)

        for row in range(grid):
            y1 = row * cell_h
            y2 = height if row == grid - 1 else min(height, (row + 1) * cell_h)
            for col in range(grid):
                x1 = col * cell_w
                x2 = width if col == grid - 1 else min(width, (col + 1) * cell_w)
                block = delta[y1:y2, x1:x2]
                if block.size:
                    block_score = max(block_score, float(np.mean(block)))
        return global_score, block_score

    def _read_frame(self, video_path: Path, frame_num: int):
        _load_frame_deps()
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None

    def _diff(self, frame1, frame2) -> float:
        _load_frame_deps()
        if frame1 is None or frame2 is None:
            return 0.0
        gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
        return float(np.mean(cv2.absdiff(gray1, gray2)))
