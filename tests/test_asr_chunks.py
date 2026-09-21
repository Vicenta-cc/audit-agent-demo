from array import array
import wave

import pytest

from backend.audit_agent.asr_chunks import ASROutOfMemoryError, transcribe_in_chunks
from backend.audit_agent.video_processor import DemoAudioProcessor


def audio(tmp_path, seconds):
    path = tmp_path / 'long.wav'
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 10, 0, 'NONE', 'not compressed'))
        output.writeframes(array('h', range(seconds * 10)).tobytes())
    return path


def inspect(path):
    with wave.open(str(path), 'rb') as source:
        data = array('h', source.readframes(source.getnframes()))
        return data[0] / 10, len(data) / 10


def transcript(path):
    offset, duration = inspect(path)
    words = [{'word': f'w{index}', 'start': index - offset, 'end': index + .2 - offset}
             for index in range(int(offset), int(offset + duration))]
    return {'provider': 'dolphin', 'text': ' '.join(word['word'] for word in words),
            'segments': [{'start': 0, 'end': duration, 'text': 'chunk', 'words': words}]}


@pytest.mark.parametrize('duration,count', [(740, 3), (1162, 4)])
def test_five_minute_chunks_overlap_and_restore_word_timeline(tmp_path, duration, count):
    path = audio(tmp_path, duration)
    calls = []
    temporary_paths = []
    def decode(chunk):
        temporary_paths.append(chunk)
        calls.append(inspect(chunk))
        return transcript(chunk)
    result = transcribe_in_chunks(path, decode, max_seconds=300)
    assert len(calls) == count
    assert all(length <= 300 for _, length in calls)
    assert all(calls[i-1][0] + calls[i-1][1] - calls[i][0] == 5 for i in range(1, count))
    assert calls[-1][0] + calls[-1][1] == duration
    words = [word for segment in result['segments'] for word in segment['words']]
    assert [word['word'] for word in words] == [f'w{i}' for i in range(duration)]
    assert [word['start'] for word in words] == list(range(duration))
    assert result['text'].split() == [f'w{i}' for i in range(duration)]
    assert result['chunk_count'] == count
    assert all(not chunk.exists() for chunk in temporary_paths)
    assert path.exists()


def test_oom_subdivides_only_failed_chunks_and_still_merges(tmp_path):
    calls = []
    def decode(chunk):
        window = inspect(chunk)
        calls.append(window)
        if window[1] > 75:
            raise ASROutOfMemoryError('GPU full')
        return transcript(chunk)
    result = transcribe_in_chunks(audio(tmp_path, 740), decode, max_seconds=300)
    assert len(calls) == len(set(calls)), 'An OOM window must never be repeated unchanged'
    assert result['oom_split_count'] > 0
    assert result['text'].split() == [f'w{i}' for i in range(740)]


def test_persistent_oom_stops_at_minimum_and_cleans_files(tmp_path):
    calls, paths = [], []
    def decode(chunk):
        calls.append(inspect(chunk)[1]); paths.append(chunk)
        raise RuntimeError('CUDA out of memory')
    original = audio(tmp_path, 740)
    with pytest.raises(ASROutOfMemoryError, match='缩短分段后仍失败'):
        transcribe_in_chunks(original, decode, max_seconds=300)
    assert calls == [300, 150, 75, 37.5, 18.7, 15]
    assert all(not path.exists() for path in paths)
    assert original.exists()


def test_non_oom_failure_does_not_publish_partial_transcript(tmp_path):
    calls = []
    def decode(chunk):
        calls.append(inspect(chunk))
        if len(calls) == 2:
            raise RuntimeError('provider denied request')
        return transcript(chunk)
    with pytest.raises(RuntimeError, match='provider denied'):
        transcribe_in_chunks(audio(tmp_path, 740), decode)
    assert len(calls) == 2


def test_dolphin_entry_uses_bounded_chunks_and_reuses_processor(tmp_path, monkeypatch):
    processor = DemoAudioProcessor()
    calls = []
    def decode(chunk):
        calls.append(inspect(chunk))
        return transcript(chunk)
    monkeypatch.setattr(processor, '_transcribe_dolphin_chunk', decode)
    monkeypatch.setattr(__import__('backend.audit_agent.config', fromlist=['settings']).settings, 'asr_chunk_seconds', 300)
    result = processor._transcribe_dolphin(audio(tmp_path, 740))
    assert len(calls) == 3
    assert len(result['text'].split()) == 740


def test_text_only_overlap_is_deduplicated_with_explicit_warning(tmp_path):
    results = iter([{'text': '开始 重叠上下文', 'segments': []},
                    {'text': '重叠上下文 结束', 'segments': []}])
    result = transcribe_in_chunks(audio(tmp_path, 400), lambda _: next(results), max_seconds=300)
    assert result['text'].count('重叠上下文') == 1
    assert 'lacks word timestamps' in result['timestamp_warning']


def test_repeated_words_at_different_times_are_preserved(tmp_path):
    def decode(chunk):
        result = transcript(chunk)
        for word in result['segments'][0]['words']:
            word['word'] = 'yes'
        return result
    result = transcribe_in_chunks(audio(tmp_path, 400), decode, max_seconds=300)
    assert result['text'].split() == ['yes'] * 400


def test_old_server_raw_word_timestamps_support_overlap_deduplication(tmp_path):
    def decode(chunk):
        result = transcript(chunk)
        result['raw'] = {'word_timestamps': result['segments'][0].pop('words')}
        return result
    result = transcribe_in_chunks(audio(tmp_path, 740), decode, max_seconds=300)
    assert result['text'].split() == [f'w{i}' for i in range(740)]
    assert not result.get('timestamp_warning')


def test_server_exhausted_subdivision_is_not_repeated_by_client(tmp_path):
    calls = []
    def decode(chunk):
        calls.append(inspect(chunk))
        raise ASROutOfMemoryError('server already reached minimum', subdivision_exhausted=True)
    with pytest.raises(ASROutOfMemoryError):
        transcribe_in_chunks(audio(tmp_path, 740), decode, max_seconds=300)
    assert calls == [(0, 300)]


def test_remote_client_sends_overlapping_bounded_wav_to_legacy_server(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import requests
    from backend.audit_agent.remote_inference import RemoteInferenceClient
    client = RemoteInferenceClient()
    windows = []
    def post(*args, **kwargs):
        chunk = kwargs['files']['audio'][1]
        windows.append(inspect(chunk.name))
        response = Mock(spec=requests.Response)
        response.status_code = 200
        response.json.return_value = transcript(chunk.name)
        return response
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(__import__('backend.audit_agent.config', fromlist=['settings']).settings, 'asr_chunk_seconds', 300)
    result = client.transcribe(audio(tmp_path, 1162))
    assert windows == [(0, 300), (295, 300), (590, 300), (885, 277)]
    assert result['text'].split() == [f'w{i}' for i in range(1162)]


def test_inference_endpoint_returns_structured_terminal_oom(tmp_path, monkeypatch):
    import asyncio
    from io import BytesIO
    from unittest.mock import Mock
    from fastapi import HTTPException, UploadFile
    from backend.audit_agent.config import settings
    # This module intentionally configures its standalone process at import.
    # Restore those flags so importing it here cannot affect other test suites.
    flags = ['use_remote_asr', 'use_remote_whisper', 'use_remote_vlm',
             'use_remote_llm', 'use_remote_translation', 'use_remote_mms_asr']
    original = {key: getattr(settings, key) for key in flags}
    try:
        from backend import inference_server
    finally:
        for key, value in original.items():
            setattr(settings, key, value)
    processor = Mock()
    processor.transcribe.side_effect = ASROutOfMemoryError('GPU full', subdivision_exhausted=True)
    monkeypatch.setattr(inference_server, 'get_audio_processor', lambda: processor)
    monkeypatch.setattr(settings, 'inference_api_key', '')
    upload = UploadFile(filename='audio.wav', file=BytesIO(audio(tmp_path, 1).read_bytes()))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(inference_server.transcribe(audio=upload, x_inference_key=None))
    assert caught.value.status_code == 422
    assert caught.value.detail['code'] == 'asr_gpu_out_of_memory'
    assert caught.value.detail['retryable'] is False
    assert caught.value.detail['subdivision_exhausted'] is True
    assert not processor.transcribe.call_args.args[0].exists()


@pytest.mark.parametrize('truncated_first_response', [False, True])
def test_merged_asr_passes_qwen_json_translation_and_keeps_global_timestamps(
    tmp_path, monkeypatch, truncated_first_response,
):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.config import settings
    from backend.audit_agent.pipeline import AuditPipeline
    from backend.audit_agent.qwen_client import QwenClient

    merged = transcribe_in_chunks(audio(tmp_path, 740), transcript, max_seconds=300)
    merged['language'] = 'ug'
    original_times = [(segment['start'], segment['end']) for segment in merged['segments']]
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = 'chunk-json-contract'
    pipeline.translator = SimpleNamespace(should_translate=lambda *args, **kwargs: True)
    parser = QwenClient.__new__(QwenClient)
    payloads = []
    def audit_text(prompt, **kwargs):
        payload = json.loads(prompt.split('输入 JSON：\n', 1)[1])
        payloads.append(payload)
        assert 'w0 ' in payload['full_source_text']
        assert 'w739' in payload['full_source_text']
        assert all(key not in payload for key in ('raw', 'words', 'chunk_count', 'oom_split_count'))
        if truncated_first_response and len(payloads) == 1:
            return parser._parse_chat_json('{"segments":[{"index":1,"translation_zh":"截断')
        indexes = payload.get('target_indexes') or list(range(1, 741))
        return parser._parse_chat_json(json.dumps({
            'segments': [{'index': index, 'translation_zh': f'译文{index}'} for index in indexes],
            'global_translation_zh': ' '.join(f'译文{index}' for index in indexes),
        }, ensure_ascii=False))
    pipeline.qwen = SimpleNamespace(audit_text=audit_text)
    monkeypatch.setattr(settings, 'asr_translate_engine', 'qwen_text')
    monkeypatch.setattr(pipeline_module.job_store, 'log', Mock())
    result = pipeline._translate_transcript_if_needed(
        pipeline._validated_authoritative_transcript(merged), '视频 1',
    )
    assert result['completion_status'] == 'completed'
    assert result['translation']['translated'] is True
    assert [segment['translation_zh'] for segment in result['segments']] == [f'译文{i}' for i in range(1, 4)]
    assert [(segment['start'], segment['end']) for segment in result['segments']] == original_times
    assert len(payloads) == (3 if truncated_first_response else 1)
    compact = pipeline._format_transcripts_for_prompt([{'transcript': result}])
    assert 'w739' in compact or 'w730' in compact
    assert all(key not in compact for key in ('"words"', 'chunk_count', 'oom_split_count'))
    persisted = pipeline._persist_asr_raw(result, tmp_path / 'asr_raw.json', tmp_path)
    assert 'raw' not in persisted and 'raw' not in persisted['translation']
    assert 'dolphin_raw' in json.loads((tmp_path / 'asr_raw.json').read_text())
