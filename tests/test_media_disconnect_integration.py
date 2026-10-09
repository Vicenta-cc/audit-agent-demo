"""Real local TCP disconnect -> crawler process -> detail receipt -> ingestion.

Only candidate/detail metadata are fixtures. HTTP transport, retry, media storage,
subprocess exit parsing, keyword sweep and SQLite ingestion are production code.
No platform, model, existing database or account is used.
"""
import json
import os
import socket
import threading
from types import SimpleNamespace
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.audit_agent import pipeline as module
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter, CrawlerMediaDownloadError
from backend.audit_agent.ingestion import IngestionStore
from test_triaged_search import FakeEngine, _new_pipeline, _base_request


CHILD = r'''
import argparse, asyncio, os, sys
from pathlib import Path
from unittest.mock import AsyncMock
root, base = sys.argv[1:3]
sys.path.insert(0, root)
parser = argparse.ArgumentParser()
parser.add_argument('--save_data_path')
parser.add_argument('--specified_id')
args, _ = parser.parse_known_args(sys.argv[3:])
os.environ['MEDIACRAWLER_REQUEST_SCHEDULER_DB'] = str(Path(args.save_data_path) / 'scheduler.sqlite3')
os.environ.pop('MEDIACRAWLER_ACCOUNT_ID', None)
import config, httpx
config.SAVE_DATA_PATH = args.save_data_path
config.SAVE_DATA_OPTION = 'jsonl'
config.STREAM_ITEMS = True
config.ENABLE_GET_MEIDAS = True
config.ENABLE_GET_COMMENTS = False
config.DY_SPECIFIED_ID_LIST = [args.specified_id]
config.DY_MEDIA_MAX_URL_ATTEMPTS = 2
config.DY_REQUEST_MIN_INTERVAL = config.DY_MEDIA_REQUEST_INTERVAL = 0
config.DY_ACCOUNT_MIN_INTERVAL = config.DY_PACING_JITTER = 0
config.DY_REQUESTS_PER_MINUTE = config.DY_ACCOUNT_REQUESTS_PER_MINUTE = 10000
from media_platform.douyin import client
from media_platform.douyin.core import DouYinCrawler
from var import crawler_type_var
# Ignore workstation proxy settings; all HTTP stays on the loopback server.
client.make_async_client = lambda **kw: httpx.AsyncClient(trust_env=False, **kw)
crawler_type_var.set('detail')
c = DouYinCrawler()
c.dy_client = client.DouYinClient(headers={'User-Agent': 'isolated-regression'},
                                 playwright_page=None, cookie_dict={})
post = {'aweme_id': args.specified_id, 'desc': 'isolated test video',
        'video': {'play_addr': {'url_list': [base + '/' + args.specified_id + '/backup',
                                           base + '/' + args.specified_id + '/primary']}}}
c.get_aweme_detail = AsyncMock(return_value=post)
asyncio.run(c.get_specified_awemes())
'''


@pytest.mark.parametrize('scenario', ['transient', 'persistent_first', 'persistent_all'])
def test_disconnect_across_real_crawler_and_application(tmp_path, monkeypatch, scenario):
    root = Path(os.environ.get('MEDIACRAWLER_RELIABILITY_ROOT', str(settings.media_crawler_dir))).resolve()
    python = root / '.venv/bin/python'
    if not python.exists() or not (root / 'tools/collection_status.py').exists():
        pytest.skip('requires paired MediaCrawler runtime')
    counts = Counter()
    first, second = '7617896258897224154', '7617896258897224155'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            counts[self.path] += 1
            content_id = self.path.split('/')[1]
            fail = scenario == 'persistent_all' or (
                content_id == first and (scenario == 'persistent_first' or counts[self.path] == 1)
            )
            if fail:
                # Reproduce a peer closing before sending HTTP response headers.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            body = b'fixture-media-bytes'
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = tmp_path / 'crawler_fixture.py'
    script.write_text(CHILD)
    base = f'http://127.0.0.1:{server.server_port}'
    searches = []

    class Adapter(MediaCrawlerAdapter):
        def _subprocess_env(self, *args, **kwargs):
            # No production profile, key or account environment enters the fixture.
            return {'PATH': os.environ.get('PATH', ''), 'PYTHONUNBUFFERED': '1'}

        def _base_command(self, platform):
            return [str(python), str(script), str(root), base]

        def run_search(self, *, platform, keyword, save_root, **kwargs):
            searches.append(keyword)
            content_id = first if keyword == '词A' else second
            return CrawlOutput(platform, [{'aweme_id': content_id, 'desc': '上分'}], [], save_root)

    monkeypatch.setattr(module.settings, 'triage_mode', 'select')
    monkeypatch.setattr(module.job_store, 'log', lambda *a, **kw: None)
    monkeypatch.setattr(module, 'crawler_account_store', SimpleNamespace(get=lambda _: None))
    # Adapter CWD/locks and every database/output are isolated too.
    adapter = Adapter(tmp_path)
    ingestion = IngestionStore(tmp_path / 'audit.sqlite3')
    pipeline = _new_pipeline('disconnect-test', adapter, ingestion, FakeEngine())
    crawl_root = tmp_path / 'crawler'
    batches = []

    def ingest(contents, comments):
        batch = tmp_path / f'batch-{len(batches)}.json'
        batch.write_text(json.dumps({'task_id': 'disconnect-test', 'platform': 'dy',
                                    'items': contents, 'comments': comments}))
        batches.extend(ingestion.ingest_batch(batch, tmp_path / 'raw'))

    def sweep():
        return pipeline._run_triaged_search(
            request=_base_request(keyword='词A,词B', collect_comments=False), save_root=crawl_root,
            start_page=1, max_total_notes=10, crawler_concurrency=1, account_auth_state=None,
            crawler_account_id='', content_callback=ingest, stream_items=True,
            stop_checker=lambda: False, started_callback=None, progress_callback=None,
        )

    try:
        if scenario == 'persistent_all':
            with pytest.raises(CrawlerMediaDownloadError, match='未取得可研判结果'):
                sweep()
            expected = set()
        else:
            result = sweep()
            expected = {first, second} if scenario == 'transient' else {second}
            assert {item['aweme_id'] for item in result.contents} == expected
        assert searches == ['词A', '词B']
        assert {ref['content_key'] for ref in batches} == expected
        assert ingestion.stats_for_task('disconnect-test')['ingested_count'] == len(expected)
        logs = '\n'.join(path.read_text() for path in crawl_root.rglob('mediacrawler_stderr.log'))
        assert 'RemoteProtocolError' in logs
        assert 'MEDIA_TRANSIENT_RETRY' in logs
        assert counts[f'/{first}/primary'] == 2
        if scenario == 'transient':
            assert sum(counts.values()) == 3  # A fails once then recovers; B succeeds.
            assert not (crawl_root / 'keyword_failures.json').exists()
        else:
            assert counts[f'/{first}/backup'] == 2
            assert sum(counts.values()) == (8 if scenario == 'persistent_all' else 5)
            failures = json.loads((crawl_root / 'keyword_failures.json').read_text())
            assert set(failures) == ({'词A', '词B'} if scenario == 'persistent_all' else {'词A'})
            assert 'COLLECTION_INCOMPLETE' in logs
        receipts = [json.loads(p.read_text()) for p in (crawl_root / 'douyin/detail_completed').glob('*.json')]
        assert {r['contents'][0]['aweme_id'] for r in receipts} == expected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
