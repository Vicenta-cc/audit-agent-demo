"""Ordinary media handoff is bound to one post and its exact detail attempt."""
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess

import pytest

from backend.audit_agent import geographic_media as media_module
from backend.audit_agent.crawler_route import CollectionRouteError
from backend.audit_agent.scoped_selection import digest
from test_geographic_media_readiness import image


@pytest.fixture
def triage_media(tmp_path):
    store = media_module.TriageMediaReadiness(tmp_path / 'job' / 'crawler')
    item = {'aweme_id': 'post', 'desc': 'original text', 'image_list': ['https://one', 'https://two']}
    binding = dict(job_id='job', platform='dy', content_key='post', content_digest=digest(item),
                   source_dir='rotation-A/details/post-attempt')
    item['triage_media_binding'] = binding
    policy = dict(collect_media=True, image_urls=['https://one', 'https://two'], video_urls=[],
                  image_limit=2, analysis_scope='all')
    folder = store.root / binding['source_dir'] / 'douyin'
    return store, binding, policy, folder, item


def forbidden_download(*_):
    pytest.fail('local replay or unauthorized recovery performed a download')


def test_complete_local_images_are_reused_without_route(triage_media):
    store, binding, policy, folder, _ = triage_media
    paths = [image(folder / f'images/post/{index:03d}.jpeg') for index in range(2)]
    result = store.prepare(binding, **policy, route=None, download=forbidden_download)
    assert result == {'images': list(map(str, paths)), 'videos': []}
    assert store.load(binding, **policy) == result
    assert 'attempt_id' not in binding


def test_partial_image_album_downloads_only_missing_source_index(triage_media):
    store, binding, policy, folder, _ = triage_media
    first = image(folder / 'images/post/000.jpeg')
    # An unrelated attempt's second image must never fill this attempt's hole.
    image(store.root / 'rotation-A/details/previous-attempt/douyin/images/post/001.jpeg')
    calls = []

    def download(url, target):
        calls.append(url)
        return SimpleNamespace(path=image(target))

    result = store.prepare(binding, **policy, route=object(), download=download)
    assert calls == ['https://two']
    assert result['images'][0] == str(first)
    assert len(result['images']) == 2
    assert Path(result['images'][1]).is_relative_to(folder)
    assert store.prepare(binding, **policy, route=None, download=forbidden_download) == result


def test_missing_media_recovery_requires_original_route(triage_media):
    store, binding, policy, folder, _ = triage_media
    image(folder / 'images/post/000.jpeg')
    with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        store.prepare(binding, **policy, route=None, download=forbidden_download)
    assert not store.path(binding).exists()


def test_disabled_media_never_downloads_but_remains_policy_bound(triage_media):
    store, binding, policy, _, _ = triage_media
    disabled = policy | {'collect_media': False}
    assert store.prepare(binding, **disabled, route=None, download=forbidden_download) == {'images': [], 'videos': []}
    with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        store.load(binding, **policy)


@pytest.mark.parametrize('source_dir', ['../outside', '/tmp/outside', 'rotation-A/../details', ''])
def test_source_directory_cannot_escape_or_alias_an_attempt(triage_media, source_dir):
    store, binding, policy, _, _ = triage_media
    with pytest.raises((CollectionRouteError, ValueError)):
        store.prepare(binding | {'source_dir': source_dir}, **policy, route=object(), download=forbidden_download)


def test_symlink_source_directory_is_rejected_even_with_valid_images(triage_media, tmp_path):
    store, binding, policy, folder, _ = triage_media
    outside = tmp_path / 'outside'
    for index in range(2):
        image(outside / f'douyin/images/post/{index:03d}.jpeg')
    folder.parent.parent.mkdir(parents=True)
    folder.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises((CollectionRouteError, ValueError)):
        store.prepare(binding, **policy, route=None, download=forbidden_download)


def test_ready_file_replacement_is_rejected_by_local_replay(triage_media):
    store, binding, policy, folder, _ = triage_media
    for index in range(2):
        image(folder / f'images/post/{index:03d}.jpeg')
    result = store.prepare(binding, **policy, route=None, download=forbidden_download)
    original = Path(result['images'][0])
    data = bytearray(original.read_bytes())
    data[-1] ^= 1  # Preserve file size: this must be caught by the bound hash.
    original.write_bytes(data)
    with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        store.load(binding, **policy)
    with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        store.prepare(binding, **policy, route=object(), download=forbidden_download)


@pytest.fixture
def real_video(tmp_path):
    bundled = Path('/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/bin/ffmpeg')
    ffmpeg = str(bundled) if bundled.is_file() else shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg is needed for the real video decoder fixture')
    path = tmp_path / 'valid.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=black:s=32x32:r=2', '-t', '1', '-c:v', 'libx264', '-y', str(path)], check=True)
    return ffmpeg, path


@pytest.mark.parametrize('repair', [False, True])
def test_corrupt_video_is_not_ready_until_valid_source_download(triage_media, real_video, repair):
    store, binding, policy, folder, _ = triage_media
    ffmpeg, valid_video = real_video
    policy = policy | {'image_urls': [], 'video_urls': ['https://video']}
    original = folder / 'videos/post/video.mp4'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 100)
    calls = []

    def download(url, target):
        calls.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(valid_video.read_bytes() if repair else original.read_bytes())
        return SimpleNamespace(path=target)

    if repair:
        result = store.prepare(binding, **policy, route=object(), download=download, ffmpeg=ffmpeg)
        assert result['images'] == []
        assert len(result['videos']) == 1
        assert result['videos'][0] != str(original)
        assert store.load(binding, **policy) == result
    else:
        with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
            store.prepare(binding, **policy, route=object(), download=download, ffmpeg=ffmpeg)
        assert not store.path(binding).exists()
    assert calls == ['https://video']


def test_binding_is_independent_of_geographic_attempt_identity(triage_media):
    store, binding, _, _, item = triage_media
    store.validate_item_binding(item, job_id='job', platform='dy', content_key='post')
    assert 'attempt_id' not in binding


@pytest.mark.parametrize('changes', [
    {'job_id': 'another-job'}, {'platform': 'xhs'}, {'content_key': 'another-post'},
])
def test_item_binding_cannot_move_between_job_platform_or_post(triage_media, changes):
    store, _, _, _, item = triage_media
    identity = dict(job_id='job', platform='dy', content_key='post') | changes
    with pytest.raises((CollectionRouteError, ValueError)):
        store.validate_item_binding(item, **identity)


def test_content_changes_cannot_reuse_prior_media_binding(triage_media):
    store, _, _, _, item = triage_media
    with pytest.raises((CollectionRouteError, ValueError)):
        store.validate_item_binding(item | {'desc': 'changed text'}, job_id='job', platform='dy', content_key='post')


def test_rehashed_wrong_post_still_cannot_claim_another_content_identity(triage_media):
    store, binding, _, _, item = triage_media
    replacement = {key: value for key, value in item.items() if key != 'triage_media_binding'}
    replacement['aweme_id'] = 'another-post'
    replacement['triage_media_binding'] = binding | {'content_digest': digest(replacement)}
    with pytest.raises((CollectionRouteError, ValueError)):
        store.validate_item_binding(replacement, job_id='job', platform='dy', content_key='post')


def test_empty_media_handoff_can_bind_root_without_reading_merged_files(triage_media):
    store, binding, policy, _, _ = triage_media
    assert store.prepare(binding | {'source_dir': '.'}, **(policy | {'collect_media': False}),
                         route=None, download=forbidden_download) == {'images': [], 'videos': []}
