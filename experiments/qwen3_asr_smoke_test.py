from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a small local Qwen3-ASR smoke test against one video/audio file. "
            "The script extracts a 16 kHz mono WAV when the input is a video."
        )
    )
    parser.add_argument("input", type=Path, help="Local video/audio path, for example data/test.mp4")
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-0.6B", help="HF/ModelScope id or local model dir")
    parser.add_argument(
        "--language",
        action="append",
        default=[],
        help=(
            "Language name to force, such as Arabic, Turkish, or Persian. "
            "Omit for auto-detection. Can be passed more than once."
        ),
    )
    parser.add_argument(
        "--also-force-nearby",
        action="store_true",
        help="Also run Arabic/Turkish/Persian forced-language probes for Uyghur-like failure analysis.",
    )
    parser.add_argument("--ffmpeg", default=os.getenv("FFMPEG_PATH", ""), help="Path to ffmpeg")
    parser.add_argument("--trim-seconds", type=float, default=90.0, help="Only test the first N seconds; 0 means full file")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qwen3_asr_tests"), help="Result directory")
    parser.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    parser.add_argument("--dtype", choices=["auto", "bfloat16", "float16", "float32"], default="auto")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--max-inference-batch-size", type=int, default=1)
    parser.add_argument("--trust-remote-code", action="store_true", help="Forward trust_remote_code=True if supported")
    return parser.parse_args()


def compact_error(message: str, limit: int = 1200) -> str:
    message = " ".join((message or "").split())
    if len(message) <= limit:
        return message
    return "..." + message[-limit:]


def resolve_ffmpeg(configured: str) -> str:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(
        [
            "ffmpeg",
            "/Users/ext.wanghongtao6/Documents/software/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/usr/bin/ffmpeg",
        ]
    )
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def ensure_audio(input_path: Path, output_dir: Path, ffmpeg: str, trim_seconds: float) -> Path:
    suffix = input_path.suffix.lower()
    if suffix not in VIDEO_SUFFIXES | AUDIO_SUFFIXES:
        raise SystemExit(f"Unsupported media suffix: {input_path.suffix}")
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found. Pass --ffmpeg /Users/ext.wanghongtao6/Documents/software/ffmpeg")

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "input_16k_mono.wav"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
    ]
    if trim_seconds and trim_seconds > 0:
        cmd.extend(["-t", str(trim_seconds)])
    cmd.append(str(audio_path))
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        details = compact_error(completed.stderr or completed.stdout or "")
        raise SystemExit(f"ffmpeg failed with code {completed.returncode}: {details}")
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio. The input may not contain an audio track.")
    return audio_path


def import_qwen_asr():
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except Exception as exc:
        install_hint = """
Missing Qwen3-ASR dependencies.

Suggested server install:
  python3 -m venv .venv-qwen3-asr
  . .venv-qwen3-asr/bin/activate
  python -m pip install -U pip
  python -m pip install -U qwen-asr

Then run:
  python /mnt/workspace/qwen3-asr-test/qwen3_asr_smoke_test.py /mnt/workspace/qwen3-asr-test/test.mp4 \\
    --device cuda --dtype bfloat16 --also-force-nearby
"""
        raise SystemExit(f"{install_hint}\nOriginal import error: {exc}") from exc
    return torch, Qwen3ASRModel


def pick_device(torch: Any, requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def pick_dtype(torch: Any, device: str, requested: str) -> Any:
    if requested == "bfloat16":
        return torch.bfloat16
    if requested == "float16":
        return torch.float16
    if requested == "float32":
        return torch.float32
    if device == "cuda":
        return torch.bfloat16
    if device == "mps":
        return torch.float16
    return torch.float32


def device_map_for(device: str) -> str:
    if device == "cuda":
        return "cuda:0"
    if device == "mps":
        return "mps"
    return "cpu"


def serializable(value: Any) -> Any:
    if is_dataclass(value):
        return serializable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if hasattr(value, "model_dump"):
        return serializable(value.model_dump())
    if hasattr(value, "__dict__"):
        return serializable(vars(value))
    return str(value)


def main() -> None:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    run_dir = (args.output_dir / input_path.stem / time.strftime("%Y%m%d_%H%M%S")).resolve()
    ffmpeg = resolve_ffmpeg(args.ffmpeg)

    print(f"[1/4] input: {input_path}")
    print(f"[2/4] ffmpeg: {ffmpeg or '(not found)'}")
    audio_path = ensure_audio(input_path, run_dir, ffmpeg, args.trim_seconds)
    print(f"[2/4] extracted audio: {audio_path}")

    torch, Qwen3ASRModel = import_qwen_asr()
    device = pick_device(torch, args.device)
    dtype = pick_dtype(torch, device, args.dtype)
    dtype_name = str(dtype).replace("torch.", "")
    device_map = device_map_for(device)
    print(f"[3/4] loading model={args.model} device_map={device_map} dtype={dtype_name}")

    model_kwargs = {
        "dtype": dtype,
        "device_map": device_map,
        "max_inference_batch_size": args.max_inference_batch_size,
        "max_new_tokens": args.max_new_tokens,
    }
    if args.trust_remote_code:
        model_kwargs["trust_remote_code"] = True

    try:
        model = Qwen3ASRModel.from_pretrained(args.model, **model_kwargs)
    except TypeError:
        model_kwargs.pop("trust_remote_code", None)
        model = Qwen3ASRModel.from_pretrained(args.model, **model_kwargs)

    languages: list[str | None] = [None]
    languages.extend(args.language)
    if args.also_force_nearby:
        languages.extend(["Arabic", "Turkish", "Persian"])

    seen = set()
    languages = [lang for lang in languages if not (lang in seen or seen.add(lang))]
    report: dict[str, Any] = {
        "input": str(input_path),
        "audio": str(audio_path),
        "model": args.model,
        "device": device,
        "device_map": device_map,
        "dtype": dtype_name,
        "trim_seconds": args.trim_seconds,
        "results": [],
    }

    print(f"[4/4] transcribing {len(languages)} run(s)")
    for lang in languages:
        started = time.perf_counter()
        results = model.transcribe(audio=str(audio_path), language=lang)
        elapsed = time.perf_counter() - started
        result_items = serializable(results)
        first = result_items[0] if isinstance(result_items, list) and result_items else result_items
        text = first.get("text", "") if isinstance(first, dict) else str(first)
        detected_language = first.get("language") if isinstance(first, dict) else None
        label = "auto" if lang is None else lang.lower().replace(" ", "_")
        txt_path = run_dir / f"{label}.txt"
        txt_path.write_text(text, encoding="utf-8")
        print("")
        print(f"=== language={lang or 'auto'} detected={detected_language or 'unknown'} elapsed={elapsed:.1f}s ===")
        print(text)
        report["results"].append(
            {
                "requested_language": lang,
                "detected_language": detected_language,
                "elapsed_seconds": elapsed,
                "text": text,
                "raw": result_items,
                "txt_path": str(txt_path),
            }
        )

    json_path = run_dir / "result.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print(f"Saved JSON: {json_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
