"""Durable, local-only media handoff bound to approved geographic evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import uuid

from PIL import Image

from .crawler_route import CollectionRouteError
from .scoped_selection import digest


class GeographicMediaNotReady(CollectionRouteError):
    def __init__(self, reason):
        super().__init__('SUPPLEMENT_DEFERRED')
        self.reason = reason


def _contained(root, path):
    root, path = Path(root).resolve(), Path(path)
    path.absolute().relative_to(root)
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError('geographic media symlink is not allowed')
    path.resolve().relative_to(root)
    return path


class _InvalidMedia(ValueError):
    pass


def _sha(path, fence=lambda: None):
    h = hashlib.sha256()
    try:
        handle = path.open('rb')
    except OSError as exc:
        raise _InvalidMedia('geographic media read failed') from exc
    with handle:
        while True:
            try:
                chunk = handle.read(1024 * 1024)
            except OSError as exc:
                raise _InvalidMedia('geographic media read failed') from exc
            if not chunk:
                break
            fence()
            h.update(chunk)
    return h.hexdigest()


def validate_media(path, kind, *, ffmpeg='', fence=lambda: None):
    """Decode complete local files, never accept a container header as readiness."""
    fence()
    if not path.is_file() or not path.stat().st_size:
        raise _InvalidMedia('empty or missing geographic media')
    if kind == 'images':
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
        except (OSError, SyntaxError) as exc:
            # PIL uses OSError for corrupt/truncated images and SyntaxError
            # for bad PNG checksums. Keep only decoder calls inside this
            # handler so lease fence errors still propagate unchanged.
            raise _InvalidMedia('geographic image decode failed') from exc
    elif kind == 'videos':
        if not ffmpeg:
            raise _InvalidMedia('ffmpeg required for geographic media readiness')
        # Require self-contained MOV/MP4: auto-detected playlists could decode
        # sibling files whose bytes are absent from this asset's bound hash.
        with tempfile.TemporaryFile() as diagnostic, tempfile.TemporaryFile() as progress:
            process = subprocess.Popen([ffmpeg, '-nostdin', '-v', 'error', '-xerror',
                '-err_detect', 'explode', '-protocol_whitelist', 'file,pipe',
                '-f', 'mov', '-enable_drefs', '0', '-use_absolute_path', '0', '-i', str(path),
                '-map', '0:v:0', '-progress', 'pipe:1', '-f', 'null', '-'], stdout=progress, stderr=diagnostic)
            try:
                for _ in range(1200):
                    fence()
                    try:
                        code = process.wait(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                else:
                    raise _InvalidMedia('geographic media validation deadline exceeded')
                progress.seek(0)
                frames = [int(line.split(b'=', 1)[1]) for line in progress.read().splitlines()
                          if line.startswith(b'frame=')]
                if code or not frames or max(frames) < 1:
                    raise _InvalidMedia('geographic video decode failed')
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    else:
        raise _InvalidMedia('unsupported geographic media kind')
    fence()


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_record(root, path, record, fence):
    """Durable, atomic no-clobber publication for intents, receipts and manifests."""
    directory = _contained(root, path.parent)
    missing = []
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            name = handle.name
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        fence()
        try:
            os.link(name, _contained(root, path))
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


class GeographicMediaReadiness:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, binding):
        if any(not isinstance(binding.get(k), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', binding[k])
               for k in ('attempt_id', 'content_key')):
            raise GeographicMediaNotReady('invalid geographic media identity')
        return self.root / 'media_ready' / (digest(binding) + '.json')

    def load(self, binding, *, collect_media, image_urls, video_urls, image_limit, analysis_scope, fence=lambda: None):
        """Replay is local-only: never fetch, repair, or reinterpret saved evidence."""
        fence()
        try:
            record = json.loads(_contained(self.root, self.path(binding)).read_text())
            if (record['version'] != 1 or record['binding'] != binding or
                    record['collect_media'] is not collect_media or
                    record['image_limit'] != image_limit or record['analysis_scope'] != analysis_scope):
                raise ValueError('media readiness binding mismatch')
            required = self.required(collect_media, image_urls, video_urls, image_limit, analysis_scope)
            if record['required'] != [list(x) for x in required]:
                raise ValueError('required geographic media policy changed')
            files = record['files']
            if not collect_media and files:
                raise ValueError('media disabled in confirmed configuration')
            if [(f['kind'], f['index'], f['url']) for f in files] != [tuple(x) for x in record['required']]:
                raise ValueError('incomplete geographic required media subset')
            checked = []
            for entry in files:
                path = _contained(self.root, self.root / entry['path'])
                path.resolve().relative_to(self.root / 'details' / binding['attempt_id'] / 'douyin')
                if not path.is_file() or path.stat().st_size != entry['size']:
                    raise ValueError('geographic media changed after readiness')
                if not isinstance(entry['sha256'], str):
                    raise ValueError('invalid geographic media hash')
                checked.append((entry, path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GeographicMediaNotReady(str(exc)) from exc
        result = {'images': [], 'videos': []}
        for entry, path in checked:
            try:
                actual_hash = _sha(path, fence)
            except _InvalidMedia as exc:
                raise GeographicMediaNotReady(str(exc)) from exc
            if actual_hash != entry['sha256']:
                raise GeographicMediaNotReady('geographic media changed after readiness')
            result[entry['kind']].append(str(path))
        fence()
        return result

    @staticmethod
    def required(collect_media, image_urls, video_urls, image_limit, analysis_scope):
        if not collect_media or (video_urls and analysis_scope == 'image_text'):
            return []
        return ([('videos', 0, video_urls[0])] if video_urls else
                [('images', i, url) for i, url in enumerate(image_urls[:image_limit])])

    def _supplement(self, binding, policy, folder, kind, index, url, *, route,
                    download, ffmpeg, fence):
        # The strict source-route downloader publishes its final target only after
        # the whole transfer succeeds (private .partial -> atomic replace). The
        # durable intent precedes that call, closing even its return/receipt gap.
        context = dict(binding=binding, policy=policy)
        directory = _contained(self.root, folder / 'prepared' / digest(context))
        for intent_path in sorted(directory.glob('*.intent.json')):
            fence()
            try:
                intent = json.loads(_contained(self.root, intent_path).read_text())
                nonce = intent['nonce']
                if (intent['version'] != 1 or intent['binding'] != binding or intent['policy'] != policy
                        or not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce)
                        or intent_path.name != f"{nonce}.{digest(intent)}.intent.json"):
                    raise ValueError('geographic download intent binding mismatch')
                if (intent['kind'], intent['index'], intent['url']) not in self.required(**policy):
                    raise ValueError('geographic download intent source mismatch')
                suffix = '.mp4' if intent['kind'] == 'videos' else '.image'
                target = _contained(self.root, directory / (nonce + suffix))
                if intent['path'] != str(target.relative_to(self.root)):
                    raise ValueError('geographic download intent target mismatch')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise GeographicMediaNotReady(str(exc)) from exc
            if (intent['kind'], intent['index'], intent['url']) != (kind, index, url):
                continue
            receipt_path = intent_path.with_name(intent_path.name.replace('.intent.json', '.complete.json'))
            if not target.exists() and not receipt_path.exists():
                continue
            return self._complete_supplement(intent, target, receipt_path,
                                             kind, ffmpeg, fence)
        if route is None:
            raise GeographicMediaNotReady('missing required media needs original live source route')
        nonce = uuid.uuid4().hex
        target = _contained(self.root, directory / (nonce + ('.mp4' if kind == 'videos' else '.image')))
        intent = dict(version=1, **context, kind=kind, index=index, url=url, nonce=nonce,
                      path=str(target.relative_to(self.root)))
        intent_path = directory / f'{nonce}.{digest(intent)}.intent.json'
        if not _publish_record(self.root, intent_path, intent, fence):
            raise GeographicMediaNotReady('geographic download intent already exists')
        fence()
        # Never reuse a predecessor's target, even if its intent has no final file.
        result = download(url, target)
        if result.path is None:
            raise GeographicMediaNotReady('required geographic media download incomplete')
        chosen = _contained(self.root, Path(result.path))
        if chosen.resolve() != target.resolve():
            raise GeographicMediaNotReady('download result does not match approved attempt target')
        receipt_path = intent_path.with_name(intent_path.name.replace('.intent.json', '.complete.json'))
        return self._complete_supplement(intent, chosen, receipt_path,
                                         kind, ffmpeg, fence)

    def _complete_supplement(self, intent, target, receipt_path, kind, ffmpeg, fence):
        try:
            validate_media(target, kind, ffmpeg=ffmpeg, fence=fence)
        except _InvalidMedia as exc:
            raise GeographicMediaNotReady(str(exc)) from exc
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise GeographicMediaNotReady(str(exc)) from exc
        try:
            sha256 = _sha(target, fence)
        except _InvalidMedia as exc:
            raise GeographicMediaNotReady(str(exc)) from exc
        entry = dict(kind=kind, index=intent['index'], url=intent['url'], path=intent['path'],
                     size=size, sha256=sha256)
        receipt = dict(version=1, intent_digest=digest(intent), file=entry)
        # Sync recovered final bytes before making the durable receipt visible.
        with target.open('rb') as handle:
            os.fsync(handle.fileno())
        _fsync_directory(target.parent)
        if not receipt_path.exists():
            _publish_record(self.root, receipt_path, receipt, fence)
        try:
            saved = json.loads(_contained(self.root, receipt_path).read_text())
            if saved != receipt:
                raise ValueError('geographic download completion receipt mismatch')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GeographicMediaNotReady(str(exc)) from exc
        fence()
        return target

    def prepare(self, binding, *, collect_media, image_urls, video_urls, image_limit,
                route, download, ffmpeg='', fence=lambda: None, analysis_scope='all'):
        policy = dict(collect_media=collect_media, image_urls=image_urls, video_urls=video_urls,
                      image_limit=image_limit, analysis_scope=analysis_scope)
        path = self.path(binding)
        if path.exists():
            return self.load(binding, **policy, fence=fence)
        required = self.required(**policy)
        folder = _contained(self.root, self.root / 'details' / binding['attempt_id'] / 'douyin')
        if collect_media and not video_urls and any((folder / 'videos' / binding['content_key']).glob('*')):
            raise GeographicMediaNotReady('local video has no immutable source URL; reconcile before delivery')
        files = []
        for kind, index, url in required:
            fence()
            source_folder = _contained(self.root, folder / kind / binding['content_key'])
            # MediaCrawler image names preserve the source index. Arbitrary files
            # cannot prove which member of a partial image set they represent.
            candidates = (sorted(source_folder.glob(f'{index:03d}.*')) if kind == 'images'
                          else sorted(source_folder.glob('*.mp4')))
            chosen = None
            for candidate in candidates:
                candidate = _contained(self.root, candidate)
                try:
                    validate_media(candidate, kind, ffmpeg=ffmpeg, fence=fence)
                    chosen = candidate
                    break
                except _InvalidMedia:
                    continue
            if chosen is None:
                chosen = self._supplement(binding, policy, folder, kind, index, url,
                    route=route, download=download, ffmpeg=ffmpeg, fence=fence)
            files.append(dict(kind=kind, index=index, url=url,
                path=str(chosen.relative_to(self.root)), size=chosen.stat().st_size, sha256=_sha(chosen, fence)))
        fence()
        record = dict(version=1, binding=binding, collect_media=collect_media,
                      image_limit=image_limit, analysis_scope=analysis_scope, required=required, files=files)
        _publish_record(self.root, path, record, fence)
        return self.load(binding, **policy, fence=fence)
