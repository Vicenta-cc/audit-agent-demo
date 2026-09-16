"""Both entry modes receive the same bounded collection failure behavior."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

import backend.audit_agent.pipeline as module
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import (
    CrawlOutput, CrawlerAuthenticationError, CrawlerVerificationError, CrawlerRateLimitError,
)
from backend.audit_agent.ingestion import IngestionStore, AuditResultStore
from backend.audit_agent.job_store import JobStore


def test_public_account_failure_logs_do_not_become_success_messages():
    from backend.investigation_creation.adapters import _public_job_logs
    for code, expected in [('crawler_account_verification_required', '平台验证'),
                           ('crawler_account_login_required', '登录态失效'),
                           ('crawler_rate_limited', '限流')]:
        logs = _public_job_logs([{'message': code + ': 采集账号异常', 'level': 'error'}])
        assert expected in logs[0]['message']
        assert '已完成' not in logs[0]['message']
        assert logs[0]['level'] == 'error'


@pytest.mark.parametrize('authoritative', [False, True])
@pytest.mark.parametrize('scenario', ['auth', 'rate_limit', 'empty', 'stop_then_verify', 'empty_then_verify'])
def test_collection_failure_policy_is_not_selected_by_entry(tmp_path, monkeypatch, authoritative, scenario):
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    accounts = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    cipher = AuthStateCipher(Fernet.generate_key())
    ids = []
    for name in ['first', 'second', 'third']:
        account = accounts.create(platform='dy', display_name=name)
        accounts.save_auth_state(account['id'], cipher.encrypt({'name': name}))
        ids.append(account['id'])
    config = dict(platform='dy', keyword='维汉夫妻', crawl_mode='search', max_notes=2,
        max_comments=0, max_concurrency=1, max_items_per_minute=1, start_page=1,
        get_sub_comment=False, analyze_limit=0, auto_analyze=False, run_crawler=True,
        crawler_account_id=ids[0], keyword_source='keyword', lexicon_category='soft',
        lexicon_keywords=[], analysis_batch_size=1, source_output_id=None,
        prompt_profile_snapshot={}, rule_snapshot={}, library_ids=[], capabilities=['text'],
        scoring_template='balanced', creator_id='', creator_url='', policy_id='')
    job = jobs.create(**config)
    monkeypatch.setattr(module, 'job_store', jobs)
    monkeypatch.setattr(module, 'crawler_account_store', accounts)
    monkeypatch.setattr(module, 'auth_state_cipher', cipher)
    monkeypatch.setattr(module.settings, 'outputs_dir', tmp_path / 'outputs')
    monkeypatch.setattr(module.settings, 'auto_analyze_crawled_content', False)
    calls = []
    class Crawler:
        def run_search(self, **kwargs):
            calls.append(kwargs)
            if scenario == 'auth':
                raise CrawlerAuthenticationError('synthetic auth')
            if scenario == 'rate_limit':
                raise CrawlerRateLimitError('synthetic rate limit')
            if scenario == 'stop_then_verify':
                jobs.update_control(job['id'], crawl_stop_requested=True)
                raise CrawlerVerificationError('synthetic verify')
            if scenario == 'empty_then_verify' and len(calls) >= 2:
                raise CrawlerVerificationError('synthetic verify')
            return CrawlOutput('dy', [], [], Path(kwargs['save_root']))
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = job['id']
    pipeline.crawler = Crawler()
    pipeline.ingestion = IngestionStore(tmp_path / 'content.sqlite3')
    pipeline.audit_results = AuditResultStore(tmp_path / 'content.sqlite3')
    pipeline.qwen = SimpleNamespace(provider_failure='')
    pipeline.run(SimpleNamespace(**config, _authoritative_m3_contract=authoritative))
    expected_calls = {
        'auth': 3,
        'rate_limit': 1,
        'empty': 2,
        'stop_then_verify': 1,
        'empty_then_verify': 3,
    }
    assert len(calls) == expected_calls[scenario]
    if scenario == 'auth':
        assert all(accounts.get(account_id)['status'] == 'expired' for account_id in ids)
        assert jobs.get(job['id'])['error'].startswith('crawler_account_login_required:')
    elif scenario == 'rate_limit':
        assert accounts.get(ids[0])['status'] == 'active'
        assert jobs.get(job['id'])['error'].startswith('crawler_rate_limited:')
    elif scenario == 'stop_then_verify':
        assert accounts.get(ids[0])['failure_kind'] == 'verify'
        assert jobs.get(job['id'])['status'] == 'crawl_paused'
        assert not jobs.get(job['id'])['error']
    elif 'verify' in scenario:
        selected = calls[-1]['account_id']
        assert accounts.get(selected)['status'] == 'active'
        assert accounts.get(selected)['failure_kind'] == 'verify'
        assert jobs.get(job['id'])['error'].startswith('crawler_account_verification_required:')
