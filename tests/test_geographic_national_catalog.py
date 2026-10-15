"""Production catalog and real draft service checks; no geographic/model fixtures."""
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from backend.investigation_creation import scope_intent as geo
from backend.investigation_creation.contracts import ConfirmAndQueueCommand
from backend.investigation_creation.errors import ConfigurationValidationError
from backend.investigation_creation.geographic_catalog import PROVINCE_CODES
from backend.investigation_creation.store import InvestigationCreationStore
from test_geographic_draft_configuration import change, flow, no_effects, stored
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P, prepare


def test_production_catalog_supported_names_and_cross_level_aliases():
    # Intentionally no catalog fixture or validator patch: use the packaged file.
    catalog, indexed = geo._catalog()
    supported = [record for record in indexed.values() if record['level'] == 'province'
        or (record['level'] == 'prefecture' and record['kind'] in {'地级市', 'prefecture_city'})]
    assert {r['code'] for r in supported if r['level'] == 'province'} == PROVINCE_CODES
    assert Counter(r['level'] for r in supported) == {'province': 34, 'prefecture': 293}
    assert len(supported) == 327
    names = defaultdict(set)
    for record in supported:
        for name in [record['name'], *record['aliases']]:
            names[name].add(record['code'])
    for record in supported:
        result = geo.resolve_region(record['name'])
        assert result['status'] == 'resolved', (record, result)
        assert result['target_code'] == record['code']
        assert result['province_code'] == record['province_code']
        assert result['catalog_version'] == catalog['version']
    # Only supported province/city identities participate in ambiguity; an
    # unsupported county alias cannot shadow an otherwise unique city alias.
    for name, codes in names.items():
        result = geo.resolve_region(name)
        assert result['status'] == ('resolved' if len(codes) == 1 else 'pending'), name
        if len(codes) == 1:
            assert result['target_code'] == next(iter(codes)), name
        else:
            assert {(c.name, c.level) for c in result['candidates']} == {
                (indexed[code]['name'], indexed[code]['level']) for code in codes}, name


@pytest.mark.parametrize('text,name,province', [
    ('克拉玛依市', '克拉玛依市', '新疆维吾尔自治区'),
    ('克拉玛依', '克拉玛依市', '新疆维吾尔自治区'),
    ('朝阳', '朝阳市', '辽宁省'),
    ('吉林省', '吉林省', '吉林省'), ('吉林市', '吉林市', '吉林省'),
    ('广东', '广东省', '广东省'), ('新疆', '新疆维吾尔自治区', '新疆维吾尔自治区'),
    ('台湾', '台湾省', '台湾省'), ('香港', '香港特别行政区', '香港特别行政区'),
    ('澳门', '澳门特别行政区', '澳门特别行政区'),
    ('大庆', '大庆市', '黑龙江省'),
    ('深圳', '深圳市', '广东省'), ('宁波', '宁波市', '浙江省'),
    ('北京', '北京市', '北京市'), ('天津', '天津市', '天津市'),
    ('上海', '上海市', '上海市'), ('重庆', '重庆市', '重庆市'),
])
def test_real_catalog_examples(text, name, province):
    result = geo.resolve_region(text)
    assert result['status'] == 'resolved', result
    assert result['target'].name == name
    assert result['province'].name == province


@pytest.mark.parametrize('text', [
    '吉林', '朝阳区', '北京市朝阳区', '朝阳县',
    '石河子市', '济源市', '五指山市', '阿拉善盟', '阿坝藏族羌族自治州', '阿里地区',
    '朝阳街道', '克拉玛', '克拉玛依和北京', '北京、上海', '不存在的测试地域',
])
def test_real_catalog_unsupported_or_ambiguous_never_broadens(text):
    assert geo.resolve_region(text)['status'] == 'pending'


def test_real_catalog_service_persistence_projection_and_confirmation(creation_stack, content, tmp_path):
    stack = creation_stack
    catalog, _ = geo._catalog()
    first, _, _, _, _, args = prepare(stack, content)
    args['scope_decision'] = 'apply'
    args['relevance_scope_change'] = change('克拉玛依', '调查克拉玛依')
    entries = []
    draft_id = ''
    cases = [
        ('resolved', args, '调查克拉玛依'),
        ('pending', {'relevance_scope_change': change('朝阳区', '改查朝阳区')}, '改查朝阳区'),
        ('cleared', {'relevance_scope_change': {'action': 'clear', 'source_quote': '不限制地域'}}, '不限制地域'),
    ]
    for status, args, message in cases:
        revision = stored(stack, draft_id).current_revision if draft_id else 0
        if draft_id:
            args = {'operation': 'update', 'draft_id': draft_id, 'expected_revision': revision, **args}
        outcome, completed = flow(stack, first['session_id'], args, message)
        draft_id = outcome['data']['draft']['id']
        actual = stored(stack, draft_id)
        assert actual.configuration.relevance_scope_state.status == status
        if status != 'cleared':
            assert actual.configuration.relevance_scope_state.catalog_version == catalog['version']
        restarted = InvestigationCreationStore(stack['creation_store'].db_path)
        assert restarted.get_draft(draft_id, principal=P.id).configuration == actual.configuration
        responses = {
            'public_draft': stack['client'].get('/api/investigation-drafts/' + draft_id),
            'confirmation_preview': stack['client'].get('/api/investigation-drafts/' + draft_id + '/confirmation-preview'),
            'workspace': stack['client'].get('/api/investigation-workspaces/' + first['session_id'] + '/state'),
            'sse': stack['client'].get('/api/investigation-workspace-turns/' + completed['turn'].id + '/events'),
        }
        for response in responses.values():
            assert response.status_code == 200, response.text
        entry = {'operation': status, 'input': args, 'before_revision': revision,
            'after_revision': actual.current_revision,
            **{key: response.json() for key, response in responses.items() if key != 'sse'},
            'sse': [json.loads(line[6:]) for line in responses['sse'].text.splitlines() if line.startswith('data: ')]}
        state = entry['public_draft']['configuration']['relevance_scope_state']
        assert not {'principal', 'session_id', 'source_quote', 'source_user_message_id', 'catalog_version'} & state.keys()
        if status == 'resolved':
            assert entry['confirmation_preview']['relevance_scope']['target']['name'] == '克拉玛依市'
            assert actual.configuration.relevance_scope_state.target_code == '650200'
        if status != 'cleared':
            expected = 'RELEVANCE_SCOPE_PENDING' if status == 'pending' else 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE'
            assert not entry['confirmation_preview']['can_confirm']
            with pytest.raises(ConfigurationValidationError) as error:
                stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft_id,
                    expected_revision=actual.current_revision, confirmed=True, idempotency_key=uuid4().hex), principal=P)
            assert error.value.code == expected
            with pytest.raises(ConfigurationValidationError) as error:
                stack['creation_store'].confirm_and_queue(draft_id, principal=P.id,
                    expected_revision=actual.current_revision, confirmed=True, idempotency_key=uuid4().hex,
                    request_fingerprint='national-programmatic-test', resolved_configuration={})
            assert error.value.code == expected
            response = stack['client'].post('/api/investigation-drafts/' + draft_id + '/confirm-and-queue',
                json={'expected_revision': actual.current_revision, 'confirmed': True}, headers={'Idempotency-Key': uuid4().hex})
            assert response.status_code == 400
            assert response.json()['detail']['code'] == expected
        else:
            assert not any(b['code'].startswith('RELEVANCE_SCOPE') for b in entry['confirmation_preview']['blockers'])
        entries.append(entry)
        no_effects(stack)
    policy_examples = []
    for text, expected, canonical in [
        ('朝阳', 'resolved', '朝阳市'), ('吉林', 'pending', None),
        ('吉林省', 'resolved', '吉林省'), ('吉林市', 'resolved', '吉林市'),
        ('克拉玛依', 'resolved', '克拉玛依市'), ('北京', 'resolved', '北京市'),
        ('广东', 'resolved', '广东省'), ('新疆', 'resolved', '新疆维吾尔自治区'),
        ('朝阳区', 'pending', None), ('石河子市', 'pending', None),
        ('阿坝藏族羌族自治州', 'pending', None), ('朝阳街道', 'pending', None),
        ('克拉玛', 'pending', None), ('北京、上海', 'pending', None),
    ]:
        result = geo.resolve_region(text)
        assert result['status'] == expected
        target = result['target'].name if result.get('target') else None
        assert target == canonical
        policy_examples.append({'input': text, 'status': result['status'], 'target': target,
            'target_level': result.get('target_level')})
    evidence = {'validation': 'programmatic real tool/service/SQLite/API chain; scripted model substitute',
        'catalog': 'production national catalog; no catalog or validator monkeypatch',
        'catalog_version': catalog['version'], 'catalog_sha256': catalog['validation']['records_sha256'],
        'supported_province_targets': 34, 'supported_prefecture_cities': 293,
        'supported_municipalities': 4, 'supported_region_targets': 327,
        'city_target_policy': 'exact name/alias among 34 province-level regions and 293 prefecture cities',
        'model_requests': 0, 'collection_requests': 0, 'production_database_writes': 0,
        'city_policy_examples': policy_examples, 'steps': entries}
    def redact(value):
        if isinstance(value, dict):
            return {key: redact(child) for key, child in value.items()
                if key not in {'source_user_message_id', 'source_sequence', 'task_anchor_turn_id', 'principal'}}
        if isinstance(value, list):
            return [redact(child) for child in value]
        return 'synthetic-fixture-owner' if value == P.id else value
    path = Path(os.environ.get('GEOGRAPHIC_NATIONAL_FLOW_EVIDENCE', str(tmp_path / 'service-flow-national.json')))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(evidence), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
