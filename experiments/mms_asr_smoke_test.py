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
MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Meta MMS Uyghur ASR smoke test.")
    parser.add_argument("input", type=Path, help="Local video/audio file or directory.")
    parser.add_argument(
        "--model",
        default="facebook/mms-1b-all",
        help="Use facebook/mms-1b-all for ASR. facebook/mms-1b is only the pretraining base.",
    )
    parser.add_argument("--target-lang", default="uig-script_arabic", help="MMS adapter language id.")
    parser.add_argument("--ffmpeg", default=os.getenv("FFMPEG_PATH", ""), help="Path to ffmpeg.")
    parser.add_argument("--trim-seconds", type=float, default=90.0, help="Only test first N seconds; 0 means full file.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/mms_asr_tests"))
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--dtype", choices=["auto", "float16", "bfloat16", "float32"], default="auto")
    parser.add_argument("--chunk-length-s", type=float, default=20.0)
    parser.add_argument("--stride-length-s", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--return-timestamps", action="store_true")
    parser.add_argument("--hf-endpoint", default="", help="Optional Hugging Face endpoint, for example https://hf-mirror.com.")
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
    if suffix not in MEDIA_SUFFIXES:
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


def discover_inputs(input_path: Path) -> list[Path]:
    if not input_path.exists():
        raise SystemExit(f"Input path not found: {input_path}")
    if input_path.is_dir():
        media_paths = sorted(
            path for path in input_path.rglob("*")
            if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
        )
        if not media_paths:
            supported = ", ".join(sorted(MEDIA_SUFFIXES))
            raise SystemExit(f"No supported media files found under {input_path}. Supported suffixes: {supported}")
        return media_paths
    if input_path.suffix.lower() not in MEDIA_SUFFIXES:
        supported = ", ".join(sorted(MEDIA_SUFFIXES))
        raise SystemExit(f"Unsupported media suffix: {input_path.suffix or '(none)'}. Supported suffixes: {supported}")
    return [input_path]


def output_slug(media_path: Path, root_path: Path) -> str:
    try:
        relative = media_path.relative_to(root_path)
    except ValueError:
        relative = Path(media_path.name)
    parts = [part for part in relative.with_suffix("").parts if part not in ("", ".")]
    return "__".join(parts) or media_path.stem


def import_deps():
    try:
        import soundfile as sf
        import torch
        from transformers import AutoModelForCTC, AutoProcessor, pipeline
    except Exception as exc:
        install_hint = """
Missing dependencies.

Suggested install:
  python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
  python -m pip install -U transformers accelerate soundfile librosa

If the server cannot reach Hugging Face:
  export HF_ENDPOINT=https://hf-mirror.com
"""
        raise SystemExit(f"{install_hint}\nOriginal import error: {exc}") from exc
    return sf, torch, AutoModelForCTC, AutoProcessor, pipeline


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


def read_audio(sf: Any, audio_path: Path) -> tuple[dict[str, Any], float]:
    data, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    duration_seconds = len(data) / float(sample_rate) if sample_rate else 0.0
    return {"array": data, "sampling_rate": sample_rate}, duration_seconds


def transcribe_one(
    *,
    media_path: Path,
    run_dir: Path,
    ffmpeg: str,
    args: argparse.Namespace,
    sf: Any,
    asr: Any,
    device_name: str,
    dtype_name: str,
) -> dict[str, Any]:
    print(f"[4/5] extracting audio: {media_path}")
    audio_path = ensure_audio(media_path, run_dir, ffmpeg, args.trim_seconds)
    print(f"[4/5] extracted audio: {audio_path}")

    audio_input, duration_seconds = read_audio(sf, audio_path)
    transcribe_kwargs: dict[str, Any] = {"batch_size": args.batch_size}
    if args.chunk_length_s and args.chunk_length_s > 0:
        transcribe_kwargs["chunk_length_s"] = args.chunk_length_s
    if args.stride_length_s and args.stride_length_s > 0:
        transcribe_kwargs["stride_length_s"] = args.stride_length_s
    if args.return_timestamps:
        transcribe_kwargs["return_timestamps"] = True

    print(f"[4/5] transcribing duration={duration_seconds:.1f}s chunk={args.chunk_length_s}s stride={args.stride_length_s}s")
    started = time.perf_counter()
    result = asr(audio_input, **transcribe_kwargs)
    elapsed = time.perf_counter() - started
    text = result.get("text", "") if isinstance(result, dict) else str(result)
    text = text.strip()

    report = {
        "input": str(media_path),
        "audio": str(audio_path),
        "model": args.model,
        "target_lang": args.target_lang,
        "device": device_name,
        "dtype": dtype_name,
        "trim_seconds": args.trim_seconds,
        "duration_seconds": duration_seconds,
        "chunk_length_s": args.chunk_length_s,
        "stride_length_s": args.stride_length_s,
        "elapsed_seconds": elapsed,
        "text": text,
        "raw": result,
    }
    (run_dir / "text.txt").write_text(text, encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(text)
    print("")
    print(f"[4/5] saved item: {run_dir}")
    return report


def main() -> None:
    args = parse_args()
    if args.hf_endpoint:
        os.environ["HF_ENDPOINT"] = args.hf_endpoint
    if args.model == "facebook/mms-1b":
        raise SystemExit(
            "facebook/mms-1b is the pretraining base, not the ASR checkpoint. "
            "Use --model facebook/mms-1b-all for this smoke test."
        )

    input_path = args.input.expanduser().resolve()
    media_paths = discover_inputs(input_path)
    batch_run_dir = (args.output_dir / input_path.stem / time.strftime("%Y%m%d_%H%M%S")).resolve()
    ffmpeg = resolve_ffmpeg(args.ffmpeg)

    print(f"[1/5] input: {input_path}")
    print(f"[1/5] media files: {len(media_paths)}")
    print(f"[2/5] ffmpeg: {ffmpeg or '(not found)'}")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found. Install ffmpeg or pass --ffmpeg /path/to/ffmpeg")

    sf, torch, AutoModelForCTC, AutoProcessor, pipeline = import_deps()
    device_name, pipeline_device = pick_device(torch, args.device)
    torch_dtype = pick_dtype(torch, device_name, args.dtype)
    dtype_name = str(torch_dtype).replace("torch.", "")
    print(
        f"[3/5] loading model={args.model} target_lang={args.target_lang} "
        f"device={device_name} dtype={dtype_name}"
    )

    processor = AutoProcessor.from_pretrained(args.model, target_lang=args.target_lang)
    model = AutoModelForCTC.from_pretrained(
        args.model,
        target_lang=args.target_lang,
        ignore_mismatched_sizes=True,
        torch_dtype=torch_dtype,
        low_cpu_mem_usage=True,
    )
    model.to(device_name)
    model.eval()

    asr = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=torch_dtype,
        device=pipeline_device,
    )

    reports = []
    for index, media_path in enumerate(media_paths, start=1):
        if len(media_paths) == 1 and not input_path.is_dir():
            item_run_dir = batch_run_dir
        else:
            item_run_dir = batch_run_dir / output_slug(media_path, input_path)
        print(f"\n=== file {index}/{len(media_paths)} ===")
        report = transcribe_one(
            media_path=media_path,
            run_dir=item_run_dir,
            ffmpeg=ffmpeg,
            args=args,
            sf=sf,
            asr=asr,
            device_name=device_name,
            dtype_name=dtype_name,
        )
        report["output_dir"] = str(item_run_dir)
        reports.append(report)

    batch_report = {
        "input": str(input_path),
        "media_count": len(media_paths),
        "model": args.model,
        "target_lang": args.target_lang,
        "device": device_name,
        "dtype": dtype_name,
        "trim_seconds": args.trim_seconds,
        "chunk_length_s": args.chunk_length_s,
        "stride_length_s": args.stride_length_s,
        "results": reports,
    }
    batch_run_dir.mkdir(parents=True, exist_ok=True)
    (batch_run_dir / "batch_result.json").write_text(json.dumps(batch_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[5/5] saved: {batch_run_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
