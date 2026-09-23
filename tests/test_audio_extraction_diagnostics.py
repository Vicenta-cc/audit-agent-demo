import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent import pipeline as pipeline_module
from backend.audit_agent import video_processor as module
from backend.audit_agent.pipeline import AuditPipeline, AudioExtractionError
from backend.audit_agent.models import AuditSubject


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video fixture")
    processor = module.DemoAudioProcessor()
    monkeypatch.setattr(processor, "_resolve_ffmpeg", lambda: "/test/ffmpeg")
    return processor, source, tmp_path / "audio"


def test_complete_ffmpeg_error_retained_in_audio_and_post_receipts(extraction, tmp_path, monkeypatch):
    processor, source, output = extraction
    stderr = "first cause: invalid input\n" + "codec detail\n" * 300 + "last detail"
    monkeypatch.setattr(module.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, "", stderr)))
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path)
    monkeypatch.setattr(pipeline_module.job_store, "log", Mock())
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = "test"
    p.authoritative_m3 = True
    p.audio = processor
    p.qwen = SimpleNamespace(provider_failure="")
    p.ingestion = SimpleNamespace(mark_content_status=Mock())
    processor.transcribe = Mock()
    with pytest.raises(AudioExtractionError) as caught:
        p._analyze_video_file(0, source, output, "", "local")
    processor.transcribe.assert_not_called()
    subject = AuditSubject("dy", "123", "", "", "", {}, [], [], [])
    p._record_subject_failure("dy", "123", subject, caught.value)
    record = json.loads(next((tmp_path / "test/post_failures").glob("*.json")).read_text())
    assert record["error_code"] == "audio_extraction_failed"
    diagnostic = record["audio_extraction_diagnostics"][0]
    assert diagnostic["stderr"] == stderr
    assert diagnostic["returncode"] == 1
    assert diagnostic["input_size"] == len(b"video fixture")
    assert diagnostic["resolved_ffmpeg"] == "/test/ffmpeg"
    assert json.loads(Path(record["diagnostic_paths"][0]).read_text())["stderr"] == stderr


@pytest.mark.parametrize("failure", ["missing_ffmpeg", "missing_input", "permission", "bad_output_directory", "empty_output"])
def test_failure_phases_are_recorded(extraction, monkeypatch, failure):
    processor, source, output = extraction
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(module.subprocess, "run", run)
    if failure == "missing_ffmpeg":
        monkeypatch.setattr(processor, "_resolve_ffmpeg", lambda: "")
    elif failure == "missing_input":
        source = source.parent / "missing.mp4"
    elif failure == "permission":
        run.side_effect = PermissionError("execute denied")
    elif failure == "bad_output_directory":
        output.write_bytes(b"not a directory")
    assert processor.extract_audio(source, output) is None
    diagnostic = processor.last_extract_diagnostic
    assert diagnostic["status"] == "failed"
    assert diagnostic["last_extract_error"]
    if failure == "bad_output_directory":
        assert diagnostic["phase"] == "prepare_output"
        assert diagnostic["error_type"] == "FileExistsError"
        assert diagnostic["diagnostic_write_error"]
    else:
        assert json.loads((output / "audio_extraction.json").read_text()) == diagnostic
    if failure == "permission":
        assert diagnostic["phase"] == "run_ffmpeg"
        assert "PermissionError: execute denied" in diagnostic["traceback"]


def test_no_audio_track_remains_distinct_from_extraction_failure(extraction, monkeypatch):
    processor, source, output = extraction
    monkeypatch.setattr(module.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, "", "Output file does not contain any stream")))
    assert processor.extract_audio(source, output) is None
    assert processor.last_extract_status == "no_audio_track"
    assert processor.last_extract_diagnostic["stderr"]
    assert processor.last_extract_error == ""


def test_success_clears_previous_failure_and_persists_private_receipt(extraction, monkeypatch):
    processor, source, output = extraction
    processor.last_extract_error = "previous failure"
    def run(command, **kwargs):
        Path(command[-1]).write_bytes(b"audio fixture")
        return subprocess.CompletedProcess(command, 0, "", "decoded")
    monkeypatch.setattr(module.subprocess, "run", run)
    assert processor.extract_audio(source, output) == output / "audio.wav"
    diagnostic = processor.last_extract_diagnostic
    assert diagnostic["status"] == "success"
    assert diagnostic["last_extract_error"] == ""
    assert diagnostic["command"][0] == "/test/ffmpeg"
    assert (output / "audio_extraction.json").stat().st_mode & 0o777 == 0o600
