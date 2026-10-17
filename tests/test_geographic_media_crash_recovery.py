"""Offline process-loss replay through the production geographic delivery path."""
from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
import socket
from types import SimpleNamespace

from PIL import Image
import pytest

from test_geographic_runtime_pipeline import Crawler, confirmed, runtime
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


class ProcessLost(BaseException):
    """Bypass normal error handling as an abruptly lost process would."""


@pytest.mark.parametrize('crash_at', ['after_download', 'before_manifest', 'after_delivery'])
def test_settled_media_recovery_is_local_and_delivery_remains_idempotent(
        confirmed, tmp_path, monkeypatch, crash_at):
    from backend.audit_agent import geographic_media, pipeline

    adapter, config = confirmed
    events, downloads, queued, prepared_bindings = [], [], [], []
    media_root = tmp_path / 'output'
    job_id = config['_verified_geographic_binding']['job_id']
    monkeypatch.setattr(pipeline, 'job_store', adapter.job_store)
    monkeypatch.setattr(socket.socket, 'connect',
                        lambda *_: pytest.fail('recovery attempted network'))

    class ImageCrawler(Crawler):
        def run_detail(self, **kwargs):
            output = super().run_detail(**kwargs)
            output.contents[0].update(aweme_type='68',
                note_download_url='https://offline.invalid/source-image.jpeg')
            return output

    lost, downloaded, recovering = [False], [False], [False]
    def download(url, target, **kwargs):
        assert not recovering[0], 'replay must never reacquire a route or redownload'
        assert kwargs['required_route'] is True and kwargs['route_context'] is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.partial')
        Image.new('RGB', (8, 8), 'red').save(temporary, format='JPEG')
        os.replace(temporary, target)  # Existing strict transport's final-file contract.
        downloads.append((url, str(target)))
        downloaded[0] = True
        return SimpleNamespace(path=target)
    monkeypatch.setattr(pipeline, 'download_url_with_error', download)

    original_link = geographic_media.os.link
    def publish(source, destination, *args, **kwargs):
        if (crash_at == 'before_manifest' and not lost[0]
                and Path(destination).parent == media_root / 'crawler/geographic/media_ready'):
            lost[0] = True
            raise ProcessLost('before final manifest publication')
        return original_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(geographic_media.os, 'link', publish)

    def make_runner():
        runner, _, _ = runtime(confirmed, tmp_path, events=events,
                               crawler=ImageCrawler(events))
        runner.root = media_root / 'crawler/geographic'
        runner.sink.raw_root = runner.root / 'delivered'
        def route(account):
            assert not recovering[0], 'settled receipt replay must not reacquire collection'
            return nullcontext(object())
        runner.route_operation = route
        audit = pipeline.AuditPipeline.__new__(pipeline.AuditPipeline)
        audit.job_id = job_id
        audit.ingestion = adapter.ingestion_store
        audit._geographic_collect_media = True
        audit._analysis_media_scope = lambda: 'all'
        audit._assert_geographic_lease = runner.fence
        audit.audio = SimpleNamespace(_resolve_ffmpeg=lambda: '')
        def prepare(item, *, binding, root, route, fence):
            prepared_bindings.append(deepcopy(binding))
            def check():
                fence()
                if crash_at == 'after_download' and downloaded[0] and not lost[0]:
                    lost[0] = True
                    raise ProcessLost('after final media download, before completion receipt')
            return audit._prepare_geographic_delivery(item, binding=binding, root=root,
                                                       route=route, fence=check)
        runner.prepare_delivery = prepare
        runner.on_delivery = lambda refs: queued.extend(refs)
        checkpoint = runner.execution.checkpoint
        def observe(stage, payload):
            checkpoint(stage, payload)
            if stage == 'after_delivery' and crash_at == stage and not lost[0]:
                lost[0] = True
                raise ProcessLost('after durable sink commit, before executor receipt')
        runner.execution.checkpoint = observe
        return runner, audit

    first, _ = make_runner()
    with pytest.raises(ProcessLost):
        first.run()
    assert len(downloads) == 1
    before = first.execution.snapshot()
    assert len(before['attempts']) == 1
    attempt = next(iter(before['attempts'].values()))
    assert attempt['status'] == 'settled'
    assert before['deliveries'] == {}
    memberships, _ = adapter.ingestion_store.validated_selection_for_task(job_id)
    assert len(memberships) == (1 if crash_at == 'after_delivery' else 0)
    calls_before = [(kind, kwargs.get('content_id')) for kind, kwargs in events]

    recovering[0] = True
    second, audit = make_runner()  # New in-memory objects; persistent SQLite/files only.
    report, output = second.run()
    assert report['handed_off'] == 1 and len(output.contents) == 1
    assert [(kind, kwargs.get('content_id')) for kind, kwargs in events] == calls_before
    assert len(downloads) == 1
    after = second.execution.snapshot()
    assert after['attempts'] == before['attempts']
    assert after['reservations'] == before['reservations']
    assert after['state'] == before['state']
    assert len(after['deliveries']) == 1
    memberships, refs = adapter.ingestion_store.validated_selection_for_task(job_id)
    assert len(memberships) == len(refs) == 1
    assert all(binding == prepared_bindings[0] for binding in prepared_bindings)
    ready = audit._geographic_ready_media('dy', 'same', media_root, True)
    assert ready == {'images': [downloads[0][1]], 'videos': []}
    with Image.open(ready['images'][0]) as image:
        image.load()
    assert second.run()[0] == report
    assert len(adapter.ingestion_store.validated_selection_for_task(job_id)[0]) == 1
    assert len(downloads) == 1
