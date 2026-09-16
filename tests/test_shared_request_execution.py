"""Exercise the real app subprocess boundary with a network-free crawler fixture."""
import json
import sys
from pathlib import Path

import pytest
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter, CrawlerRateLimitError
from backend.audit_agent.request_scheduler import RequestScheduler


@pytest.mark.parametrize('mode',['search','creator','rate_limited','rate_limited_clean_exit'])
def test_subprocess_uses_shared_gate_for_every_mode(tmp_path,monkeypatch,mode):
    db=tmp_path/'requests.sqlite3'
    for key,value in dict(request_scheduler_db=db,request_min_interval=0.0,requests_per_minute=1000,
                          request_concurrency=1,media_request_interval=0.0).items():
        monkeypatch.setattr(settings,key,value)
    script=tmp_path/'crawler_fixture.py'
    script.write_text('''import sys,os,asyncio
sys.path.insert(0, os.environ['FIXTURE_CRAWLER_ROOT'])
import httpx
from media_platform.douyin import client as m
async def run():
    c=m.DouYinClient(headers={},playwright_page=None,cookie_dict={})
    m.make_async_client=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(int(os.environ['FIXTURE_STATUS']),json={'status_code':0})))
    await c.request('GET','https://fixture.invalid/api')
try:
    asyncio.run(run())
except Exception:
    if os.environ.get('FIXTURE_CLEAN_EXIT') == '1':print('PLATFORM_RATE_LIMITED: shared cooldown')
    else:raise
''')
    monkeypatch.setenv('FIXTURE_CRAWLER_ROOT',str(settings.media_crawler_dir))
    monkeypatch.setenv('FIXTURE_STATUS','429' if mode.startswith('rate_limited') else '200')
    monkeypatch.setenv('FIXTURE_CLEAN_EXIT','1' if mode=='rate_limited_clean_exit' else '0')
    adapter=MediaCrawlerAdapter(tmp_path)
    crawler_python = settings.media_crawler_dir / ".venv" / "bin" / "python"
    monkeypatch.setattr(
        adapter,
        '_base_command',
        lambda platform: [str(crawler_python), str(script)],
    )
    common=dict(platform='dy',max_notes=1,max_comments=1,max_concurrency=1,max_items_per_minute=5,
                get_sub_comment=False,save_root=tmp_path/'output',account_id='fixture')
    if mode.startswith('rate_limited'):
        with pytest.raises(CrawlerRateLimitError,match='冷却'):
            adapter.run_search(keyword='fixture',start_page=0,**common)
    elif mode=='search':adapter.run_search(keyword='fixture',start_page=0,**common)
    else:adapter.run_creator(creator_id='fixture',**common)
    state=RequestScheduler(db,platform='dy',min_interval=0,per_minute=1000,media_interval=0)
    assert state.snapshot()['request_count_last_minute']==1
    assert state.snapshot()['in_flight']==0
    assert bool(state.snapshot()['cooldown_until']) == mode.startswith('rate_limited')


def test_stop_kills_launcher_and_waiting_descendant(tmp_path,monkeypatch):
    import os
    import signal
    import time
    worker=tmp_path/'worker.py';pid_file=tmp_path/'worker.pid';sent=tmp_path/'unexpected-request'
    worker.write_text('''import os,signal,time,sys
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(2)
Path(sys.argv[2]).write_text('request started after stop')
time.sleep(30)
''')
    launcher=tmp_path/'launcher.py'
    launcher.write_text('''import subprocess,sys,time
subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2],sys.argv[3]])
time.sleep(30)
''')
    adapter=MediaCrawlerAdapter(tmp_path)
    start=time.monotonic()
    try:
        adapter._run_command(command=[sys.executable,str(launcher),str(worker),str(pid_file),str(sent)],
            save_root=tmp_path/'out',platform='dy',max_notes=1,progress_callback=None,content_callback=None,
            account_id='fixture',stop_checker=lambda:pid_file.exists() or time.monotonic()-start>5)
        assert pid_file.exists()
        time.sleep(2.1)
        assert not sent.exists()
    finally:
        if pid_file.exists():
            try:os.kill(int(pid_file.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
