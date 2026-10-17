from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from backend.audit_agent.geographic_media import GeographicMediaReadiness, GeographicMediaNotReady


@pytest.fixture
def media(tmp_path):
    store = GeographicMediaReadiness(tmp_path / 'geographic')
    binding = dict(job_id='job', attempt_id='attempt', content_key='post', content_digest='immutable')
    policy = dict(collect_media=True, image_urls=['https://one', 'https://two'], video_urls=[],
                  image_limit=2, analysis_scope='all')
    return store, binding, policy


def image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), 'red').save(path, format='JPEG')
    return path


def test_partial_indexed_images_are_completed_before_readiness_and_replay_is_local(media):
    store, binding, policy = media
    first = image(store.root / 'details/attempt/douyin/images/post/000.jpeg')
    calls = []
    def download(url, target):
        calls.append(url)
        return SimpleNamespace(path=image(target))
    result = store.prepare(binding, **policy, route=object(), download=download)
    assert calls == ['https://two']
    assert result['images'][0] == str(first)
    assert len(result['images']) == 2
    assert store.prepare(binding, **policy, route=None, download=lambda *_: pytest.fail('replay fetched')) == result


def test_missing_or_corrupt_readiness_never_implicitly_refetches(media):
    store, binding, policy = media
    with pytest.raises(GeographicMediaNotReady):
        store.prepare(binding, **policy, route=None, download=lambda *_: pytest.fail('no source'))
    result = store.prepare(binding, **policy, route=object(),
                           download=lambda url, target: SimpleNamespace(path=image(target)))
    Path(result['images'][0]).write_bytes(b'corrupt')
    with pytest.raises(GeographicMediaNotReady):
        store.prepare(binding, **policy, route=object(), download=lambda *_: pytest.fail('corrupt replay refetched'))


def test_disabled_media_does_not_download_and_policy_changes_fail_closed(media):
    store, binding, policy = media
    policy['collect_media'] = False
    assert store.prepare(binding, **policy, route=object(), download=lambda *_: pytest.fail('disabled')) == {'images': [], 'videos': []}
    with pytest.raises(GeographicMediaNotReady):
        store.load(binding, **(policy | {'collect_media': True}))
    with pytest.raises(GeographicMediaNotReady):
        store.load(binding, **(policy | {'image_limit': 1}))


def test_symlink_media_cannot_be_ready(media, tmp_path):
    store, binding, policy = media
    outside = image(tmp_path / 'outside.jpg')
    folder = store.root / 'details/attempt/douyin/images/post'
    folder.mkdir(parents=True)
    (folder / '000.jpeg').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        store.prepare(binding, **policy, route=object(), download=lambda *_: pytest.fail('symlink'))


def test_lease_failure_prevents_manifest_publication(media):
    store, binding, policy = media
    def fence():
        raise RuntimeError('lost lease')
    with pytest.raises(RuntimeError, match='lost lease'):
        store.prepare(binding, **policy, route=object(), fence=fence, download=lambda *_: pytest.fail('lease'))
    assert not store.path(binding).exists()


def test_arbitrary_partial_images_are_not_mistaken_for_required_source_index(media):
    store, binding, policy = media
    image(store.root / 'details/attempt/douyin/images/post/unrelated.jpeg')
    calls = []
    def download(url, target):
        calls.append(url)
        return SimpleNamespace(path=image(target))
    store.prepare(binding, **policy, route=object(), download=download)
    assert calls == policy['image_urls']


def test_crash_before_manifest_reconstructs_only_complete_local_media_without_route(media):
    store, binding, policy = media
    for i in range(2):
        image(store.root / f'details/attempt/douyin/images/post/{i:03d}.jpeg')
    result = store.prepare(binding, **policy, route=None, download=lambda *_: pytest.fail('recovery network'))
    assert len(result['images']) == 2


def test_unsafe_attempt_alias_cannot_select_sibling_media(media):
    store, binding, policy = media
    with pytest.raises(GeographicMediaNotReady, match='SUPPLEMENT_DEFERRED'):
        store.prepare(binding | {'attempt_id': 'a/../attempt'}, **policy,
                      route=object(), download=lambda *_: pytest.fail('unsafe identity'))


def test_media_without_source_url_is_explicitly_pending_instead_of_skipped(media):
    store, binding, policy = media
    local = store.root / 'details/attempt/douyin/videos/post/video.mp4'
    local.parent.mkdir(parents=True)
    local.write_bytes(b'not a real video')
    with pytest.raises(GeographicMediaNotReady) as failure:
        store.prepare(binding, **policy, route=object(), download=lambda *_: pytest.fail('ambiguous media'))
    assert 'no immutable source URL' in failure.value.reason


def test_real_video_decoder_rejects_truncation_and_header_only(tmp_path):
    import subprocess
    from backend.audit_agent.geographic_media import validate_media
    ffmpeg = Path('/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/bin/ffmpeg')
    if not ffmpeg.is_file():
        pytest.skip('local ffmpeg unavailable')
    video = tmp_path / 'valid.mp4'
    subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=black:s=32x32:r=2', '-t', '1', '-c:v', 'libx264', '-y', str(video)], check=True)
    validate_media(video, 'videos', ffmpeg=str(ffmpeg))
    truncated = tmp_path / 'truncated.mp4'
    truncated.write_bytes(video.read_bytes()[:video.stat().st_size // 2])
    with pytest.raises(ValueError, match='decode'):
        validate_media(truncated, 'videos', ffmpeg=str(ffmpeg))
    header = tmp_path / 'header.mp4'
    header.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 100)
    with pytest.raises(ValueError, match='decode'):
        validate_media(header, 'videos', ffmpeg=str(ffmpeg))


def test_corrupt_original_image_is_repaired_only_by_source_route_download(media):
    store, binding, policy = media
    original = store.root / 'details/attempt/douyin/images/post/000.jpeg'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'corrupt original')
    calls = []
    def download(url, target):
        calls.append(url)
        return SimpleNamespace(path=image(target))
    result = store.prepare(binding, **policy, route=object(), download=download)
    assert calls == policy['image_urls']
    assert len(result['images']) == 2
    assert str(original) not in result['images']


def test_corrupt_downloaded_image_remains_explicitly_pending(media):
    store, binding, policy = media
    def download(url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'corrupt downloaded image')
        return SimpleNamespace(path=target)
    with pytest.raises(GeographicMediaNotReady) as failure:
        store.prepare(binding, **policy, route=object(), download=download)
    assert 'image decode failed' in failure.value.reason
    assert not store.path(binding).exists()


def test_corrupt_original_without_live_source_stays_pending_without_download(media):
    store, binding, policy = media
    original = store.root / 'details/attempt/douyin/images/post/000.jpeg'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'corrupt original')
    with pytest.raises(GeographicMediaNotReady) as failure:
        store.prepare(binding, **policy, route=None,
                      download=lambda *_: pytest.fail('no original source capability'))
    assert 'original live source route' in failure.value.reason
    assert not store.path(binding).exists()


def test_competing_publisher_manifest_is_not_overwritten_after_initial_exists_check(media):
    import json
    store, binding, policy = media
    policy = policy | {'collect_media': False}
    path = store.path(binding)
    winner = []
    def fence():
        temporary = list(path.parent.glob('tmp*'))
        if temporary and not winner:
            # This callback runs after the loser's initial exists check and
            # fsynced temporary write, immediately before its publication.
            record = json.loads(temporary[0].read_text())
            winner.append(json.dumps(record, indent=4).encode() + b'\n')
            with path.open('xb') as handle:
                handle.write(winner[0])
    assert store.prepare(binding, **policy, route=None, fence=fence,
                         download=lambda *_: pytest.fail('media disabled')) == {'images': [], 'videos': []}
    assert path.read_bytes() == winner[0]
    assert list(path.parent.glob('tmp*')) == []


def test_competing_invalid_manifest_is_preserved_and_rejected(media):
    store, binding, policy = media
    policy = policy | {'collect_media': False}
    path = store.path(binding)
    def fence():
        if list(path.parent.glob('tmp*')) and not path.exists():
            path.write_bytes(b'not valid readiness')
    with pytest.raises(GeographicMediaNotReady):
        store.prepare(binding, **policy, route=None, fence=fence,
                      download=lambda *_: pytest.fail('media disabled'))
    assert path.read_bytes() == b'not valid readiness'
    assert list(path.parent.glob('tmp*')) == []


def test_prepublication_fence_failure_propagates_and_cleans_temporary_file(media):
    store, binding, policy = media
    policy = policy | {'collect_media': False}
    path = store.path(binding)
    def fence():
        if list(path.parent.glob('tmp*')):
            raise RuntimeError('lease lost before publication')
    with pytest.raises(RuntimeError, match='lease lost before publication'):
        store.prepare(binding, **policy, route=None, fence=fence,
                      download=lambda *_: pytest.fail('media disabled'))
    assert not path.exists()
    assert list(path.parent.glob('tmp*')) == []


@pytest.mark.parametrize('boundary', ['download_return', 'receipt_complete'])
def test_completed_supplement_recovers_offline_before_manifest(media, monkeypatch, boundary):
    import backend.audit_agent.geographic_media as module
    store, binding, policy = media
    image(store.root / 'details/attempt/douyin/images/post/000.jpeg')
    targets = []
    def download(url, target):
        targets.append(image(target))
        return SimpleNamespace(path=target)
    original_validate = module.validate_media
    def validate(path, kind, **kwargs):
        if boundary == 'download_return' and path in targets:
            raise RuntimeError('crashed after final download returned')
        return original_validate(path, kind, **kwargs)
    monkeypatch.setattr(module, 'validate_media', validate)
    original_link = module.os.link
    def link(source, target):
        if boundary == 'receipt_complete' and Path(target) == store.path(binding):
            raise RuntimeError('crashed before total manifest')
        return original_link(source, target)
    monkeypatch.setattr(module.os, 'link', link)
    with pytest.raises(RuntimeError, match='crashed'):
        store.prepare(binding, **policy, route=object(), download=download)
    assert not store.path(binding).exists()
    monkeypatch.setattr(module, 'validate_media', original_validate)
    monkeypatch.setattr(module.os, 'link', original_link)
    recovered = GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
        download=lambda *_: pytest.fail('offline recovery fetched'))
    assert recovered['images'][1] == str(targets[0])


def test_unrecorded_legacy_prepared_media_is_not_trusted(media):
    store, binding, policy = media
    for index in range(2):
        image(store.root / f'details/attempt/douyin/prepared/images/{index:03d}.image')
    with pytest.raises(GeographicMediaNotReady):
        store.prepare(binding, **policy, route=None, download=lambda *_: pytest.fail('offline'))


def interrupted_supplements(store, binding, policy, monkeypatch):
    """Leave real completed receipts with the final manifest still unpublished."""
    import backend.audit_agent.geographic_media as module
    original_link = module.os.link
    def link(source, target):
        if Path(target) == store.path(binding):
            raise RuntimeError('simulated manifest crash')
        return original_link(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'link', link)
        with pytest.raises(RuntimeError, match='simulated manifest crash'):
            store.prepare(binding, **policy, route=object(),
                          download=lambda url, target: SimpleNamespace(path=image(target)))
    return sorted(store.root.rglob('*.intent.json')), sorted(store.root.rglob('*.complete.json'))


@pytest.mark.parametrize('field', ['binding', 'policy', 'url', 'index', 'kind', 'path'])
def test_tampered_download_intent_cannot_recover(media, monkeypatch, field):
    import json
    store, binding, policy = media
    intents, _ = interrupted_supplements(store, binding, policy, monkeypatch)
    intent = json.loads(intents[0].read_text())
    if field == 'binding':
        intent['binding']['content_digest'] = 'another-content'
    elif field == 'policy':
        intent['policy']['image_limit'] = 999
    elif field == 'index':
        intent[field] = 99
    else:
        intent[field] = 'unapproved'
    intents[0].write_text(json.dumps(intent))
    with pytest.raises(GeographicMediaNotReady):
        GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
            download=lambda *_: pytest.fail('tampered intent fetched'))
    assert not store.path(binding).exists()


@pytest.mark.parametrize('change', ['content', 'policy', 'url', 'scope'])
def test_saved_intents_cannot_cross_current_binding_or_policy(media, monkeypatch, change):
    store, binding, policy = media
    interrupted_supplements(store, binding, policy, monkeypatch)
    if change == 'content':
        binding = binding | {'content_digest': 'changed'}
    elif change == 'policy':
        policy = policy | {'image_limit': 1}
    elif change == 'scope':
        policy = policy | {'analysis_scope': 'image_text'}
    else:
        policy = policy | {'image_urls': ['https://changed', 'https://two']}
    with pytest.raises(GeographicMediaNotReady):
        GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
            download=lambda *_: pytest.fail('changed policy fetched'))


@pytest.mark.parametrize('change', ['hash', 'path', 'bytes', 'symlink', 'intent_symlink', 'receipt_symlink'])
def test_recovery_rejects_tampered_receipt_or_final_media(media, monkeypatch, tmp_path, change):
    import json
    store, binding, policy = media
    intents, receipts = interrupted_supplements(store, binding, policy, monkeypatch)
    receipt = json.loads(receipts[0].read_text())
    target = store.root / receipt['file']['path']
    if change in {'hash', 'path'}:
        receipt['file']['sha256' if change == 'hash' else 'path'] = 'tampered'
        receipts[0].write_text(json.dumps(receipt))
    elif change == 'bytes':
        Image.new('RGB', (8, 8), 'blue').save(target, format='JPEG')
    else:
        path = {'symlink': target, 'intent_symlink': intents[0], 'receipt_symlink': receipts[0]}[change]
        outside = tmp_path / 'outside'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    with pytest.raises((GeographicMediaNotReady, ValueError)):
        GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
            download=lambda *_: pytest.fail('tampered local media fetched'))
    assert not store.path(binding).exists()


def test_partial_download_intent_remains_pending_offline(media):
    store, binding, policy = media
    calls = []
    def download(url, target):
        calls.append(url)
        if len(calls) == 1:
            return SimpleNamespace(path=image(target))
        # Strict downloader never publishes this unfinished temporary file.
        target.with_name('.' + target.name + '.partial').write_bytes(b'incomplete')
        return SimpleNamespace(path=None)
    with pytest.raises(GeographicMediaNotReady):
        store.prepare(binding, **policy, route=object(), download=download)
    assert len(list(store.root.rglob('*.intent.json'))) == 2
    assert len(list(store.root.rglob('*.complete.json'))) == 1
    with pytest.raises(GeographicMediaNotReady):
        GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
            download=lambda *_: pytest.fail('partial recovery fetched'))
    assert not store.path(binding).exists()


def test_delayed_predecessor_cannot_overwrite_successor_download(media):
    store, binding, policy = media
    policy = policy | {'image_limit': 1}
    targets = []
    winner = []
    def successor_download(url, target):
        targets.append(image(target))
        return SimpleNamespace(path=target)
    def predecessor_download(url, target):
        targets.append(target)
        winner.append(GeographicMediaReadiness(store.root).prepare(binding, **policy,
            route=object(), download=successor_download))
        # A late atomic replace still only touches the predecessor's nonce path.
        Image.new('RGB', (8, 8), 'blue').save(image(target), format='JPEG')
        return SimpleNamespace(path=target)
    result = store.prepare(binding, **policy, route=object(), download=predecessor_download)
    assert targets[0] != targets[1]
    assert result == winner[0] == {'images': [str(targets[1])], 'videos': []}
    with Image.open(targets[1]) as saved:
        assert saved.getpixel((0, 0))[0] > 200


def test_intent_is_published_before_download_and_fenced_off_invocation_is_offline(media):
    import json
    store, binding, policy = media
    def download(url, target):
        intents = list(target.parent.glob('*.intent.json'))
        assert len(intents) == 1
        intent = json.loads(intents[0].read_text())
        assert intent['binding'] == binding and intent['policy'] == policy
        assert intent['path'] == str(target.relative_to(store.root))
        raise RuntimeError('lease lost at download boundary')
    with pytest.raises(RuntimeError, match='lease lost at download boundary'):
        store.prepare(binding, **policy, route=object(), download=download)
    with pytest.raises(GeographicMediaNotReady):
        GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
            download=lambda *_: pytest.fail('intent granted networking'))


def test_consumer_load_does_not_reconstruct_missing_manifest(media, monkeypatch):
    store, binding, policy = media
    interrupted_supplements(store, binding, policy, monkeypatch)
    with pytest.raises(GeographicMediaNotReady):
        store.load(binding, **policy)
    assert not store.path(binding).exists()


@pytest.mark.parametrize('stage', ['decode', 'hash', 'receipt', 'load_hash'])
def test_value_error_fence_is_propagated_unchanged(media, monkeypatch, stage):
    import backend.audit_agent.geographic_media as module
    store, binding, policy = media
    error = ValueError('geographic crawl lease expired')
    if stage == 'load_hash':
        store.prepare(binding, **policy, route=object(),
                      download=lambda url, target: SimpleNamespace(path=image(target)))
    else:
        interrupted_supplements(store, binding, policy, monkeypatch)
    if stage == 'receipt':
        for path in store.root.rglob('*.complete.json'):
            path.unlink()
    active = []
    if stage in {'hash', 'load_hash', 'decode'}:
        attribute = 'validate_media' if stage == 'decode' else '_sha'
        original = getattr(module, attribute)
        def operation(*args, **kwargs):
            active.append(True)
            try:
                return original(*args, **kwargs)
            finally:
                active.pop()
        monkeypatch.setattr(module, attribute, operation)
    def fence():
        if active or (stage == 'receipt' and list(store.root.rglob('tmp*'))):
            raise error
    with pytest.raises(ValueError) as failure:
        store.prepare(binding, **policy, route=None, fence=fence,
                      download=lambda *_: pytest.fail('fence failure fetched'))
    assert failure.value is error
    if stage != 'load_hash':
        assert not store.path(binding).exists()


@pytest.mark.parametrize('boundary', ['download_return', 'receipt_complete'])
def test_real_video_supplement_recovers_offline(media, tmp_path, monkeypatch, boundary):
    import os
    import shutil
    import subprocess
    import backend.audit_agent.geographic_media as module
    store, binding, policy = media
    ffmpeg = Path('/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/bin/ffmpeg')
    if not ffmpeg.is_file():
        pytest.skip('local ffmpeg unavailable')
    source = tmp_path / 'local-video.mp4'
    subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=black:s=32x32:r=2', '-t', '1', '-c:v', 'libx264', '-y', str(source)], check=True)
    policy = policy | {'video_urls': ['https://immutable/video.mp4']}
    targets = []
    def download(url, target):
        targets.append(target)
        partial = target.with_name('.' + target.name + '.partial')
        shutil.copyfile(source, partial)
        os.replace(partial, target)
        return SimpleNamespace(path=target)
    original_validate = module.validate_media
    original_link = module.os.link
    def validate(path, kind, **kwargs):
        if boundary == 'download_return' and path in targets:
            raise RuntimeError('video worker crashed')
        return original_validate(path, kind, **kwargs)
    def link(source, target):
        if boundary == 'receipt_complete' and Path(target) == store.path(binding):
            raise RuntimeError('video worker crashed')
        return original_link(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'validate_media', validate)
        patch.setattr(module.os, 'link', link)
        with pytest.raises(RuntimeError, match='video worker crashed'):
            store.prepare(binding, **policy, route=object(), download=download, ffmpeg=str(ffmpeg))
    result = GeographicMediaReadiness(store.root).prepare(binding, **policy, route=None,
        download=lambda *_: pytest.fail('video recovery fetched'), ffmpeg=str(ffmpeg))
    assert result == {'images': [], 'videos': [str(targets[0])]}


@pytest.mark.parametrize('valid_winner', [True, False])
def test_competing_completion_receipt_is_never_overwritten(media, monkeypatch, valid_winner):
    import json
    import backend.audit_agent.geographic_media as module
    store, binding, policy = media
    policy = policy | {'image_limit': 1}
    original_link = module.os.link
    winners = []
    def link(source, target):
        if str(target).endswith('.complete.json') and not winners:
            record = json.loads(Path(source).read_text())
            data = json.dumps(record, indent=4).encode() if valid_winner else b'invalid winner'
            with Path(target).open('xb') as handle:
                handle.write(data)
            winners.append((Path(target), data))
        return original_link(source, target)
    monkeypatch.setattr(module.os, 'link', link)
    def prepare():
        return store.prepare(binding, **policy, route=object(),
            download=lambda url, target: SimpleNamespace(path=image(target)))
    if valid_winner:
        assert len(prepare()['images']) == 1
    else:
        with pytest.raises(GeographicMediaNotReady):
            prepare()
        assert not store.path(binding).exists()
    assert winners[0][0].read_bytes() == winners[0][1]
    assert not list(store.root.rglob('tmp*'))


def test_supplement_recovery_in_fresh_python_process_needs_no_memory_or_network(media, monkeypatch):
    import json
    import subprocess
    import sys
    import backend.audit_agent.geographic_media as module
    store, binding, policy = media
    targets = []
    original_validate = module.validate_media
    def download(url, target):
        targets.append(image(target))
        return SimpleNamespace(path=target)
    def validate(path, kind, **kwargs):
        if len(targets) == 2 and path == targets[-1]:
            raise RuntimeError('process stopped before final receipt')
        return original_validate(path, kind, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'validate_media', validate)
        with pytest.raises(RuntimeError, match='process stopped before final receipt'):
            store.prepare(binding, **policy, route=object(), download=download)
    assert len(list(store.root.rglob('*.intent.json'))) == 2
    assert len(list(store.root.rglob('*.complete.json'))) == 1
    assert not store.path(binding).exists()
    script = '''
import json
import socket
import sys
import requests
from backend.audit_agent.geographic_media import GeographicMediaReadiness
from backend.audit_agent import asset_utils, crawler_route

def forbidden(*args, **kwargs):
    raise AssertionError('fresh-process recovery attempted networking or download')

socket.socket = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
requests.sessions.Session.request = forbidden
asset_utils.download_url = forbidden
asset_utils.download_url_with_error = forbidden
crawler_route.authorize_source_route = forbidden
crawler_route.download_asset_hop = forbidden
root, binding, policy = json.loads(sys.argv[1])
result = GeographicMediaReadiness(root).prepare(binding, **policy, route=None, download=forbidden)
print(json.dumps(result))
'''
    completed = subprocess.run([sys.executable, '-c', script,
        json.dumps([str(store.root), binding, policy])], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {'images': [str(path) for path in targets], 'videos': []}
    assert len(list(store.root.rglob('*.complete.json'))) == 2
    assert store.path(binding).is_file()


def bad_png_crc(path):
    """Corrupt only IDAT's CRC so PIL.verify raises its decoder SyntaxError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), 'red').save(path, format='PNG')
    data = bytearray(path.read_bytes())
    offset = 8
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], 'big')
        if data[offset + 4:offset + 8] == b'IDAT':
            data[offset + 8 + size] ^= 1
            path.write_bytes(data)
            return path
        offset += size + 12
    raise AssertionError('fixture PNG has no IDAT chunk')


def test_bad_png_crc_in_original_is_repaired_with_live_source_route(media):
    store, binding, policy = media
    policy = policy | {'image_limit': 1}
    original = bad_png_crc(store.root / 'details/attempt/douyin/images/post/000.png')
    calls = []
    def download(url, target):
        calls.append(url)
        return SimpleNamespace(path=image(target))
    result = store.prepare(binding, **policy, route=object(), download=download)
    assert calls == ['https://one']
    assert len(result['images']) == 1
    assert result['images'][0] != str(original)


def test_bad_png_crc_in_original_stays_pending_without_source_route(media):
    store, binding, policy = media
    bad_png_crc(store.root / 'details/attempt/douyin/images/post/000.png')
    with pytest.raises(GeographicMediaNotReady) as failure:
        store.prepare(binding, **policy, route=None,
                      download=lambda *_: pytest.fail('bad PNG caused offline download'))
    assert 'original live source route' in failure.value.reason
    assert not store.path(binding).exists()


def test_bad_png_crc_in_download_stays_pending(media):
    store, binding, policy = media
    with pytest.raises(GeographicMediaNotReady) as failure:
        store.prepare(binding, **policy, route=object(),
            download=lambda url, target: SimpleNamespace(path=bad_png_crc(target)))
    assert 'image decode failed' in failure.value.reason
    assert not store.path(binding).exists()


def test_mp4_readiness_rejects_playlist_with_unhashed_sibling_dependency(tmp_path):
    import subprocess
    from backend.audit_agent.geographic_media import validate_media
    ffmpeg = Path('/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/bin/ffmpeg')
    if not ffmpeg.is_file():
        pytest.skip('local ffmpeg unavailable')
    sibling = tmp_path / 'sibling.mp4'
    subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=black:s=32x32:r=2', '-t', '1', '-c:v', 'libx264', '-y', str(sibling)], check=True)
    validate_media(sibling, 'videos', ffmpeg=str(ffmpeg))
    bound = tmp_path / 'bound.mp4'
    bound.write_text("ffconcat version 1.0\nfile 'sibling.mp4'\n")
    with pytest.raises(ValueError, match='video decode failed'):
        validate_media(bound, 'videos', ffmpeg=str(ffmpeg))
