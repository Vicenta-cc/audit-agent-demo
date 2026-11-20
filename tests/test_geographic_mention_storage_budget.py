"""Offline process benchmark; run separately for each root with GEO_BUDGET_ROOT.

DB byte metrics are retained logical evidence, not physical filesystem writes.
"""
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get('GEO_BUDGET_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT/'tests'), str(ROOT), str(ROOT/'backend')]
MODE = os.environ.get('GEO_BUDGET_MODE', 'mention')
if MODE == 'legacy':
    from test_geographic_runtime_pipeline import confirmed
else:
    from test_geographic_mention_pipeline import confirmed
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_runtime_pipeline import runtime
import json
import resource
import socket
import sqlite3
import time
import pytest


def metrics(path):
    with sqlite3.connect(path) as db:
        count, size = db.execute('SELECT count(*),coalesce(sum(length(body)),0) FROM geographic_objects').fetchone()
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
    return dict(object_count=count, object_bytes=size, database_bytes=pages*page_size)


def test_offline_storage_budget(confirmed, tmp_path, monkeypatch):
    from backend.audit_agent.crawler_adapter import CrawlOutput
    from backend.audit_agent import geographic_storage as records
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: pytest.fail('network forbidden'))
    class Crawler:
        def __init__(self): self.calls = []
        def run_search(self, **kw):
            index = len(self.calls)
            self.calls.append(kw)
            ids = range(index*2, index*2+2) if MODE == 'mention' else range(6)
            items = [dict(aweme_id=str(i), title='北京市欠薪', desc='离线正文'*100, follower_count=1) for i in ids]
            comments = [dict(aweme_id=item['aweme_id'], comment_id=f"c-{item['aweme_id']}-{j}", content='离线评论'*40) for item in items for j in range(2)]
            extra = {}
            if MODE == 'mention':
                receipt = {k:v for k,v in kw['geographic_continuation'].items() if k != 'state_path'}
                receipt.update(status='complete', exhausted=False, count=len(items))
                extra['continuation'] = receipt
            return CrawlOutput('dy', items, comments, kw['save_root'], **extra)
        def run_detail(self, **kw): pytest.fail('outside-region candidates must not be selected')
    crawler = Crawler()
    runner, _, model = runtime(confirmed, tmp_path, crawler=crawler)
    monkeypatch.setattr(runner.store, 'load', lambda *a, **kw: pytest.fail('hot full snapshot load'))
    from backend.audit_agent.scoped_selection import digest
    frozen_plan_digest = digest(runner.execution.plan)
    if MODE == 'mention':
        assert runner.execution._evaluation_plan() is runner.execution.plan
    path = runner.store.path
    before = metrics(path)
    def catalog_nodes():
        with sqlite3.connect(path) as db:
            return [key for key, body in db.execute('SELECT hash,body FROM geographic_objects')
                    if b'\"matching_records\"' in bytes(body)]
    frozen_catalog_nodes = catalog_nodes() if MODE == 'mention' else []
    if MODE == 'mention':
        assert len(frozen_catalog_nodes) == 1
    started = time.perf_counter()
    report, output = runner.run()
    elapsed = time.perf_counter()-started
    after = metrics(path)
    assert digest(runner.execution.plan) == frozen_plan_digest
    if MODE == 'mention':
        assert catalog_nodes() == frozen_catalog_nodes
    assert not output.contents and not report['unfinished_queries']
    assert len(crawler.calls) == (3 if MODE == 'mention' else 1)
    assert sum('suspicion' in prompt for prompt, _ in model.calls) == 6
    # Summary reads verify the one shared profile blob without loading candidate
    # bodies or fetching thousands of individual administrative records.
    reads = []
    original = records.Tree.body
    def observed(tree, key):
        result = original(tree, key)
        reads.append(json.dumps(result, ensure_ascii=False))
        return result
    monkeypatch.setattr(records.Tree, 'body', observed)
    summary_started = time.perf_counter()
    for _ in range(5):
        runner.execution.report()
    summary_elapsed = time.perf_counter()-summary_started
    assert not any('离线正文' in row or '离线评论' in row for row in reads)
    assert len(reads) <= 200  # Five compact polls, independent of catalog size.
    result = dict(mode=MODE, source_root=str(ROOT), candidates=6, comments=12,
        capture_batches=len(crawler.calls), model_calls=len(model.calls), execution_seconds=elapsed,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
        before=before, after=after, object_bytes_added=after['object_bytes']-before['object_bytes'],
        database_bytes_added=after['database_bytes']-before['database_bytes'],
        five_summary_polls_seconds=summary_elapsed, summary_object_reads=len(reads),
        hot_full_load_forbidden=True, summary_candidate_reads=0,
        shared_catalog_nodes=len(frozen_catalog_nodes), evaluation_plan_unchanged=True)
    if os.environ.get('GEO_BUDGET_RESULT'):
        Path(os.environ['GEO_BUDGET_RESULT']).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
