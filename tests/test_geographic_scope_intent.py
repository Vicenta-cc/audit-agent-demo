"""Programmatic contract/provenance checks, not an AI understanding evaluation."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.investigation.store import InvestigationStore
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.errors import ConfigurationValidationError
from backend.investigation_creation import scope_intent as geo


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    # Explicit tiny fixture: production catalog coverage is verified separately.
    # Deliberately bypass the production national-package gate ONLY in these
    # business tests; this ten-record fixture cannot run in production.
    monkeypatch.setattr(geo.geographic_catalog, 'validate_packaged_catalog', lambda _: None)
    records = [
        {'code': '65', 'name': '新疆维吾尔自治区', 'aliases': ['新疆'], 'level': 'province', 'kind': '自治区', 'province_code': '65'},
        {'code': '6502', 'name': '克拉玛依市', 'aliases': ['克拉玛依'], 'level': 'prefecture', 'kind': '地级市', 'province_code': '65'},
        {'code': '11', 'name': '北京市', 'aliases': ['北京'], 'level': 'province', 'kind': '直辖市', 'province_code': '11'},
        {'code': '110105', 'name': '朝阳区', 'aliases': ['朝阳', '北京市朝阳区'], 'level': 'county', 'kind': '市辖区', 'province_code': '11'},
        {'code': '21', 'name': '辽宁省', 'aliases': ['辽宁'], 'level': 'province', 'kind': '省', 'province_code': '21'},
        {'code': '2113', 'name': '朝阳市', 'aliases': ['朝阳'], 'level': 'prefecture', 'kind': '地级市', 'province_code': '21'},
        {'code': '22', 'name': '吉林省', 'aliases': ['吉林'], 'level': 'province', 'kind': '省', 'province_code': '22'},
        {'code': '2202', 'name': '吉林市', 'aliases': ['吉林'], 'level': 'prefecture', 'kind': '地级市', 'province_code': '22'},
        {'code': '659001', 'name': '石河子市', 'aliases': ['石河子'], 'level': 'county', 'kind': '县级市', 'province_code': '65'},
        {'code': '6540', 'name': '伊犁哈萨克自治州', 'aliases': ['伊犁'], 'level': 'prefecture', 'kind': '自治州', 'province_code': '65'},
    ]
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'schema_version': 'geographic-catalog-v1', 'version': 'test-fixture', 'sources': ['test'], 'records': records}))
    monkeypatch.setattr(geo, 'CATALOG_PATH', path)
    return records


def change(**kw):
    return geo.RelevanceScopeChange(action='set', target_text='克拉玛依', source_quote='调查克拉玛依', selection_policy='local_first_with_supplement', **kw)


@pytest.mark.parametrize('body', [
    {'action': 'keep', 'source_quote': 'a'},
    {'action': 'set', 'source_quote': 'a'},
    {'action': 'set', 'source_quote': ' ', 'target_text': 'a', 'selection_policy': 'local_only'},
    {'action': 'clear', 'source_quote': 'a', 'target_text': None},
    {'action': 'clear', 'source_quote': 'a', 'selection_policy': 'local_only'},
    {'action': 'clear', 'source_quote': 'a', 'principal': 'forged'},
])
def test_strict_change_contract(body):
    with pytest.raises(ValidationError):
        geo.RelevanceScopeChange.model_validate(body)


def test_resolution_exact_names_and_real_types(catalog):
    for record in catalog:
        result = geo.resolve_region(record['name'])
        assert result['status'] == ('resolved' if record['level'] == 'province' or record['kind'] == '地级市' else 'pending')
    city = geo.resolve_region('克拉玛依')
    assert city['target'].name == '克拉玛依市'
    assert city['province'].name == '新疆维吾尔自治区'
    municipality = geo.resolve_region('北京')
    assert municipality['target'] == municipality['province']
    assert municipality['target_level'] == 'province'
    assert geo.resolve_region('朝阳')['target'].name == '朝阳市'
    ambiguous = geo.resolve_region('吉林')
    assert ambiguous['status'] == 'pending'
    assert {candidate.name for candidate in ambiguous['candidates']} == {'吉林省', '吉林市'}
    assert geo.resolve_region('吉林省')['target'].name == '吉林省'
    assert geo.resolve_region('吉林市')['target'].name == '吉林市'
    assert geo.resolve_region('新疆')['target'].name == '新疆维吾尔自治区'
    for name in ['朝阳区', '北京市朝阳区', '石河子', '石河子市', '伊犁', '克拉玛', '克拉玛依和北京', '未知地域', '朝阳街道']:
        assert geo.resolve_region(name)['status'] == 'pending'


@pytest.mark.parametrize('text', [
    '新疆克拉玛依', '新疆克拉玛依市', '新疆维吾尔自治区克拉玛依',
    '新疆维吾尔自治区克拉玛依市', ' 新疆克拉玛依市 ',
])
def test_compound_province_city_uses_catalog_parent_and_aliases(catalog, text):
    result = geo.resolve_region(text)
    assert result['status'] == 'resolved'
    assert result['target_code'] == '6502'
    assert result['province_code'] == '65'
    assert result['target'].name == '克拉玛依市'


@pytest.mark.parametrize('text', [
    '辽宁克拉玛依市', '新疆朝阳市', '新疆克拉玛', '新疆克拉玛依市附近',
    '新疆石河子市', '新疆伊犁', '北京朝阳区', '新疆克拉玛依和北京',
    '新疆克拉玛依市、吉林市', '新疆克拉玛依市朝阳区', '调查新疆克拉玛依市',
])
def test_compound_never_guesses_drops_remainder_or_broadens_unsupported(catalog, text):
    result = geo.resolve_region(text)
    assert result['status'] == 'pending'
    assert 'target_code' not in result


def test_province_qualification_disambiguates_without_changing_bare_name(catalog):
    assert geo.resolve_region('吉林')['status'] == 'pending'
    for text in ('吉林吉林', '吉林省吉林', '吉林吉林市', '吉林省吉林市'):
        result = geo.resolve_region(text)
        assert result['status'] == 'resolved'
        assert result['target_code'] == '2202'
    assert geo.resolve_region('新疆吉林市')['status'] == 'pending'


def test_compound_still_requires_unique_supported_identity(catalog):
    catalog.append({'code': '6501', 'name': '测试市', 'aliases': ['克拉玛依'],
        'level': 'prefecture', 'kind': '地级市', 'province_code': '65'})
    geo.CATALOG_PATH.write_text(json.dumps({'version': 'test-fixture', 'records': catalog}))
    result = geo.resolve_region('新疆克拉玛依')
    assert result['status'] == 'pending'
    assert {candidate.name for candidate in result['candidates']} == {'克拉玛依市', '测试市'}
    assert geo.resolve_region('新疆克拉玛依市')['target_code'] == '6502'


def test_missing_catalog_explicit_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(geo, 'CATALOG_PATH', tmp_path / 'missing')
    with pytest.raises(ConfigurationValidationError, match='名录') as error:
        geo.resolve_region('北京')
    assert error.value.code == 'REGION_CATALOG_UNAVAILABLE'


def test_incomplete_small_catalog_rejected_by_production_validator(tmp_path, monkeypatch):
    # No catalog fixture / validator patch: a nominally valid small name table
    # must never silently become production national support.
    path = tmp_path / 'small-catalog.json'
    path.write_text(json.dumps({'schema_version': 'geographic-catalog-v1', 'version': 'small',
        'sources': [{'url': 'https://example.gov.cn/list'}], 'records': [
            {'code': '110000', 'name': '北京市', 'aliases': ['北京'], 'level': 'province',
             'kind': 'municipality', 'province_code': '110000'}]}), encoding='utf-8')
    monkeypatch.setattr(geo, 'CATALOG_PATH', path)
    with pytest.raises(ConfigurationValidationError) as error:
        geo.resolve_region('北京')
    assert error.value.code == 'REGION_CATALOG_UNAVAILABLE'


@pytest.fixture
def app(tmp_path):
    return SimpleNamespace(conversation_store=InvestigationStore(tmp_path / 'conversation.db'),
        store=InvestigationCreationStore(tmp_path / 'creation.db'))


def turn(app, session, text):
    with app.conversation_store._connect() as db:
        db.execute("UPDATE investigation_turns SET status='completed' WHERE session_id=?", (session,))
    return app.conversation_store.create_turn(session, client_message_id=uuid4().hex, user_input=text)[0]


def prepare(app, session, current, command=None, **kw):
    return geo.prepare_change(command or change(), application=app, principal=Principal('owner'),
        session_id=session, application_turn_id=current.id, **kw)


def test_real_source_current_priority_earlier_preparation_and_safe_projection(app, catalog):
    session = app.conversation_store.create_creation_session(principal='owner').id
    first = turn(app, session, '调查克拉玛依')
    current = turn(app, session, '采用展示的词库')
    scope, state = prepare(app, session, current)
    assert scope.target.name == '克拉玛依市'
    assert state.source_turn_id == first.id
    assert state.application_turn_id == current.id
    public = geo.project_state(state).model_dump(mode='json')
    for private in ['principal', 'session_id', 'source_quote', 'source_user_message_id', 'application_turn_id']:
        assert private not in public
    duplicate = turn(app, session, '调查克拉玛依')
    assert prepare(app, session, duplicate)[1].source_turn_id == duplicate.id
    later = turn(app, session, '采用词库')
    with pytest.raises(ConfigurationValidationError) as error:
        prepare(app, session, later)
    assert error.value.code == 'REGION_SOURCE_AMBIGUOUS'


def test_source_rejects_runtime_other_owner_session_future_assistant(app, catalog):
    session = app.conversation_store.create_creation_session(principal='owner').id
    current = turn(app, session, '采用词库')
    for app_turn, owner, sid in [('runtime-turn', 'owner', session), (current.id, 'other', session),
                                (current.id, 'owner', 'other-session')]:
        with pytest.raises(ConfigurationValidationError):
            geo.prepare_change(change(), application=app, principal=Principal(owner), session_id=sid, application_turn_id=app_turn)
    with app.conversation_store._connect() as db:
        db.execute('''INSERT INTO investigation_messages
            (id,session_id,turn_id,role,content,sequence,created_at) VALUES (?,?,?,?,?,?,?)''',
            ('assistant', session, current.id, 'assistant', '调查克拉玛依', 2, '2099'))
        db.execute('''INSERT INTO investigation_messages
            (id,session_id,turn_id,role,content,sequence,created_at) VALUES (?,?,?,?,?,?,?)''',
            ('future', session, 'future-turn', 'user', '调查克拉玛依', 3, '2099'))
    with pytest.raises(ConfigurationValidationError):
        prepare(app, session, current)


def test_pending_clear_and_frozen_types(app, catalog):
    session = app.conversation_store.create_creation_session(principal='owner').id
    current = turn(app, session, '查朝阳区')
    scope, state = prepare(app, session, current, geo.RelevanceScopeChange(action='set',
        target_text='朝阳区', source_quote='查朝阳区', selection_policy='local_only'))
    assert scope is None and state.status == 'pending' and state.target_text == '朝阳区'
    assert state.selection_policy == 'local_only'
    with pytest.raises(ValidationError):
        state.status = 'resolved'
    current = turn(app, session, '不限制地域')
    scope, state = prepare(app, session, current,
        geo.RelevanceScopeChange(action='clear', source_quote='不限制地域'))
    assert scope is None and state.status == 'cleared' and state.target is None


def test_confirmed_round_is_a_real_source_boundary(app, catalog):
    session = app.conversation_store.create_creation_session(principal='owner').id
    old = turn(app, session, '调查克拉玛依')
    with app.conversation_store._connect() as db:
        timestamp = db.execute('SELECT created_at FROM investigation_messages WHERE id=?', (old.user_message_id,)).fetchone()[0]
    with app.store._connect() as db:
        db.execute('''INSERT INTO investigation_drafts
            (id,owner_principal,status,current_revision,title,objective,configuration_json,
             created_by,updated_by,confirmed_by,confirmed_at,created_at,updated_at)
            VALUES ('old','owner','QUEUED',1,'old','old','{}','owner','owner','owner',?,?,?)''',
            (timestamp, timestamp, timestamp))
        db.execute('''INSERT INTO draft_creation_operations
            (id,session_id,principal,scope,state,draft_id)
            VALUES ('old-op',?,'owner','application:old','CREATED','old')''', (session,))
    current = turn(app, session, '新一轮采用资源')
    with pytest.raises(ConfigurationValidationError) as error:
        prepare(app, session, current)
    assert error.value.code == 'REGION_SOURCE_INVALID'
    # New task preparation remains usable in a later adoption turn.
    preparation = turn(app, session, '调查克拉玛依')
    current = turn(app, session, '采用资源')
    assert prepare(app, session, current)[1].source_turn_id == preparation.id
    with pytest.raises(ConfigurationValidationError):
        prepare(app, session, current, draft_id='not-member')


def test_blockers_binding_and_copy_lineage(app, catalog):
    from pydantic import BaseModel

    class Configuration(BaseModel):
        platform: str = 'dy'
        investigation: object = SimpleNamespace(mode='search')
        relevance_scope: geo.RelevanceScope | None = None
        relevance_scope_state: geo.RelevanceScopeState | None = None

    session = app.conversation_store.create_creation_session(principal='owner').id
    current = turn(app, session, '调查克拉玛依')
    scope, state = prepare(app, session, current)
    config = Configuration(relevance_scope=scope, relevance_scope_state=state)
    bound = geo.bind_draft_state(config, 'first', 'owner')
    assert bound.relevance_scope_state.draft_id == 'first'
    assert config.relevance_scope_state.draft_id == ''
    assert geo.scope_blockers(bound)[0]['code'] == 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE'
    with pytest.raises(ConfigurationValidationError):
        geo.ensure_execution_ready(bound)
    with pytest.raises(ConfigurationValidationError):
        geo.bind_draft_state(bound, 'second', 'owner')
    with pytest.raises(ConfigurationValidationError):
        geo.bind_draft_state(bound, 'first', 'other')
    copied = geo.bind_draft_state(bound, 'second', 'owner', copied_from_draft_id='first')
    assert copied.relevance_scope_state.copied_from_draft_id == 'first'
    assert copied.relevance_scope_state.draft_id == 'second'
    assert copied.relevance_scope_state.source_user_message_id == state.source_user_message_id
    assert len(geo.scope_blockers(bound.model_copy(update={'platform': 'xhs'}))) == 2
    pending = state.model_copy(update={'status': 'pending'})
    assert geo.scope_blockers(bound.model_copy(update={'relevance_scope_state': pending}))[0]['code'] == 'RELEVANCE_SCOPE_PENDING'
    assert geo.scope_blockers(Configuration()) == []
    geo.ensure_execution_ready(Configuration())


def test_legacy_configuration_without_geography_fields_remains_usable():
    legacy = SimpleNamespace(platform='xhs', investigation=SimpleNamespace(mode='search'))
    assert geo.scope_blockers(legacy) == []
    assert geo.bind_draft_state(legacy, 'legacy', 'owner') is legacy
    geo.ensure_execution_ready(legacy)
