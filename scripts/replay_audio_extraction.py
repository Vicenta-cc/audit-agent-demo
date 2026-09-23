"""Re-extract audio from failed saved video posts, without calling ASR or updating jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error("output directory must be empty")

    from backend.audit_agent.config import settings
    from backend.audit_agent import video_processor as module

    notes = sorted({
        item["note_id"]
        for path in (args.source_output / "post_failures").glob("*.json")
        for item in [json.loads(path.read_text())]
        if item.get("stage") == "video_audit" and str(item.get("note_id", "")).isdigit()
    })
    if not notes:
        parser.error("No saved video-stage failures found")
    resolved = module.DemoAudioProcessor()._resolve_ffmpeg()
    version = subprocess.run([resolved, "-version"], capture_output=True, text=True).stdout if resolved else ""
    manifest = {
        "source_output": str(args.source_output),
        "python": sys.executable,
        "processor_file": module.__file__,
        "processor_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "ffmpeg_path": resolved,
        "ffmpeg_version": version,
        "configured_asr_engine": settings.asr_engine,
        "configured_use_remote_asr": settings.use_remote_asr,
        "asr_called": False,
        "notes": notes,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    results = []
    for note in notes:
        source = args.source_output / "crawler" / "douyin" / "videos" / note / "video.mp4"
        processor = module.DemoAudioProcessor()
        audio = processor.extract_audio(source, args.output / note)
        input_hash = None
        if source.is_file():
            with source.open("rb") as stream:
                input_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        result = {
            "note_id": note,
            "status": processor.last_extract_status,
            "error": processor.last_extract_error,
            "input_sha256": input_hash,
            "audio_path": str(audio) if audio else None,
            "audio_size": audio.stat().st_size if audio else None,
            "diagnostic": processor.last_extract_diagnostic,
        }
        results.append(result)
        print(json.dumps({key: result[key] for key in ("note_id", "status", "error", "audio_size")}, ensure_ascii=False), flush=True)
    summary = {
        "videos": len(results),
        "success": sum(r["status"] == "success" for r in results),
        "no_audio_track": sum(r["status"] == "no_audio_track" for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
        "results": results,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}), flush=True)


if __name__ == "__main__":
    main()
