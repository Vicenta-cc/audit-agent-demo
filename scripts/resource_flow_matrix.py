"""Independent natural-language scenarios; called only by the opt-in Qwen runner.

Resources and accounts are synthetic. Actual Run confirmation and Job compilation
use production application services, but no worker or crawler is started.
"""
import json


def run_matrix(stack, manager, principal, start_session, turn, evidence, persist, selected=()):
    from test_temporary_ruleset_execution import adapter
    from backend.investigation_creation.contracts import UpdateDraftCommand
    from backend.investigation_creation.errors import DraftAlreadyConfirmedError
    app = stack['app_service']
    execution = adapter(stack)
    evidence['scenarios'] = []
    evidence['execution_boundary'] = 'real confirm_and_queue + ensure_job; no worker/crawler'
    seed_session = start_session('seed')
    seed_ctx = dict(session_id=seed_session.id, principal=principal)
    rule = {
        'schema_version': 0, 'name': '招聘押金诈骗验收规则', 'domain': 'recruitment_fraud',
        'audit_goal': '识别以招聘为名要求求职者先缴押金的诈骗推广，保护正常招聘讨论。',
        'general_exemptions': [{'exemption_id': 'critical', 'name': '非赞同引用',
                                'condition': '仅揭露或反对诈骗，不支持或招揽该行为。'}],
        'categories': [{'category_id': 'fraud', 'name': '招聘诈骗', 'description': '招聘前置收费', 'order': 1,
                        'rules': [{'rule_id': 'deposit', 'name': '入职押金招揽',
                                   'hit_condition': '以招聘入职为名要求求职者预先缴纳押金，并提供收费或联系引导。',
                                   'suggested_risk_level': 'high', 'rule_exemptions': [],
                                   'application_stages': ['image_evidence', 'video_frame_evidence', 'comment_audit', 'fusion_audit'],
                                   'adjudication_notes': '正常招聘不命中。', 'enabled': True, 'order': 1}]}],
    }
    proposal = app.create_ruleset_proposal(rule, session_id=seed_session.id)
    saved_rule = manager.save(proposal.proposal_id, 1, 'new', 'matrix-seed-rule', **seed_ctx)
    lexicon = {'title': '招聘押金诈骗验收词库', 'risk_label': '招聘诈骗', 'entries': [
        {'id': 'matrix-main', 'kind': 'main', 'term': '招聘押金'},
        {'id': 'matrix-variant', 'kind': 'variant', 'parent_id': 'matrix-main', 'term': '入职保证金'},
        {'id': 'matrix-disabled', 'kind': 'main', 'term': '招聘服装费', 'enabled': False},
        {'id': 'matrix-tag', 'kind': 'tag', 'term': '求职避坑'},
    ]}
    lex = manager.create_lexicon(lexicon, **seed_ctx)
    saved_lex = manager.save(lex['edit_id'], 1, 'new', 'matrix-seed-lex', **seed_ctx)
    rule_id, lex_id = saved_rule['resource_id'], saved_lex['resource_id']

    def read(kind, identifier):
        return manager.read(kind, identifier, principal=principal)

    def no_draft(record):
        assert record['counts'] == record['before_counts'], 'Unexpected Draft or Run'

    def draft(record, rule_strategy, lex_strategy):
        value = record['artifact']['draft']
        assert record['counts']['investigation_drafts'] == record['before_counts']['investigation_drafts'] + 1
        assert value['configuration']['judgement']['strategy'] == rule_strategy
        assert value['configuration']['investigation']['recall_plan']['strategy'] == lex_strategy
        assert record['artifact']['confirmation_preview']['can_confirm']
        return value

    def start(label, value, expected_terms=None):
        result = turn(label, '确认按刚才展示的最新草案开始执行。', run_delta=1)
        assert 'confirm_and_queue_investigation' in result['tools']
        run_id = result['artifact']['run_id']
        run = stack['creation_store'].get_run(run_id, principal=principal.id)
        job_id = execution.ensure_job(run)
        job = execution.job_store.get(job_id)
        assert job is not None
        assert execution.ensure_job(run) == job_id, 'Duplicate job on replay'
        snap = run.confirmed_configuration
        if expected_terms is not None:
            # The authoritative execution projection, not prose in the answer.
            actual = snap['resolved_search_terms']
            assert actual == expected_terms, (actual, expected_terms)
        try:
            app.update_draft(UpdateDraftCommand(draft_id=value['id'], expected_revision=value['current_revision'], title='不应覆盖已确认草案'), principal=principal)
        except DraftAlreadyConfirmedError:
            pass
            pass
        else:
            raise AssertionError('Confirmed Draft remained editable')
        result['execution_check'] = {'run_id': run_id, 'job_id': job_id, 'snapshot': snap,
                                     'job_compiled': True, 'crawler_started': False}
        persist()
        return result

    def read_existing():
        start_session('read-existing')
        before_rule, before_lex = read('ruleset', rule_id), read('lexicon', lex_id)
        a = turn('read-rule', '请读取后台“招聘押金诈骗验收规则”的完整规则条件和豁免，只查看，不修改、不创建任务。')
        b = turn('read-lexicon', '再读取后台“招聘押金诈骗验收词库”，告诉我主词、变体、标签和启用状态，以及实际会搜索哪些词。仍然只查看。')
        for r in (a, b):
            no_draft(r)
            assert not r['edits']
            assert 'read_resource' in r['tools']
            assert not set(r['tools']) & {'save_resource', 'create_ruleset_proposal', 'create_lexicon_edit'}
        assert read('ruleset', rule_id) == before_rule
        assert read('lexicon', lex_id) == before_lex
        assert before_lex['search_terms'] == ['招聘押金']

    def modify_existing():
        session = start_session('modify-existing')
        before_rule, before_lex = read('ruleset', rule_id), read('lexicon', lex_id)
        r = turn('edit-without-save', '修改后台“招聘押金诈骗验收词库”：把主词“招聘押金”改成“求职押金”，保留变体、标签和其他词的启用状态。再修改“招聘押金诈骗验收规则”的“入职押金招揽”：命中条件中的收费类型扩展到押金或培训费，其他条件不变。先展示修改，不保存、不创建任务。')
        no_draft(r)
        assert len(r['edits']) == 2 and all(not e['saved'] for e in r['edits'])
        assert read('ruleset', rule_id) == before_rule and read('lexicon', lex_id) == before_lex
        r = turn('save-existing-edits', '这两份修改都保存回各自原资源，保留原来的名称，不另存，不创建任务。')
        no_draft(r)
        assert all(e['saved'] for e in r['edits'])
        after_rule, after_lex = read('ruleset', rule_id), read('lexicon', lex_id)
        assert after_rule['version'] == before_rule['version'] + 1
        assert after_lex['version'] == before_lex['version'] + 1
        assert '培训费' in after_rule['content']['categories'][0]['rules'][0]['hit_condition']
        assert after_lex['search_terms'] == ['求职押金']
        entries = {e['id']: e for e in after_lex['content']['entries']}
        assert entries['matrix-variant']['parent_id'] == 'matrix-main'
        assert entries['matrix-variant']['term'] == '入职保证金'
        assert entries['matrix-disabled']['enabled'] is False and entries['matrix-tag']['kind'] == 'tag'
        r = turn('reopen-saved', '重新从后台读取刚才保存的规则和词库，核对是否真的保存成功。不要创建任务。')
        no_draft(r)
        assert 'read_resource' in r['tools'] or 'open_resource_edit' in r['tools']

    def existing_start():
        start_session('existing-start')
        r = turn('existing-draft', '用后台已有的“招聘押金诈骗验收规则”和“招聘押金诈骗验收词库”准备小红书关键词抓取审核任务，只搜索启用主词。先展示草案，不要开始。')
        d = draft(r, 'existing_ruleset', 'existing_lexicon')
        start('existing-start', d, read('lexicon', lex_id)['search_terms'])

    def new_saved_start():
        start_session('new-saved')
        r = turn('new-create-and-save', '请新建一套“民族交流实验规则”（4—6条，关注维汉关系讨论中的具体攻击，保护正常文化表达）和“民族交流实验词库”（2个主词，每词1个变体），两份都保存到后台。只完成创建与保存，不要创建或启动任务。')
        no_draft(r)
        assert {e['kind'] for e in r['edits']} == {'ruleset', 'lexicon'}
        assert all(e['saved'] for e in r['edits'])
        terms = next(e for e in r['edits'] if e['kind'] == 'lexicon')['search_terms']
        r = turn('new-saved-draft', '现在用刚保存的这套规则和这份词库，准备小红书抓取审核任务，采用正式资源，不搜索变体。先展示草案。')
        d = draft(r, 'existing_ruleset', 'existing_lexicon')
        start('new-saved-start', d, terms)

    def missing_consent():
        start_session('missing-consent')
        r = turn('missing-ask', '我想调查小红书上二手天文望远镜虚标光学参数、冒充原厂配件的销售误导。请先找合适的已有审核规则和词库；没有合适的就先问我，不要擅自生成或启动。')
        no_draft(r)
        assert not r['edits']
        assert 'query_investigation_options' in r['tools'] or 'read_resource' in r['tools']
        assert any(s in r['answer'] for s in ('是否', '需要', '可以', '要不要', '请问', '？', '?')), 'No consultation language; inspect answer'
        r = turn('missing-decline', '暂时不要生成，也不要拿其他领域的规则凑用，先停在这里。')
        no_draft(r)
        assert not r['edits']
        r = turn('missing-consent-generate', '现在同意创建：生成适合这个天文望远镜调查的3条审核规则和一份2主词各1变体的词库，先展示，全部只临时使用，不保存，不启动。')
        no_draft(r)
        assert len(r['edits']) == 2 and all(not e['saved'] for e in r['edits'])
        terms = next(e for e in r['edits'] if e['kind'] == 'lexicon')['search_terms']
        r = turn('missing-adopt', '采用刚才完整展示的规则和词库启用主词，创建这个小红书调查草案。规则与词库都不保存，先展示草案，不要开始。')
        d = draft(r, 'temporary_ruleset', 'temporary_terms')
        start('missing-start', d, terms)

    def missing_auto_consult():
        start_session('missing-auto-consult')
        r = turn('missing-natural-request', '我想调查小红书上二手天文望远镜虚标光学参数、冒充原厂配件的销售误导，帮我发起抓取审核任务。')
        no_draft(r)
        assert not r['edits'], 'Investigation intent was incorrectly treated as generation authorization'
        assert 'query_investigation_options' in r['tools'] or 'read_resource' in r['tools']
        assert any(s in r['answer'] for s in ('是否', '需要', '可以', '要不要', '请问', '？', '?')), 'No consultation language; inspect answer'

    def saved_rule_temporary_lexicon():
        start_session('saved-rule-temporary-lexicon')
        r = turn('mixed-temporary-lexicon-create', '审核沿用后台“招聘押金诈骗验收规则”。请单独生成一份求职前置收费词库，2个主词，每词1个变体，只临时用，不保存。先展示，不创建任务。')
        no_draft(r)
        assert len(r['edits']) == 1 and r['edits'][0]['kind'] == 'lexicon' and not r['edits'][0]['saved']
        terms = r['edits'][0]['search_terms']
        r = turn('mixed-temporary-lexicon-draft', '用刚生成词库的启用主词临时搜索，审核使用后台那套招聘押金诈骗验收规则，创建小红书任务草案，先不要开始。')
        d = draft(r, 'existing_ruleset', 'temporary_terms')
        start('mixed-temporary-lexicon-start', d, terms)

    def temporary_rule_saved_lexicon():
        start_session('temporary-rule-saved-lexicon')
        r = turn('mixed-temporary-rule-create', '词库沿用后台“招聘押金诈骗验收词库”。请单独生成3条识别招聘押金、入职培训费、强制购置工具费诈骗推广的审核规则，只临时使用，不保存，先完整展示，不创建任务。')
        no_draft(r)
        assert len(r['edits']) == 1 and r['edits'][0]['kind'] == 'ruleset' and not r['edits'][0]['saved']
        r = turn('mixed-temporary-rule-draft', '采用刚才展示的临时规则，搜索用后台招聘押金诈骗验收词库，创建小红书草案，先展示，不开始。')
        d = draft(r, 'temporary_ruleset', 'existing_lexicon')
        start('mixed-temporary-rule-start', d, read('lexicon', lex_id)['search_terms'])

    scenarios = [read_existing, modify_existing, existing_start, new_saved_start, missing_consent,
                 saved_rule_temporary_lexicon, temporary_rule_saved_lexicon, missing_auto_consult]
    for scenario in scenarios:
        if selected and scenario.__name__ not in selected:
            continue
        offset = len(evidence['cases'])
        try:
            scenario()
            result = {'scenario': scenario.__name__, 'status': 'PASS'}
        except Exception as exc:
            result = {'scenario': scenario.__name__, 'status': 'FAIL', 'failure_type': type(exc).__name__, 'failure': str(exc)}
        result['cases'] = [c['case'] for c in evidence['cases'][offset:]]
        evidence['scenarios'].append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        persist()
    evidence['status'] = 'PASS' if evidence['scenarios'] and all(s['status'] == 'PASS' for s in evidence['scenarios']) else 'FAIL'
    evidence['crawler_calls'] = 0
    persist()
