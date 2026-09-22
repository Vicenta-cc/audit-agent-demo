"""Bound ASR input size for the PCM WAV produced by audio extraction."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import math
import wave


class ASROutOfMemoryError(RuntimeError):
    code = "asr_gpu_out_of_memory"

    def __init__(self, message: str, *, subdivision_exhausted: bool = False):
        super().__init__(message)
        self.subdivision_exhausted = subdivision_exhausted


def is_asr_oom(value: object) -> bool:
    if isinstance(value, ASROutOfMemoryError):
        return True
    message = str(value).lower()
    return any(marker in message for marker in (
        "asr_gpu_out_of_memory", "cuda out of memory", "cuda error: out of memory",
        "torch.cuda.outofmemoryerror",
    ))


def _join_text(parts: list[str], *, deduplicate: bool = True) -> str:
    text = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Only remove an exact suffix/prefix match at a chunk boundary. No
        # fuzzy replacement of potentially meaningful differences in speech.
        overlap = 0
        for length in range(min(len(text), len(part), 500) if deduplicate else 0, 1, -1):
            if text.endswith(part[:length]):
                cjk = any("\u4e00" <= char <= "\u9fff" for char in part[:length])
                if not cjk and (
                    (len(text) > length and text[-length - 1].isalnum() and part[0].isalnum())
                    or (len(part) > length and part[length - 1].isalnum() and part[length].isalnum())
                ):
                    continue
                overlap = length
                break
        part = part[overlap:].lstrip()
        if part:
            text = f"{text}\n{part}" if text else part
    return text


def _timestamp(item: dict, key: str) -> float | None:
    try:
        value = float(item.get(key, item.get(f"{key}_time")))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _provider_words(result: dict) -> list[dict]:
    # Older inference servers expose Dolphin word timestamps only in raw.
    raw = result.get("raw")
    words = []
    for item in raw if isinstance(raw, list) else [raw]:
        if not isinstance(item, dict):
            continue
        for word in item.get("word_timestamps") or []:
            if not isinstance(word, dict):
                continue
            start, end = _timestamp(word, "start"), _timestamp(word, "end")
            if start is not None and end is not None:
                words.append({"start": start, "end": end,
                              "word": str(word.get("word") or word.get("text") or word.get("token") or word.get("char") or "")})
    return words


def _merge_chunks(chunks: list[dict], *, max_seconds: float, overlap_seconds: float) -> dict:
    segments, texts, warnings = [], [], set()
    precise_words = True
    for index, chunk in enumerate(chunks):
        result = chunk["result"]
        offset, duration = chunk["offset_seconds"], chunk["duration_seconds"]
        left = (offset + chunks[index - 1]["offset_seconds"] + chunks[index - 1]["duration_seconds"]) / 2 if index else 0
        right = (offset + duration + chunks[index + 1]["offset_seconds"]) / 2 if index + 1 < len(chunks) else offset + duration
        kept = []
        raw_words = _provider_words(result)
        for original in result["segments"]:
            segment = deepcopy(original)
            start, end = _timestamp(segment, "start"), _timestamp(segment, "end")
            if not segment.get("words") and raw_words and start is not None and end is not None:
                segment["words"] = [dict(word) for word in raw_words if start <= (word["start"] + word["end"]) / 2 < end]
            for item in [segment, *(segment.get("words") or [])]:
                for key in ("start", "end"):
                    value = _timestamp(item, key)
                    if value is not None:
                        item[key] = offset + max(0.0, min(duration, value))
                        if f"{key}_time" in item:
                            item[f"{key}_time"] = item[key]
            words = segment.get("words") or []
            if words and all(_timestamp(word, "start") is not None and _timestamp(word, "end") is not None for word in words):
                words = [word for word in words if left <= (word["start"] + word["end"]) / 2 < right]
                if not words:
                    continue
                segment.update(words=words, start=words[0]["start"], end=words[-1]["end"])
                word_texts = [str(word.get("word", word.get("text", ""))).strip() for word in words]
                separator = "" if all(any("\u4e00" <= char <= "\u9fff" for char in word) for word in word_texts) else " "
                segment["text"] = separator.join(word_texts)
            else:
                precise_words = False
                start, end = _timestamp(segment, "start"), _timestamp(segment, "end")
                if start is not None and end is not None:
                    if not left <= (start + end) / 2 < right:
                        continue
                warnings.add("Provider lacks word timestamps; overlap deduplication uses segment times and exact text matches.")
            kept.append(segment)
        segments.extend(kept)
        # Timestamped segments are authoritative for the text at the overlap.
        # With text-only ASR, preserve the text and deduplicate exact boundaries.
        texts.append(" ".join(str(seg.get("text") or "") for seg in kept) if result["segments"] else result["text"])
        if not result["segments"] and result["text"]:
            precise_words = False
            warnings.add("Provider lacks word timestamps; overlap deduplication uses segment times and exact text matches.")
        if result.get("timestamp_warning"):
            warnings.add(result["timestamp_warning"])
    output = {**chunks[0]["result"], "text": _join_text(texts, deduplicate=not precise_words), "segments": segments,
              "raw": {"chunks": [{key: value for key, value in chunk.items() if key != "result"}
                                  | {"payload": chunk["result"].get("raw")} for chunk in chunks]},
              "chunk_count": sum(chunk["result"].get("chunk_count", 1) for chunk in chunks),
              "chunk_seconds": max_seconds, "overlap_seconds": overlap_seconds,
              "oom_split_count": sum(chunk["result"].get("oom_split_count", 0) for chunk in chunks)}
    if warnings:
        output["timestamp_warning"] = " ".join(sorted(warnings))
    return output


def transcribe_in_chunks(audio_path: Path, transcribe, *, max_seconds: float = 120,
                         overlap_seconds: float = 5, min_seconds: float = 15) -> dict:
    """Serial overlapping PCM chunks, with bounded subdivision on explicit OOM.

    Own words/segments by the midpoint of each overlap. Sample positions define
    global timestamps. A failed chunk never publishes an incomplete transcript.
    """
    with wave.open(str(audio_path), "rb") as source:
        rate = source.getframerate()
        size = max(1, int(max_seconds * rate))
        frames = source.getnframes()
        if frames <= size:
            try:
                result = transcribe(audio_path)
                if isinstance(result, dict) and is_asr_oom(result.get("error", "")):
                    raise ASROutOfMemoryError("语音转写 GPU 显存不足")
                return result
            except Exception as exc:
                if not isinstance(exc, ASROutOfMemoryError) and not is_asr_oom(exc):
                    raise
                if getattr(exc, "subdivision_exhausted", False):
                    raise
                if frames / rate <= min_seconds:
                    raise ASROutOfMemoryError("语音转写 GPU 显存不足；缩短分段后仍失败，已停止本帖转写",
                                             subdivision_exhausted=True) from exc
            # Leave the exception scope before retrying so model tensors held
            # by the failed call's traceback can be released.
            result = transcribe_in_chunks(audio_path, transcribe,
                max_seconds=max(min_seconds, frames / rate / 2),
                overlap_seconds=overlap_seconds, min_seconds=min_seconds)
            result["oom_split_count"] = result.get("oom_split_count", 0) + 1
            return result
        overlap = max(0, min(int(overlap_seconds * rate), size // 3))
        chunks = []
        with TemporaryDirectory(prefix="audit-asr-chunks-") as directory:
            chunk_path = Path(directory) / "chunk.wav"
            for start in range(0, frames, size - overlap):
                count = min(size, frames - start)
                source.setpos(start)
                with wave.open(str(chunk_path), "wb") as target:
                    target.setparams(source.getparams())
                    target.writeframes(source.readframes(count))
                result = transcribe_in_chunks(chunk_path, transcribe,
                    max_seconds=max_seconds, overlap_seconds=overlap_seconds, min_seconds=min_seconds)
                if not isinstance(result, dict) or result.get("error"):
                    raise RuntimeError("ASR chunk failed; incomplete transcript discarded")
                if not isinstance(result.get("text"), str) or not isinstance(result.get("segments"), list):
                    raise RuntimeError("ASR chunk returned invalid text/segments")
                chunks.append({"offset_seconds": start / rate, "duration_seconds": count / rate, "result": result})
                if start + count == frames:
                    break
        return _merge_chunks(chunks, max_seconds=max_seconds, overlap_seconds=overlap / rate)
