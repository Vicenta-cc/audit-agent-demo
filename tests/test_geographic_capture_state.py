"""Continuation transport stays opt-in and preserves completed batch replay."""
import json
import os
import pytest
from backend.audit_agent import geographic_capture_state as state
from backend.audit_agent.crawler_adapter import CrawlOutput
from test_crawler_adapter_execution_settings import RecordingAdapter


def identity(tmp_path):
    return dict(state_path=str(tmp_path/'state.sqlite'), scope_id='a'*64, query_id='b'*64, batch_id='c'*64)


def test_private_task_directory_and_identity_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(state.settings, 'crawler_camoufox_state_root', tmp_path/'child')
    monkeypatch.setattr(state, 'child_process_options', lambda: {})
    monkeypatch.setattr(state, 'child_environment', lambda: (tmp_path/'child/outputs').mkdir(parents=True, exist_ok=True))
    result = state.prepare_continuation('a'*64, 'b'*64, 'c'*64)
    from pathlib import Path
    folder = Path(result['state_path']).parent
    assert folder.stat().st_uid == os.getuid()
    assert folder.stat().st_mode & 0o077 == 0
    assert state.prepare_continuation('a'*64, 'b'*64, 'c'*64) == result
    with pytest.raises(ValueError, match='identity'):
        state.prepare_continuation('../outside', 'b'*64, 'c'*64)
    folder.chmod(0o755)
    with pytest.raises(ValueError, match='ownership'):
        state.prepare_continuation('a'*64, 'b'*64, 'c'*64)


@pytest.mark.parametrize('bad', ['missing','conflicting','symlink','incomplete'])
def test_receipt_rejects_incomplete_or_unsafe_result(tmp_path, bad):
    expected = identity(tmp_path)
    folder = tmp_path/'douyin/collection_status'
    folder.mkdir(parents=True)
    receipt = {k:v for k,v in expected.items() if k!='state_path'} | dict(status='complete',count=0,exhausted=True)
    if bad != 'missing':
        path = folder/'run_geographic_summary.json'
        path.write_text(json.dumps(receipt | (dict(status='partial') if bad=='incomplete' else {})))
        if bad=='conflicting':
            (folder/'other_geographic_summary.json').write_text(json.dumps(receipt | dict(count=1)))
        if bad=='symlink':
            other=tmp_path/'external.json';path.rename(other);path.symlink_to(other)
    with pytest.raises(ValueError):
        state.read_continuation_receipt(tmp_path, expected)


@pytest.mark.parametrize('existing', [0,1,2])
def test_adapter_keeps_frozen_batch_limit_for_completed_replay(tmp_path, existing):
    expected = identity(tmp_path)
    class Adapter(RecordingAdapter):
        def _load_platform_output(self, root, platform):
            count = 2 if getattr(self, 'completed', False) else existing
            return CrawlOutput(platform,[dict(aweme_id=str(i),source_keyword='关键词') for i in range(count)],[],root)
        def _run_command(self, **kw):
            self.recorded_calls.append(kw)
            folder = kw['save_root']/'douyin/collection_status'
            folder.mkdir(parents=True,exist_ok=True)
            receipt = {k:v for k,v in expected.items() if k!='state_path'} | dict(status='complete',count=2,exhausted=False)
            (folder/'spool_geographic_summary.json').write_text(json.dumps(receipt))
            self.completed = True
            return CrawlOutput('dy',[dict(aweme_id=str(i),source_keyword='关键词') for i in range(2)],[],kw['save_root'])
    adapter=Adapter(tmp_path)
    result=adapter.run_search(platform='dy',keyword='关键词',start_page=0,max_notes=2,
        max_total_notes=2,max_comments=0,max_concurrency=1,max_items_per_minute=1,
        get_sub_comment=False,save_root=tmp_path/'output',geographic_continuation=expected)
    call,=adapter.recorded_calls
    assert call['max_notes']==2
    command=call['command']
    assert json.loads(command[command.index('--dy_geographic_continuation')+1])==expected
    assert result.continuation['count']==2 and len(result.contents)==2


def test_ordinary_search_does_not_receive_continuation(tmp_path):
    adapter=RecordingAdapter(tmp_path)
    result=adapter.run_search(platform='dy',keyword='关键词',start_page=0,max_notes=2,
        max_comments=0,max_concurrency=1,max_items_per_minute=1,get_sub_comment=False,save_root=tmp_path/'output')
    assert result.continuation is None
    assert '--dy_geographic_continuation' not in adapter.recorded_calls[0]['command']
