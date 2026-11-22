"""No platform access: a real crawler subprocess receives synthetic responses."""
import json
import os
import time
from pathlib import Path

import pytest

from backend.audit_agent.config import settings
from test_crawler_adapter_output_handoff import handoff
from test_task_admission import stack
from backend.audit_agent.crawler_adapter import (
    MediaCrawlerAdapter, CrawlerCollectionIncompleteError,
    CrawlerMediaDownloadError,
    CrawlerAuthenticationError, CrawlerVerificationError,
)


def write_handoff_failure(item, marker, exit_code, reason=None):
    """Emit failures inside the private child so classification follows real handoff."""
    path = item.tmp_path / 'worker.py'
    source = path.read_text()
    failure = ""
    if reason is not None:
        failure += (
            "status=root/'douyin/collection_status/media-123.json'\n"
            "status.parent.mkdir(parents=True)\n"
            f"status.write_text(json.dumps({{'stage':'media','status':'failed','reason':{reason!r}}}))\n"
        )
    failure += f"print({marker!r},file=sys.stderr,flush=True)\nos._exit({exit_code})"
    terminal = "os._exit(7 if args.fixture_mode=='nonzero' else 0)"
    assert terminal in source
    path.write_text(source.replace(terminal, failure))


def test_scheduler_database_busy_is_a_recoverable_collection_failure(handoff, stack):
    from backend.audit_agent.job_state import failure_metadata
    from test_task_admission import make_run, worker_for
    from test_investigation_creation_m3 import FakeExecutionAdapter, FakeReportAdapter

    write_handoff_failure(handoff, 'tools.collection_route.CollectionRouteError: SCHEDULER_DATABASE_BUSY', 1)
    with pytest.raises(CrawlerCollectionIncompleteError, match='采集调度数据库繁忙') as exc:
        handoff.run()

    code, _, message = str(exc.value).partition(': ')
    assert code == 'crawler_scheduler_database_busy'
    metadata = failure_metadata(code, message)
    assert metadata['recovery_action'] == 'continue_crawl'
    creation, _, (owner, _) = stack
    task = make_run(creation, owner, 'database-busy')
    state = {**FakeExecutionAdapter.completed_state(ingested=1, completed=1),
             'status': 'failed', 'crawl_status': 'failed', 'error': str(exc.value),
             'control': {'failure': metadata}}
    reports = FakeReportAdapter()
    worker_for(creation, FakeExecutionAdapter(state), reports).run_once()
    saved = creation.get_run_for_worker(task.id)
    assert saved.status.value == 'INTERRUPTED' and saved.error_code == code
    assert '采集调度数据库繁忙' in saved.error_message
    assert reports.generate_calls == 0
    assert handoff.events.index('final') < handoff.events.index('cleanup')


def test_nonterminal_busy_marker_does_not_reclassify_another_failure(handoff):
    write_handoff_failure(handoff,
        'tools.collection_route.CollectionRouteError: SCHEDULER_DATABASE_BUSY\nRuntimeError: different failure', 1)
    with pytest.raises(RuntimeError, match='different failure') as exc:
        handoff.run()
    assert not isinstance(exc.value, CrawlerCollectionIncompleteError)


@pytest.mark.parametrize('exit_code', [0, 1])
@pytest.mark.parametrize('reason,expected', [('media_download_failed', True),
    ('incomplete_images', True), ('missing_video_url', True),
    ('TypeError', False), ('missing_or_mismatched_detail', False)])
def test_media_subtype_requires_structured_failure_record(handoff, exit_code, reason, expected):
    write_handoff_failure(handoff, 'COLLECTION_INCOMPLETE: fixture', exit_code, reason)
    with pytest.raises(CrawlerCollectionIncompleteError) as error:
        handoff.run()
    assert isinstance(error.value, CrawlerMediaDownloadError) is expected
    # The parent must classify the safely published record, not inspect private files.
    records = list((handoff.tmp_path / 'published/douyin/collection_status').glob('*_media-123.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text())['reason'] == reason
    assert handoff.events.index('final') < handoff.events.index('cleanup')


@pytest.mark.parametrize('exit_code', [0,1])
def test_incomplete_marker_is_typed_even_on_clean_exit(tmp_path,monkeypatch,exit_code):
    class Process:
        returncode=exit_code
        def __init__(self,*args,stderr,**kwargs):
            stderr.write('COLLECTION_INCOMPLETE: creator_pagination_stalled')
            stderr.flush()
        def poll(self):return self.returncode
    monkeypatch.setattr('backend.audit_agent.crawler_adapter.subprocess.Popen',Process)
    adapter=MediaCrawlerAdapter(tmp_path)
    with pytest.raises(CrawlerCollectionIncompleteError) as error:
        adapter._run_command(command=['python','main.py'],save_root=tmp_path/'output',platform='xhs',
                             account_id='test',max_notes=2,progress_callback=None,content_callback=None)
    assert not isinstance(error.value,(CrawlerAuthenticationError,CrawlerVerificationError))


def test_media_download_stack_timeout_is_typed_without_crawler_marker(handoff):
    marker = """Traceback (most recent call last):
  File "core.py", line 1044, in get_aweme_video
    content = await self._download_media_with_retry(url, budget)
  File "core.py", line 1008, in _download_media_with_retry
    return await self.dy_client.get_aweme_media(url, raise_on_error=True)
  File "client.py", line 624, in get_aweme_media
    response = await self._send("GET", url, operation='media')
  File "tools/persistent_request_gate.py", line 230, in slot
    async with asyncio.timeout(timeout):
TimeoutError
"""
    write_handoff_failure(handoff, marker, 1)
    with pytest.raises(CrawlerMediaDownloadError, match='媒体下载超时'):
        handoff.run()
    assert handoff.events.index('final') < handoff.events.index('cleanup')


def test_unscoped_timeout_remains_infrastructure_failure(handoff):
    write_handoff_failure(handoff, 'Traceback: search_api\nTimeoutError\n', 1)
    with pytest.raises(RuntimeError, match='MediaCrawler failed') as error:
        handoff.run()
    assert not isinstance(error.value, CrawlerCollectionIncompleteError)
    assert handoff.events.index('final') < handoff.events.index('cleanup')


@pytest.mark.parametrize('caught',[False,True])
def test_actual_crawler_preserves_good_post_and_rejects_incomplete_post(tmp_path,monkeypatch,caught):
    root=Path(os.environ.get('MEDIACRAWLER_RELIABILITY_ROOT',str(settings.media_crawler_dir))).resolve()
    python=Path(os.environ.get('MEDIACRAWLER_RELIABILITY_PYTHON', str(root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))))
    if not (root/'tools/collection_status.py').exists() or not python.exists():
        pytest.skip('requires paired MediaCrawler reliability branch and runtime')
    script=tmp_path/'fixture.py'
    script.write_text('''import sys,asyncio,argparse,time
from pathlib import Path
from unittest.mock import AsyncMock
parser=argparse.ArgumentParser()
parser.add_argument('--fixture-crawler',required=True)
parser.add_argument('--fixture-result',required=True)
parser.add_argument('--save_data_path',required=True)
args,_=parser.parse_known_args()
sys.path.insert(0,args.fixture_crawler)
from tools.bound_proxy import initialize_bound_route,obtain_route_ack,BoundProxySession
route=initialize_bound_route();ack=obtain_route_ack(route);route.require_managed_ack(ack)
session=BoundProxySession(route,ack=ack)
from tools.collection_route import ProbeReceipt
session.context_id=route.context_id
session.receipt=ProbeReceipt(route.proxy_id,route.config_revision,route.context_id,'203.0.113.9',time.time(),route.exit_generation)
import config
from var import crawler_type_var
from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.douyin.exception import MediaDownloadError
from tools.collection_status import CollectionIncompleteError
config.SAVE_DATA_PATH=args.save_data_path
config.SAVE_DATA_OPTION='jsonl'
config.STREAM_ITEMS=True
config.ENABLE_GET_MEIDAS=True
config.ENABLE_GET_COMMENTS=False
config.CRAWLER_MAX_NOTES_COUNT=2
config.START_PAGE=0
config.KEYWORDS='test'
config.DY_SKIP_AWEME_IDS_FILE=''
config.DY_REUSABLE_CONTENT_DB=''
crawler_type_var.set('search')
c=DouYinCrawler()
c._bound_proxy_session=session
c.wait_for_content_slot=AsyncMock()
c.dy_client=DouYinClient.__new__(DouYinClient)
c.dy_client._bound_proxy_session=session
posts=[{'aweme_info':{'aweme_id':i,'desc':'fixture','images':[{'url_list':['https://fixture.invalid/image']} ]}} for i in ('123','456')]
c.dy_client.search_info_by_keyword=AsyncMock(return_value={'data':posts})
async def fixture_media(url, output_path, *, raise_on_error=False):
 output_path=Path(output_path)
 if output_path.parent.name=='456':raise MediaDownloadError('403',403)
 output_path.parent.mkdir(parents=True,exist_ok=True)
 output_path.write_bytes(b'fixture-bytes')
 return output_path
c.dy_client.get_aweme_media=AsyncMock(side_effect=fixture_media)
try:asyncio.run(c.search())
except CollectionIncompleteError:
 if args.fixture_result=='caught':print('COLLECTION_INCOMPLETE: fixture caught',file=sys.stderr)
 else:raise
''')
    output=tmp_path/'output'
    emitted=[]
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent import crawler_route
    from crawler_auth_fixtures import mark_camoufox_login_verified
    for name,value in dict(media_crawler_dir=root,crawler_camoufox_state_root=tmp_path/'state',
                           crawler_browser_profile_root=tmp_path/'profiles',task_resource_lock_dir=tmp_path/'locks',
                           request_scheduler_db=tmp_path/'scheduler.sqlite3',crawler_browser_maintenance=False,
                           crawler_safety_enabled=True,crawler_proxy_enabled=False,app_auth_mode='disabled').items():
        monkeypatch.setattr(settings,name,value)
    accounts=CrawlerAccountStore(tmp_path/'accounts.sqlite3')
    account=accounts.create(platform='dy',display_name='incomplete fixture')
    accounts.save_auth_state(account['id'],'fixture-encrypted-auth')
    mark_camoufox_login_verified(accounts,account['id'])
    monkeypatch.setattr(crawler_route,'_run_maintenance_probe',lambda bundle,**kwargs:
        crawler_route.route_contract().ProbeReceipt(bundle.snapshot.proxy_id,bundle.snapshot.config_revision,
            'offline-incomplete-fixture','203.0.113.9',time.time()))
    bundle=crawler_route.acquire_route_execution(account['id'],'incomplete-fixture','fixture-owner',account_store=accounts)
    adapter=MediaCrawlerAdapter(root,account_store=accounts)
    try:
        with pytest.raises(CrawlerCollectionIncompleteError):
            adapter._run_command(command=[str(python),str(script),'--fixture-crawler',str(root),
                '--fixture-result','caught' if caught else 'raise','--save_data_path',str(output)],
                save_root=output,platform='dy',account_id=account['id'],max_notes=2,route_context=bundle,
                progress_callback=None,content_callback=lambda contents,comments:emitted.extend(contents))
    finally:
        bundle.close()
    assert [item['aweme_id'] for item in adapter.load_latest_output(output,'dy').contents]==['123']
    assert [item['aweme_id'] for item in emitted]==['123']
    statuses=[json.loads(p.read_text()) for p in (output/'douyin/collection_status').glob('*.json')]
    statuses=[item for item in statuses if item['stage']=='media']
    assert {p['content_id']:p['status'] for p in statuses}=={'123':'complete','456':'failed'}
    assert (output/'douyin/images/123/000.jpeg').exists()
    assert not (output/'douyin/images/456/000.jpeg').exists()


@pytest.mark.parametrize('exit_code', [0, 1])
@pytest.mark.parametrize('markers,expected', [
    ('ACCOUNT_AUTH_INVALID: expired', CrawlerAuthenticationError),
    ('ACCOUNT_AUTH_INVALID: expired\nACCOUNT_VERIFY: verification', CrawlerVerificationError),
])
def test_child_account_marker_precedence(handoff, exit_code, markers, expected):
    write_handoff_failure(handoff, markers, exit_code)
    with pytest.raises(expected):
        handoff.run()
