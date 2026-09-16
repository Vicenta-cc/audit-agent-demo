"""No platform access: a real crawler subprocess receives synthetic responses."""
import json
import os
from pathlib import Path

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.crawler_adapter import (
    MediaCrawlerAdapter, CrawlerCollectionIncompleteError,
    CrawlerAuthenticationError, CrawlerVerificationError,
)


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
        adapter._run_command(command=['python','main.py'],save_root=tmp_path/'output',platform='dy',
                             account_id='test',max_notes=2,progress_callback=None,content_callback=None)
    assert not isinstance(error.value,(CrawlerAuthenticationError,CrawlerVerificationError))


@pytest.mark.parametrize('caught',[False,True])
def test_actual_crawler_preserves_good_post_and_rejects_incomplete_post(tmp_path,monkeypatch,caught):
    root=Path(os.environ.get('MEDIACRAWLER_RELIABILITY_ROOT',str(settings.media_crawler_dir))).resolve()
    python=root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not (root/'tools/collection_status.py').exists() or not python.exists():
        pytest.skip('requires paired MediaCrawler reliability branch and runtime')
    script=tmp_path/'fixture.py'
    script.write_text('''import sys,asyncio
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0,sys.argv[1])
import config
from var import crawler_type_var
from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.douyin.exception import MediaDownloadError
from tools.collection_status import CollectionIncompleteError
config.SAVE_DATA_PATH=sys.argv[2]
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
c.wait_for_content_slot=AsyncMock()
c.dy_client=DouYinClient.__new__(DouYinClient)
posts=[{'aweme_info':{'aweme_id':i,'desc':'fixture','images':[{'url_list':['https://fixture.invalid/image']} ]}} for i in ('123','456')]
c.dy_client.search_info_by_keyword=AsyncMock(return_value={'data':posts})
c.dy_client.get_aweme_media=AsyncMock(side_effect=[b'fixture-bytes',MediaDownloadError('403',403)])
try:asyncio.run(c.search())
except CollectionIncompleteError:
 if sys.argv[3]=='caught':print('COLLECTION_INCOMPLETE: fixture caught',file=sys.stderr)
 else:raise
''')
    output=tmp_path/'output'
    emitted=[]
    adapter=MediaCrawlerAdapter(root)
    with pytest.raises(CrawlerCollectionIncompleteError):
        adapter._run_command(command=[str(python),str(script),str(root),str(output),'caught' if caught else 'raise'],
            save_root=output,platform='dy',account_id='fixture',max_notes=2,
            progress_callback=None,content_callback=lambda contents,comments:emitted.extend(contents))
    assert [item['aweme_id'] for item in adapter.load_latest_output(output,'dy').contents]==['123']
    assert [item['aweme_id'] for item in emitted]==['123']
    statuses=[json.loads(p.read_text()) for p in (output/'douyin/collection_status').glob('media-*.json')]
    assert {p['content_id']:p['status'] for p in statuses}=={'123':'complete','456':'failed'}
    assert (output/'douyin/images/123/000.jpeg').exists()
    assert not (output/'douyin/images/456/000.jpeg').exists()
