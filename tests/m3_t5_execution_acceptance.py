"""Isolated real Qwen, HTTP Confirm, Worker and unmodified AuditPipeline acceptance."""
import argparse
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-env', type=Path, required=True)
    parser.add_argument('--account-db', type=Path, required=True)
    parser.add_argument('--auth-key-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', choices=['xhs', 'dy'], default='xhs')
    parser.add_argument('--integrated', action='store_true',
                        help='T1-T5 A: real Qwen-generated Recall; B: existing Recall and formal RuleSet.')
    parser.add_argument('--wait-for-process', type=int,
                        help='Allow authoring now, but serialize real execution after an earlier acceptance process exits.')
    parser.add_argument('--existing-recall-db', type=Path,
                        help='Read-only source of the already-saved gambling Recall resource for integrated B.')
    parser.add_argument('--cases', nargs='+', choices=list('ABCDEFGH'), default=list('ABCDEFGH'))
    parser.add_argument('--approved-content-evidence', type=Path,
                        help='Reuse recorded real A inline content for controlled B-H setup only.')
    args = parser.parse_args()
    if args.integrated and not set(args.cases) <= {'A', 'B'}:
        parser.error('--integrated requires --cases A B (or one of them)')
    if not args.integrated and 'A' not in args.cases and args.approved_content_evidence is None:
        parser.error('B-H-only runs require --approved-content-evidence')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dotenv import load_dotenv
    load_dotenv(args.credentials_env, override=True)
    evidence = {'stage': 'M3_PHASE_T5', 'pass': False, 'requested_cases': args.cases,
                'approved_content_evidence': args.approved_content_evidence.name if args.approved_content_evidence else None,
                'cases': [], 'qwen_turns': [],
                'scope': 'Real Qwen A authoring; HTTP Confirm; real Worker, AuditPipeline, crawler and providers. B-D use controlled durable T4B setup; no scripted execution.'}
    if args.integrated:
        evidence.update(stage='M3_T1_T5_INTEGRATED', scope='A: real Qwen queries options, generates temporary Recall and Proposal, later user adoption. B: existing resource options and Application Draft command. Both: HTTP Confirm and real Worker/Pipeline/crawler/providers.')
    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='m3-t5-real-') as temporary:
        root = Path(temporary)
        for key, value in {'XHS_AUDIT_DATA_DIR': root, 'XHS_AUDIT_OUTPUTS_DIR': root/'outputs',
                           'HERMES_HOME': root/'hermes', 'CRAWLER_AUTH_KEY_FILE': root/'crawler_auth.key'}.items():
            os.environ[key] = str(value)
        shutil.copyfile(args.auth_key_file, root/'crawler_auth.key')
        (root/'crawler_auth.key').chmod(0o600)
        # The existing fixture and real pipeline resolve the same isolated database.
        (root/'audit_index.sqlite3').symlink_to(root/'resources.sqlite3')
        from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn, _t1_temporary_arguments
        from backend.investigation_creation.principal import Principal
        from backend.investigation_creation.adapters import AuditPipelineExecutionAdapter
        from backend.investigation_creation.worker import InvestigationWorker
        from backend.investigation_creation.contracts import CreateDraftCommand, QueryInvestigationOptions
        from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools, M3_MUTATION_TOOL_NAMES
        from backend.audit_agent.pipeline import AuditPipeline
        from backend.audit_agent.job_store import JobStore
        from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
        from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
        fixture = creation_stack.__wrapped__(root)
        stack = next(fixture); app = stack['app_service']; conversation = stack['conversation']
        principal = Principal('principal-a'); conversation.fake_runtime = False
        configure_hermes_investigation_creation_tools(stack['tool_service'], principal_provider=conversation.principal_for_session)
        # Copy only existing authorized encrypted accounts; never export them.
        with sqlite3.connect(f'file:{args.account_db}?mode=ro', uri=True) as source, sqlite3.connect(stack['resource_db']) as target:
            source.row_factory = sqlite3.Row
            accounts = source.execute("SELECT * FROM crawler_accounts WHERE platform=? AND status='active' AND auth_state_ciphertext IS NOT NULL", (args.platform,)).fetchall()
            assert accounts, 'No authorized active account'
            target.execute('DELETE FROM crawler_accounts')
            for row in accounts:
                keys = list(row.keys())
                target.execute('INSERT INTO crawler_accounts ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')', tuple(row))
        jobs = JobStore(stack['resource_db']); revisions = TaskAuditConfigRevisionStore(stack['resource_db'])
        ingestion = IngestionStore(stack['resource_db']); results = AuditResultStore(stack['resource_db'])
        def table(name):
            with sqlite3.connect(stack['resource_db']) as db:
                db.row_factory = sqlite3.Row
                return [dict(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
        formal_before = {name: table(name) for name in ['rule_sets','rule_set_revisions','rule_set_publish_idempotency']}
        if args.existing_recall_db:
            assert args.integrated and args.cases == ['B']
            lexicons = app.resource_service.lexicon_store
            with sqlite3.connect(f'file:{args.existing_recall_db}?mode=ro', uri=True) as source, sqlite3.connect(stack['resource_db']) as target:
                source.row_factory = sqlite3.Row
                expected_terms = lexicons.enabled_main_terms('gambling', connection=source)
                expected_hash = lexicons.runtime_content_hash('gambling', connection=source)
                assert expected_terms
                target.execute("DELETE FROM lexicon_keywords WHERE category_id='gambling'")
                for row in source.execute("SELECT * FROM lexicon_keywords WHERE category_id='gambling' ORDER BY id"):
                    keys = [key for key in row.keys() if key != 'id']
                    target.execute('INSERT INTO lexicon_keywords ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')', tuple(row[key] for key in keys))
            assert lexicons.enabled_main_terms('gambling') == expected_terms
            assert lexicons.runtime_content_hash('gambling') == expected_hash
            evidence['existing_recall_source'] = {'method': 'Read-only copy of existing saved entries into isolated resource DB, before Draft creation; no new terms generated.',
                'lexicon_id': 'gambling', 'enabled_main_terms': expected_terms, 'runtime_content_hash': expected_hash}
        def turn(session, label, message):
            events = []
            original_read = stack['tool_service'].execute
            original_mutation = stack['tool_service'].execute_with_identity
            def observe_read(name, arguments, **kwargs):
                result = original_read(name, arguments, **kwargs)
                if name not in M3_MUTATION_TOOL_NAMES:
                    events.append({'name': name, 'arguments': deepcopy(arguments), 'result': deepcopy(result)})
                return result
            def observe_mutation(name, arguments, **kwargs):
                result = original_mutation(name, arguments, **kwargs)
                events.append({'name': name, 'arguments': deepcopy(arguments), 'result': deepcopy(result)})
                return result
            accepted, _ = conversation.accept_message(session.id, client_message_id=label, content=message, principal=principal)
            try:
                with patch.object(stack['tool_service'], 'execute', observe_read), patch.object(stack['tool_service'], 'execute_with_identity', observe_mutation), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = conversation.execute_turn(accepted.id)
            except Exception as exc:
                saved = conversation.store.get_turn(accepted.id)
                evidence['qwen_turns'].append({'label': label, 'message': message, 'status': saved.status,
                    'session_id': session.id, 'turn_id': accepted.id, 'events': events,
                    'answer': '', 'artifact': saved.public_artifact, 'failure_type': type(exc).__name__})
                save()
                raise
            saved = conversation.store.get_turn(accepted.id)
            evidence['qwen_turns'].append({'label': label, 'message': message, 'status': saved.status,
                'session_id': session.id, 'turn_id': accepted.id, 'events': events,
                'answer': result.answer, 'artifact': saved.public_artifact})
            save(); print(label, saved.status, [event['name'] for event in events], flush=True)
            assert saved.status == 'completed'
            return saved.public_artifact
        consumed = []
        pipeline_observed = []
        def pipeline_factory(*, job_id):
            pipeline = AuditPipeline(job_id=job_id)
            original = pipeline._verified_m3_snapshot
            def observe():
                value = original()
                consumed.append({'job_id': job_id, 'execution': deepcopy(value)})
                return value
            pipeline._verified_m3_snapshot = observe
            original_context = pipeline._set_prompt_context
            def observe_context(category, prompts):
                pipeline_observed.append({'job_id': job_id, 'rule_snapshot': deepcopy(pipeline.rule_snapshot),
                    'prompt_profile_snapshot': deepcopy(prompts), 'audit_config_revision_id': pipeline.audit_config_revision_id})
                return original_context(category, prompts)
            pipeline._set_prompt_context = observe_context
            return pipeline
        execution = AuditPipelineExecutionAdapter(job_store=jobs, revision_store=revisions,
            crawler_account_store=app.resource_service.crawler_account_store,
            ingestion_store=ingestion, audit_result_store=results, pipeline_factory=pipeline_factory)
        worker = InvestigationWorker(stack['creation_store'], execution_adapter=execution, report_adapter=None, session_adapter=None)
        def confirm_execute(label, draft, after_confirm=None, unavailable=False):
            if args.wait_for_process:
                print(label, 'waiting for earlier real acceptance execution to exit', flush=True)
                while True:
                    try:
                        os.kill(args.wait_for_process, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(1)
            preview = stack['client'].get(f'/api/investigation-drafts/{draft.id}/confirmation-preview')
            record = {'case': label, 'draft': draft.model_dump(mode='json'), 'preview': preview.json(), 'pass': False}
            evidence['cases'].append(record); save()
            if preview.status_code != 200 or not preview.json()['can_confirm']:
                record['status'] = 'PREVIEW_BLOCKED'; save(); return
            with ExitStack() as context:
                if unavailable:
                    context.enter_context(patch.object(app, 'get_ruleset_proposal', side_effect=AssertionError('Proposal read forbidden')))
                    context.enter_context(patch.object(stack['creation_store'], '_owned_proposal', side_effect=AssertionError('Proposal store forbidden')))
                response = stack['client'].post(f'/api/investigation-drafts/{draft.id}/confirm-and-queue',
                    headers={'Idempotency-Key': label}, json={'expected_revision': draft.current_revision, 'confirmed': True})
                record['confirm'] = {'status_code': response.status_code, 'response': response.json(),
                    'request': {'expected_revision': draft.current_revision, 'confirmed': True}}
                if response.status_code != 202:
                    record.update(status='CONFIRM_REJECTED', status_code=response.status_code)
                    save(); return
                run_id = response.json()['run_id']; run = stack['creation_store'].get_run_for_worker(run_id)
                record['run'] = run.model_dump(mode='json'); save()
                if after_confirm: after_confirm()
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    completed = worker.run_once()
                record['status'] = completed.status.value
                record['error_code'] = completed.error_code
                record['consumed'] = [x for x in consumed if x['job_id'] == execution.job_id_for_run(run_id)]
                record['pipeline_observed'] = [x for x in pipeline_observed if x['job_id'] == execution.job_id_for_run(run_id)]
                record['completed_run'] = completed.model_dump(mode='json')
                job = jobs.get(execution.job_id_for_run(run_id))
                if job:
                    record['job'] = {key: job.get(key) for key in ['id','status','rule_snapshot','prompt_profile_snapshot','current_audit_config_revision_id']}
                    record['audit_config_revision'] = revisions.get(job['current_audit_config_revision_id']) if job.get('current_audit_config_revision_id') else None
                    record['audit_results'] = execution.get_job_state(job['id'])['audit_results']
                    record['audit_result_provenance'] = [
                        {key: row[key] for key in ['id', 'job_id', 'content_key', 'prompt_version', 'audit_config_revision_id']}
                        for row in table('audit_results') if row['job_id'] == job['id']]
                    diagnostics = '\n'.join(str(log.get('message', '')) for log in job.get('logs', [])).lower()
                    record['diagnostic_categories'] = [key for key in [
                        'timeout', 'timed out', '429', 'rate limit', 'quota', '401', '403',
                        'unauthorized', 'connection', 'json', 'parse'] if key in diagnostics]
                    crawler_logs = list((root/'outputs'/job['id']).rglob('mediacrawler_*.log'))
                    record['crawler_diagnostics'] = [{'file': path.name, 'bytes': path.stat().st_size,
                        'categories': [key for key in ['error', 'timeout', 'captcha', 'login', 'account_auth_invalid', 'search', 'comment', 'retryerror', 'datafetcherror', 'traceback', 'is empty']
                                       if key in path.read_text(errors='replace').lower()],
                        # This known crawler logger emits only search terms, page and
                        # selected content IDs, never authentication or response bodies.
                        'search_events': [line[line.index('[DouYinCrawler.search]'):][:500]
                                          for line in path.read_text(errors='replace').splitlines()
                                          if '[DouYinCrawler.search]' in line]} for path in crawler_logs]
                    record['pipeline_milestones'] = [log.get('message', '') for log in job.get('logs', [])
                        if str(log.get('message', '')).startswith(('crawler output loaded:', 'crawl limits requested:', '词库展开 '))]
                save(); print(label, record['status'], record['error_code'], flush=True)
                if completed.status.value != 'AUDIT_COMPLETED':
                    return
                verified = execution.verify_run_job(completed)
                assert record['consumed'] and record['consumed'][0]['execution'] == verified
                assert record['pipeline_observed'] == [{'job_id': job['id'],
                    'rule_snapshot': verified['rule_snapshot'], 'prompt_profile_snapshot': verified['prompt_profile_snapshot'],
                    'audit_config_revision_id': verified['_verified_audit_config_revision_id']}]
                assert record['audit_results'], 'No durable audit result'
                assert all(row['audit_config_revision_id'] == job['current_audit_config_revision_id']
                           and row['prompt_version'] == verified['prompt_profile_snapshot']['prompt_version']
                           for row in record['audit_result_provenance'])
                if draft.configuration.judgement.strategy == 'temporary_ruleset':
                    assert run.confirmed_configuration['temporary_ruleset'] == draft.configuration.judgement.model_dump(mode='json')
                elif args.integrated:
                    import subprocess
                    import types
                    baseline = 'ad47e537013c38fe031ec1bab9bd7486767e49cb'
                    module = types.ModuleType('t5_baseline_contracts')
                    sys.modules[module.__name__] = module
                    source = subprocess.check_output(['git', 'show', baseline+':backend/investigation_creation/contracts.py'], text=True)
                    exec(compile(source, '<baseline-contracts>', 'exec'), module.__dict__)
                    frozen = run.confirmed_configuration
                    assert 'temporary_ruleset' not in frozen
                    assert module.ConfirmedConfigurationSnapshotV4.model_validate(frozen).model_dump(mode='json') == frozen
                    selection = draft.configuration.judgement
                    assert frozen['ruleset_revision']['id'] == selection.ruleset_revision_id
                    assert frozen['ruleset_revision']['version'] == selection.expected_ruleset_version
                    assert frozen['ruleset_revision']['content_hash'] == selection.expected_ruleset_content_hash
                    record['baseline_formal_reader_hash_shape_pass'] = True
                recall = draft.configuration.investigation.recall_plan
                terms = list(recall.terms if recall.strategy == 'temporary_terms' else recall.enabled_main_terms)
                assert run.confirmed_configuration['resolved_search_terms'] == terms
                assert verified['keyword'] == ','.join(terms)
                record['pass'] = True; save()
        try:
            if 'A' in args.cases:
                session = conversation.create_session(principal=principal, workspace_key='t5-real')
                platform_name = '小红书' if args.platform == 'xhs' else '抖音'
                message = (f'帮我在{platform_name}搜索调查招聘诈骗。先查询系统现有调查选项；如果没有适合这个目标的召回词库，请你生成一个最贴近目标的规范临时搜索词供本次调查使用，不要保存成词库。如果没有合适的正式规则，请生成一套完整规则给我看。现在只准备配置和展示规则，等我后续批准再绑定，先不要开始调查。'
                           if args.integrated else f'帮我在{platform_name}搜索调查招聘诈骗，使用临时搜索词招聘收费。如果没有合适的规则，生成一套给我看看。')
                turn(session, 'A-generate', message)
                if args.integrated:
                    turn(session, 'A-refine-recall', '请先修正临时召回词：去掉宽泛主题词、同义重复和变体，不要凑数量。只生成并保留一个能直接检索到实际招聘收费行为的核心搜索概念，使用一个规范说法。后续草案只使用这个最终搜索词。规则先保持原样，不要采用，也不要开始调查。')
                turn(session, 'A-edit', '增加一条：以安排工作为由要求先交培训费或办理培训贷，需要识别风险。先展示修改后的完整规则。')
                adopted = turn(session, 'A-adopt',
                    '可以，采用你已完整展示的当前规则创建调查草案。召回只使用你前面已经收敛的那一个规范搜索词；不要增加其他词，先不开始调查。'
                    if args.integrated else '可以，就按现在这套规则创建调查草案。')
                if not adopted.get('draft_id'):
                    adopted = turn(session, 'A-adopt-confirm', '确认，采用你前面已经完整展示的这套规则，创建调查草案；平台和搜索词保持前面指定的配置。')
                assert adopted.get('draft_id'), 'Qwen did not bind Draft'
                draft = app.get_draft(adopted['draft_id'], principal=principal)
                assert draft.configuration.judgement.strategy == 'temporary_ruleset'
                approved_content = draft.configuration.judgement.content.model_dump(mode='json')
                if args.integrated:
                    plan = draft.configuration.investigation.recall_plan
                    assert plan.strategy == 'temporary_terms' and plan.terms
                    assert len(plan.terms) == 1, 'The later user correction requires exactly one canonical core concept'
                    assert all(term == term.strip() and ',' not in term and '，' not in term for term in plan.terms)
                    assert len(plan.terms) == len(set(plan.terms))
                    assert any(e['name'] == 'query_investigation_options' for t in evidence['qwen_turns'] for e in t['events'])
                    adoption = next(t for t in reversed(evidence['qwen_turns']) if any(e['name'] == 'use_ruleset_proposal' and e['result']['status'] == 'ok' for e in t['events']))
                    use = next(e for e in adoption['events'] if e['name'] == 'use_ruleset_proposal' and e['result']['status'] == 'ok')
                    presented_turn, presentation = next((t, p) for t in evidence['qwen_turns'] for p in t['artifact'].get('proposal_presentations', []) if p['presentation_id'] == use['arguments']['presentation_id'])
                    assert evidence['qwen_turns'].index(presented_turn) < evidence['qwen_turns'].index(adoption)
                    assert presented_turn['status'] == 'completed'
                    source = draft.configuration.judgement.model_dump(mode='json')
                    assert source['content'] == presentation['snapshot']['content']
                    assert all(source[key] == presentation[key] for key in ['proposal_id', 'proposal_version', 'content_hash'])
                    with sqlite3.connect(stack['creation_store'].db_path) as db:
                        db.row_factory = sqlite3.Row
                        approvals = [dict(row) for row in db.execute('SELECT * FROM ruleset_proposal_approvals')]
                        assert db.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0
                    assert not jobs.list() and approvals
                    evidence['integrated_binding'] = {'presentation': presentation, 'presentation_turn_id': presented_turn['turn_id'],
                        'adoption_turn_id': adoption['turn_id'], 'approval_audits': approvals, 'recall': plan.model_dump(mode='json')}
                    save()
                confirm_execute('A', draft, unavailable=args.integrated)
            elif not args.integrated:
                previous = json.loads(args.approved_content_evidence.read_text())
                accepted = next(case for case in previous['cases'] if case['case'] == 'A' and case['pass'])
                approved_content = accepted['draft']['configuration']['judgement']['content']
            def seeded(label):
                with patch.object(conversation, 'fake_runtime', True):
                    initial = deepcopy(approved_content); initial['audit_goal'] += ' 初稿'
                    first = _run_scripted_creation_turn(stack, content='生成候选', client_message_id=label+'-setup',
                        actions=[('create_ruleset_proposal', {'content': initial})])
                    record = first['turn'].public_artifact['proposal_presentations'][0]
                    edited = _run_scripted_creation_turn(stack, session_id=first['session_id'], client_message_id=label+'-edit',
                        content='修改并展示第二版', actions=[('update_ruleset_proposal', {'proposal_id':record['proposal_id'],
                            'expected_version':1, 'content':approved_content})])
                    record = edited['turn'].public_artifact['proposal_presentations'][0]
                    assert record['proposal_version'] == 2
                    adopted = _run_scripted_creation_turn(stack, session_id=first['session_id'], client_message_id=label+'-adopt',
                        content='就用这套', actions=[('use_ruleset_proposal', {'presentation_id': record['presentation_id'],
                            'create_draft': {'title':'招聘诈骗调查', 'objective':'识别招聘收费风险', 'configuration': {
                                'platform':args.platform, 'investigation':{'mode':'search','recall_plan':{'strategy':'temporary_terms','terms':['招聘收费'],'source_lexicon_ids':[]}}}}})])
                return app.get_draft(adopted['turn'].public_artifact['draft_id'], principal=principal), first['session_id']
            def change(draft, session_id):
                changed = draft.configuration.judgement.content.model_dump(mode='json'); changed['audit_goal'] += ' 后续修改'
                app.update_ruleset_proposal(draft.configuration.judgement.proposal_id, session_id=session_id,
                    expected_version=draft.configuration.judgement.proposal_version, content=changed)
            if 'B' in args.cases:
                if args.integrated:
                    from test_investigation_creation_conversation import _search_draft_from_options
                    options = app.query_investigation_options(QueryInvestigationOptions(include_lexicon_terms_for_ids=['gambling']), principal=principal).model_dump(mode='json')
                    arguments = _search_draft_from_options([options])
                    arguments['configuration']['platform'] = args.platform
                    evidence['formal_options'] = options
                    b = app.create_draft(CreateDraftCommand.model_validate(arguments), principal=principal)
                    assert b.configuration.investigation.recall_plan.strategy == 'existing_lexicon'
                    confirm_execute('B', b)
                else:
                    b, sid = seeded('B'); change(b, sid); confirm_execute('B', b)
            if 'C' in args.cases:
                c, sid = seeded('C'); confirm_execute('C', c, unavailable=True)
            if 'D' in args.cases:
                d, sid = seeded('D'); confirm_execute('D', d, after_confirm=lambda: change(d, sid))
            for label in ['E','F','G']:
                if label not in args.cases: continue
                negative, sid = seeded(label)
                before = len(jobs.list())
                with sqlite3.connect(stack['creation_store'].db_path) as db:
                    runs_before = db.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0]
                with ExitStack() as context:
                    if label == 'F':
                        config = negative.configuration.model_dump(mode='json'); config['judgement']['content_hash'] = '0'*64
                        with sqlite3.connect(stack['creation_store'].db_path) as db:
                            db.execute('UPDATE investigation_drafts SET configuration_json=? WHERE id=?',(json.dumps(config), negative.id))
                    if label == 'G': context.enter_context(patch('backend.rulesets.compiler.compile_ruleset_content', side_effect=ValueError('injected compile failure')))
                    response = stack['client'].post(f'/api/investigation-drafts/{negative.id}/confirm-and-queue',
                        headers={'Idempotency-Key':label}, json={'expected_revision':99 if label=='E' else 1,'confirmed':True})
                    assert response.status_code >= 400 and len(jobs.list()) == before
                    with sqlite3.connect(stack['creation_store'].db_path) as db:
                        assert db.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == runs_before
                    evidence['cases'].append({'case':label, 'pass':True,'status_code':response.status_code,
                        'fault_injection':True,'new_runs':0,'new_jobs':0}); save()
            if 'H' in args.cases:
                arguments = _t1_temporary_arguments(stack); arguments['configuration']['platform'] = args.platform
                formal = app.create_draft(CreateDraftCommand.model_validate(arguments), principal=principal)
                confirm_execute('H', formal)
            assert formal_before == {name: table(name) for name in formal_before}
            assert not table('report_versions')
            evidence['pass'] = all(case['pass'] for case in evidence['cases']) and {case['case'] for case in evidence['cases']} == set(args.cases)
            assert evidence['pass'], 'One or more requested real acceptance cases failed'
        except Exception as exc:
            evidence['failure_type'] = type(exc).__name__
            # Do not export exception text from provider/crawler or authentication paths.
            raise
        finally:
            evidence['no_formal_mutation'] = formal_before == {name: table(name) for name in formal_before}
            evidence['no_report_created'] = not table('report_versions')
            evidence['counts'] = {'jobs':len(jobs.list()),'reports':len(table('report_versions'))}
            with sqlite3.connect(stack['creation_store'].db_path) as db:
                evidence['counts']['runs'] = db.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0]
            save()
            try: next(fixture)
            except StopIteration: pass


if __name__ == '__main__':
    main()
