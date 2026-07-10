from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Uyghur fine-tuned Whisper ASR smoke test.")
    parser.add_argument("input", type=Path, help="Local video/audio path.")
    parser.add_argument("--model", default="ixxan/whisper-small-uyghur-common-voice")
    parser.add_argument("--ffmpeg", default=os.getenv("FFMPEG_PATH", ""), help="Path to ffmpeg")
    parser.add_argument("--trim-seconds", type=float, default=90.0, help="Only test first N seconds; 0 means full file")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/whisper_uyghur_tests"))
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--dtype", choices=["auto", "float16", "bfloat16", "float32"], default="auto")
    parser.add_argument("--chunk-length-s", type=float, default=30.0)
    parser.add_argument("--stride-length-s", type=float, default=5.0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--return-timestamps", action="store_true")
    return parser.parse_args()


def compact_error(message: str, limit: int = 1200) -> str:
    message = " ".join((message or "").split())
    return message if len(message) <= limit else "..." + message[-limit:]


def resolve_ffmpeg(configured: str) -> str:
    candidates = [
        configured,
        "ffmpeg",
        "/Users/ext.wanghongtao6/Documents/software/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    for candidate in candidates:
        if not candidate:
            continue
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
        raise SystemExit("ffmpeg not found. Install ffmpeg or pass --ffmpeg /path/to/ffmpeg")

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


def import_deps():
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except Exception as exc:
        install_hint = """
Missing dependencies.

Suggested install:
  python -m pip install -U transformers accelerate soundfile librosa

If the server cannot reach Hugging Face:
  export HF_ENDPOINT=https://hf-mirror.com
"""
        raise SystemExit(f"{install_hint}\nOriginal import error: {exc}") from exc
    return torch, AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline


def pick_device(torch: Any, requested: str) -> tuple[str, int]:
    if requested == "cuda":
        return "cuda", 0
    if requested == "cpu":
        return "cpu", -1
    if torch.cuda.is_available():
        return "cuda", 0
    return "cpu", -1


def pick_dtype(torch: Any, device: str, requested: str) -> Any:
    if requested == "float16":
        return torch.float16
    if requested == "bfloat16":
        return torch.bfloat16
    if requested == "float32":
        return torch.float32
    return torch.float16 if device == "cuda" else torch.float32


def main() -> None:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    run_dir = (args.output_dir / input_path.stem / time.strftime("%Y%m%d_%H%M%S")).resolve()
    ffmpeg = resolve_ffmpeg(args.ffmpeg)

    print(f"[1/4] input: {input_path}")
    print(f"[2/4] ffmpeg: {ffmpeg or '(not found)'}")
    audio_path = ensure_audio(input_path, run_dir, ffmpeg, args.trim_seconds)
    print(f"[2/4] extracted audio: {audio_path}")

    torch, AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline = import_deps()
    device_name, pipeline_device = pick_device(torch, args.device)
    torch_dtype = pick_dtype(torch, device_name, args.dtype)
    print(f"[3/4] loading model={args.model} device={device_name} dtype={str(torch_dtype).replace('torch.', '')}")

    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        args.model,
        torch_dtype=torch_dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    )
    processor = AutoProcessor.from_pretrained(args.model)
    asr = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=torch_dtype,
        device=pipeline_device,
    )

    generate_kwargs: dict[str, Any] = {"task": "transcribe"}
    print(f"[4/4] transcribing chunk={args.chunk_length_s}s stride={args.stride_length_s}s")
    started = time.perf_counter()
    result = asr(
        str(audio_path),
        chunk_length_s=args.chunk_length_s,
        stride_length_s=args.stride_length_s,
        batch_size=args.batch_size,
        return_timestamps=args.return_timestamps,
        generate_kwargs=generate_kwargs,
    )
    elapsed = time.perf_counter() - started
    text = result.get("text", "") if isinstance(result, dict) else str(result)
    report = {
        "input": str(input_path),
        "audio": str(audio_path),
        "model": args.model,
        "device": device_name,
        "dtype": str(torch_dtype).replace("torch.", ""),
        "trim_seconds": args.trim_seconds,
        "elapsed_seconds": elapsed,
        "text": text,
        "raw": result,
    }
    (run_dir / "text.txt").write_text(text, encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print(text)
    print("")
    print(f"Saved: {run_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
