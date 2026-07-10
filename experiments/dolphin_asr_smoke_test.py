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
    parser = argparse.ArgumentParser(description="Run a Dolphin Uyghur ASR smoke test.")
    parser.add_argument("input", type=Path, help="Local video/audio path.")
    parser.add_argument("--model", default="small", help="Dolphin model name. Try small or base.")
    parser.add_argument("--model-dir", default="", help="Directory where Dolphin downloads/loads model files.")
    parser.add_argument("--lang-sym", default="ug", help="Dolphin language symbol. Use ug for Uyghur.")
    parser.add_argument("--region-sym", default="CN", help="Dolphin region symbol. Try CN or NULL for Uyghur.")
    parser.add_argument("--ffmpeg", default=os.getenv("FFMPEG_PATH", ""), help="Path to ffmpeg.")
    parser.add_argument("--trim-seconds", type=float, default=90.0, help="Only test first N seconds; 0 means full file.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/dolphin_asr_tests"))
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--decoding-method", default="", help="Optional Dolphin decoding method, e.g. attention.")
    parser.add_argument("--word-timestamp", action="store_true", help="Request word timestamps if supported.")
    parser.add_argument(
        "--audio-loader",
        choices=["soundfile", "torchaudio"],
        default="soundfile",
        help="Use soundfile to avoid torchaudio/torchcodec decoder issues.",
    )
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
    if suffix in AUDIO_SUFFIXES and suffix == ".wav" and (not trim_seconds or trim_seconds <= 0):
        return input_path
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


def import_dolphin():
    try:
        import dolphin
        from dolphin import transcribe
    except Exception as exc:
        install_hint = """
Missing Dolphin dependencies.

Suggested install:
  python -m pip install -U dataoceanai-dolphin

If model download is slow, Dolphin also publishes weights on ModelScope.
"""
        raise SystemExit(f"{install_hint}\nOriginal import error: {exc}") from exc
    return dolphin, transcribe


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


def patch_torchaudio_load_with_soundfile() -> None:
    try:
        import soundfile as sf
        import torch
        import torchaudio
    except Exception as exc:
        raise SystemExit(
            "soundfile audio loader requires soundfile, torch, and torchaudio. "
            "Install with: python -m pip install soundfile"
        ) from exc

    def load(path: str | Path, *args, **kwargs):
        data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        waveform = torch.from_numpy(data.T.copy())
        return waveform, sample_rate

    torchaudio.load = load


def extract_text(result: Any) -> str:
    if isinstance(result, list):
        chunks = []
        for item in result:
            chunks.append(
                getattr(item, "text_nospecial", "")
                or getattr(item, "text", "")
                or str(item)
            )
        return "\n".join(chunk for chunk in chunks if chunk)
    return (
        getattr(result, "text_nospecial", "")
        or getattr(result, "text", "")
        or str(result)
    )


def main() -> None:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    run_dir = (args.output_dir / input_path.stem / time.strftime("%Y%m%d_%H%M%S")).resolve()
    ffmpeg = resolve_ffmpeg(args.ffmpeg)

    print(f"[1/4] input: {input_path}")
    print(f"[2/4] ffmpeg: {ffmpeg or '(not found)'}")
    audio_path = ensure_audio(input_path, run_dir, ffmpeg, args.trim_seconds)
    print(f"[2/4] audio: {audio_path}")

    dolphin, transcribe = import_dolphin()
    if args.audio_loader == "soundfile":
        patch_torchaudio_load_with_soundfile()
    print(f"[3/4] loading model={args.model} device={args.device}")
    model_dir = args.model_dir or None
    if model_dir:
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        try:
            model = dolphin.load_model(args.model, model_dir, args.device)
        except TypeError:
            model = dolphin.load_model(args.model, device=args.device)
    else:
        model = dolphin.load_model(args.model, device=args.device)

    decode_kwargs: dict[str, Any] = {
        "lang_sym": args.lang_sym,
        "region_sym": args.region_sym,
    }
    if args.decoding_method:
        decode_kwargs["decoding_method"] = args.decoding_method
    if args.word_timestamp:
        decode_kwargs["word_timestamp"] = True

    print(f"[4/4] transcribing lang={args.lang_sym} region={args.region_sym}")
    started = time.perf_counter()
    result = transcribe(model, str(audio_path), **decode_kwargs)
    elapsed = time.perf_counter() - started
    payload = serializable(result)
    text = extract_text(result)
    if not text and isinstance(payload, dict):
        text = payload.get("text", "") or payload.get("sentence", "") or str(payload)

    run_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "input": str(input_path),
        "audio": str(audio_path),
        "model": args.model,
        "model_dir": model_dir,
        "device": args.device,
        "lang_sym": args.lang_sym,
        "region_sym": args.region_sym,
        "trim_seconds": args.trim_seconds,
        "elapsed_seconds": elapsed,
        "text": text,
        "raw": payload,
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
