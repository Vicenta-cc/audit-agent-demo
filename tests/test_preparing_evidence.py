import json
import asyncio
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.crawler_douyin_login_v2 import DouyinLoginV2
from backend.audit_agent.config import settings


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['none','closed','screenshot','unsafe_dom'])
async def test_private_preparing_capture_once_is_bounded_and_secret_free(tmp_path,monkeypatch,failure):
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    values=dict(ready=True,body_length=18,panel_count=1,panel_visible=0,image_count=1,image_visible=0,image_loaded=0,button_count=1,button_clickable=0)
    page=SimpleNamespace(evaluate=AsyncMock(return_value=values),screenshot=AsyncMock())
    if failure=='closed':page.evaluate.side_effect=RuntimeError('secret-cookie')
    if failure=='screenshot':page.screenshot.side_effect=RuntimeError('secret-cookie')
    if failure=='unsafe_dom':page.evaluate.return_value={**values,'url':'https://secret.invalid/token'}
    session=object.__new__(DouyinLoginV2);session.args=SimpleNamespace(account_id='../account');session.operation_id='../operation';session.adapter=SimpleNamespace(page=page)
    await session.capture_preparing_evidence('preparing_failed')
    await session.capture_preparing_evidence('preparing_failed')
    assert page.evaluate.await_count==1
    directory=tmp_path/'crawler-login-diagnostics'
    assert stat.S_IMODE(directory.stat().st_mode)==0o700
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode)==0o600 and 'secret' not in path.read_text()
    if failure=='none':
        metadata=next(directory.glob('*.json'))
        assert json.loads(metadata.read_text())=={'stage':'preparing_failed',**values}
        assert page.screenshot.call_args.kwargs['full_page'] is False
    elif failure in {'closed','unsafe_dom'}:assert not list(directory.iterdir())


@pytest.mark.asyncio
async def test_actual_run_preparation_failure_captures_before_close_and_preserves_error(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    session=object.__new__(DouyinLoginV2);events=[]
    session.args=SimpleNamespace(account_id='account',timeout=1);session.operation_id='operation'
    session.stopped=asyncio.Event();session.phase='preparing'
    values=dict(ready=True,body_length=1,panel_count=0,panel_visible=0,image_count=0,image_visible=0,image_loaded=0,button_count=0,button_clickable=0)
    async def screenshot(**kwargs):events.append('capture');raise RuntimeError('private-secret')
    session.adapter=SimpleNamespace(page=SimpleNamespace(evaluate=AsyncMock(return_value=values),screenshot=screenshot))
    async def idle():await session.stopped.wait()
    async def failed():raise ValueError('LOGIN_PREPARATION_FAILED')
    session.read_controls=session.execute_commands=session.capture_frames=session.gesture_watchdog=idle
    session.flow=failed;session.diagnose_failure=lambda exc:None
    session.emit=lambda event,**kwargs:events.append(kwargs.get('code'))
    async def close():events.append('close')
    session.close_context=close;session.media=session.display=SimpleNamespace(close=lambda:None)
    await session.run()
    assert events==['capture','LOGIN_PREPARATION_FAILED','close']
