import asyncio
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.audit_agent import crawler_route as app
from test_managed_route_integration import managed,store_operation
from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter


@pytest.mark.parametrize('alteration',[{}, {'owner_id':'wrong'}, {'lease_version':True}, {'url':'https://secret.invalid/password'}, {'reason':'https://secret.invalid/password'}])
def test_actual_receipt_pipe_validates_private_guard_diagnostic(tmp_path,caplog,alteration):
    snapshot=managed(tmp_path)
    store=app.RouteOperationStore(tmp_path/'ops.db');store_operation(store,snapshot)
    bundle=SimpleNamespace(snapshot=snapshot,_lock=threading.RLock(),route_operation_store=store)
    pipe=app.RouteReceiptPipe(bundle)
    value=dict(type='browser_guard_stopped',operation_id=snapshot.operation_id,owner_id=snapshot.owner_id,
        lease_version=snapshot.lease_version,snapshot_digest=snapshot.snapshot_digest,context_id=snapshot.context_id,reason='PAGE_SIZES_FAILED')
    value.update(alteration)
    second={**value,'reason':'CDP_GUARD_TIMEOUT'}
    script='import os,sys;os.write(int(sys.argv[1]),sys.argv[2].encode())'
    child=subprocess.Popen([app.settings.crawler_login_python or '/Users/hose/project/zhanyun/media-crawler/.venv/bin/python','-c',script,str(pipe.write_fd),json.dumps(value)+'\n'+json.dumps(second)+'\n'],pass_fds=pipe.pass_fds)
    try:
        pipe.start(child);child.wait(timeout=5);pipe.thread.join(timeout=5)
        assert getattr(bundle,'browser_guard_first_reason',None)==(None if alteration else 'PAGE_SIZES_FAILED')
        assert 'secret.invalid' not in caplog.text and 'wrong' not in caplog.text
        assert not hasattr(bundle,'route_error') and not hasattr(bundle,'account_risk_recorded')
    finally:pipe.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[asyncio.TimeoutError(),RuntimeError('https://secret.invalid/password')])
async def test_actual_cdp_guard_reports_fixed_first_cause_before_page_close(failure):
    handlers={};events=[]
    async def send(method,params=None):
        if method=='Page.getFrameTree':return {'frameTree':{'frame':{'id':'main'}}}
        if method.startswith('Fetch.continue'):raise failure
    cdp=SimpleNamespace(send=send,on=lambda event,handler:handlers.update({event:handler}))
    session=SimpleNamespace(record_browser_guard_stop=lambda reason:events.append(reason))
    context=SimpleNamespace(new_cdp_session=AsyncMock(return_value=cdp))
    page=SimpleNamespace(is_closed=lambda:False,close=AsyncMock(side_effect=lambda:events.append('closed')))
    adapter=DouyinLoginAdapter(context,page,bound_proxy_session=session)
    await adapter.install_guard(page)
    handlers['Fetch.requestPaused']({'requestId':'private','frameId':'main','request':{'url':'https://www.douyin.com/'}})
    await asyncio.gather(*adapter.tasks)
    assert events==['CDP_GUARD_TIMEOUT' if isinstance(failure,asyncio.TimeoutError) else 'CDP_GUARD_FAILED','closed']
