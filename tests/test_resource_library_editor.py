"""Real editor writes/deletes, stale clients, restart and execution snapshots."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_resource_lifecycle import service, lexicon, P
from backend.investigation_creation.principal import LocalPrincipalProvider, Principal
from backend.resource_management.api import create_resource_router
from backend.resource_management.contracts import ResourceError
from backend.rulesets.compiler import compile_ruleset_content
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.store import RuleSetStore
from backend.rulesets.errors import RuleSetNotFoundError
from backend.audit_agent.lexicon_store import LexiconStore, LexiconCategoryReferenceConflictError


def rules():
    return json.loads((Path(__file__).parent / 'fixtures/recruitment_fraud_ruleset.json').read_text())


def put(service, kind, body, version=0, operation='create', identifier='editor-resource'):
    return service.save_library(kind, identifier, body, version, operation, principal=P)


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(create_resource_router(SimpleNamespace(resource_management=service), None, LocalPrincipalProvider(P.id)))
    return TestClient(app)


def test_rules_editor_roundtrip_execution_delete_restart(service, client):
    body = rules()
    response = client.put('/api/resource-library/ruleset/editor-resource', json={'content': body, 'expected_version': 0, 'operation_id': 'first'})
    assert response.status_code == 200, response.text
    saved = response.json()
    before = service.rulesets.get_published(saved['published_revision_id'], principal=P)
    body = deepcopy(saved['content'])
    body['name'] = 'Updated editor rule'
    body['categories'][0]['rules'][0]['hit_condition'] = 'EDITOR_HIT_SENTINEL'
    body['general_exemptions'][0]['condition'] = 'DISABLED_EXEMPTION_SENTINEL'
    body['general_exemptions'][0]['enabled'] = False
    changed = put(service, 'ruleset', body, saved['version'], 'update')
    assert changed['published_version'] == 2
    assert put(service, 'ruleset', body, saved['version'], 'update') == changed
    current = service.read('ruleset', 'editor-resource', principal=P)
    compiled = compile_ruleset_content(current['content'])
    assert 'EDITOR_HIT_SENTINEL' in compiled.prompt_profile_snapshot['fusion_prompt_template']
    assert 'DISABLED_EXEMPTION_SENTINEL' not in json.dumps(compiled.prompt_profile_snapshot)
    assert current['content']['general_exemptions'][0]['enabled'] is False
    with pytest.raises(ResourceError, match='变化'):
        put(service, 'ruleset', body, saved['version'], 'stale')
    with pytest.raises(ResourceError, match='变化'):
        service.delete_library('ruleset', 'editor-resource', saved['version'], principal=P)
    deletion = client.request('DELETE', '/api/resource-library/ruleset/editor-resource', json={'expected_version': changed['version']})
    assert deletion.status_code == 200, deletion.text
    assert client.get('/api/resource-library/ruleset/editor-resource').status_code == 404
    restarted = RuleSetStore(service.lexicons.db_path)
    assert not any(r['id'] == 'editor-resource' for r in restarted.list(actor_id=P.id))
    assert not any(r['ruleset_id'] == 'editor-resource' for r in restarted.list_current_published())
    assert restarted.get_published(before['id']) == before
    with pytest.raises(RuleSetNotFoundError):
        restarted.update_draft('editor-resource', expected_revision=changed['version']+1, content=RuleSetContent.model_validate(rules()), actor_id=P.id)


def test_lexicon_editor_preserves_disabled_entries_description_variants_and_tombstone(service, client):
    body = lexicon() | {'description': '词库说明已保存'}
    saved = put(service, 'lexicon', body)
    body = deepcopy(saved['content'])
    body['title'] = '真实词库'
    body['entries'][0]['term'] = '新主词'
    changed = put(service, 'lexicon', body, saved['version'], 'update')
    assert service.lexicons.enabled_main_terms('editor-resource') == ['新主词']
    assert service.lexicons.get_category('editor-resource')['description'] == '词库说明已保存'
    assert service.read('lexicon', 'editor-resource', principal=P)['content'] == changed['content']
    with pytest.raises(ResourceError):
        put(service, 'lexicon', body, saved['version'], 'stale')
    with pytest.raises(ResourceError):
        service.delete_library('lexicon', 'editor-resource', saved['version'], principal=P)
    assert client.request('DELETE', '/api/resource-library/lexicon/editor-resource', json={'expected_version': changed['version']}).status_code == 200
    restarted = LexiconStore(service.lexicons.db_path)
    assert not any(r['id'] == 'editor-resource' for r in restarted.list_categories())
    with restarted._connect() as conn:
        latest = conn.execute('SELECT content_json FROM lexicon_content_versions WHERE category_id=? ORDER BY version DESC', ('editor-resource',)).fetchone()[0]
        assert json.loads(latest) == {'deleted': True}
        assert conn.execute('SELECT COUNT(*) FROM lexicon_keywords WHERE category_id=?', ('editor-resource',)).fetchone()[0] == 0
    with pytest.raises(ResourceError):
        put(service, 'lexicon', body, 0, 'resurrect')


def test_validation_and_owner_failures_do_not_change_published_rules(service, client):
    saved = put(service, 'ruleset', rules())
    body = deepcopy(saved['content'])
    body['categories'][0]['rules'][0]['application_stages'] = []
    failed = client.put('/api/resource-library/ruleset/editor-resource', json={'content': body, 'expected_version': saved['version'], 'operation_id': 'invalid'})
    assert failed.status_code == 422
    assert service.read('ruleset', 'editor-resource', principal=P)['version'] == saved['version']
    with pytest.raises(ResourceError) as error:
        service.delete_library('ruleset', 'editor-resource', saved['version'], principal=Principal('other'))
    assert error.value.code == 'RESOURCE_FORBIDDEN'
    service.delete_library('ruleset', 'editor-resource', saved['version'], principal=P)
    with pytest.raises(ResourceError):
        put(service, 'ruleset', rules(), saved['version'], 'resurrect')


def test_delete_reference_conflicts_are_clear_and_non_destructive(service, client, monkeypatch):
    saved = put(service, 'ruleset', rules())
    with service.lexicons._connect() as conn:
        conn.execute('CREATE TABLE audit_policies (id TEXT, name TEXT, config_json TEXT, published_config_json TEXT)')
        conn.execute('INSERT INTO audit_policies VALUES (?,?,?,?)', ('policy', '引用方案', '{}', json.dumps({'ruleset_revision_id': saved['published_revision_id']})))
    blocked = client.request('DELETE', '/api/resource-library/ruleset/editor-resource', json={'expected_version': saved['version']})
    assert blocked.status_code == 409
    assert '引用方案' in blocked.json()['detail']['message']
    assert service.read('ruleset', 'editor-resource', principal=P)['version'] == saved['version']
    lex = put(service, 'lexicon', lexicon(), identifier='editor-lexicon', operation='create-lexicon')
    def conflict(*args, **kwargs):
        raise LexiconCategoryReferenceConflictError('editor-lexicon', [{'policy_name': '词库引用方案'}])
    monkeypatch.setattr(service.lexicons, 'delete_category_atomically', conflict)
    blocked = client.request('DELETE', '/api/resource-library/lexicon/editor-lexicon', json={'expected_version': lex['version']})
    assert blocked.status_code == 409
    assert '词库引用方案' in blocked.json()['detail']['message']


def test_pinned_k2_import_and_edit_guard_preserve_execution(service, tmp_path):
    from scripts.import_formal_k2 import import_k2
    from backend.rulesets.service import RuleSetService
    from backend.rulesets.trial_profiles import K2_RULESET_ID, TRIAL_BUNDLES
    from backend.rulesets.errors import RuleSetValidationError
    from backend.rulesets.compiler import content_hash
    source_path = tmp_path / 'source.sqlite3'
    source = RuleSetService(RuleSetStore(source_path))
    body = RuleSetContent.model_validate_json((TRIAL_BUNDLES[K2_RULESET_ID] / 'ruleset.json').read_text())
    aggregate = source.create_draft(body, principal=P, ruleset_id=K2_RULESET_ID)
    source.publish(K2_RULESET_ID, expected_revision=aggregate['draft_revision'], idempotency_key='seed', principal=P)
    result = import_k2(source_path, service.lexicons.db_path)
    assert result['rule_count'] == 13
    assert result['content_hash'] == '25ec65414796448e67f95f7de5ecbea75ae06ffb571e0750ce786d78e0e85269'
    assert not import_k2(source_path, service.lexicons.db_path)['imported']
    resource = service.read('ruleset', K2_RULESET_ID, principal=P)
    assert resource['editable'] is False
    body = deepcopy(resource['content'])
    body['categories'][0]['rules'][0]['hit_condition'] = 'Changed rule must not keep the pinned K2 identity'
    with pytest.raises(RuleSetValidationError):
        put(service, 'ruleset', body, resource['version'], 'change', K2_RULESET_ID)
    with pytest.raises(RuleSetValidationError):
        service.rulesets.update_draft(K2_RULESET_ID, expected_revision=resource['version'], content=RuleSetContent.model_validate(body), principal=P)
    assert service.read('ruleset', K2_RULESET_ID, principal=P)['content_hash'] == result['content_hash']
    copied = put(service, 'ruleset', body, operation='copy', identifier='k2-copy')
    assert copied['editable']
    service.delete_library('ruleset', K2_RULESET_ID, resource['version'], principal=P)
    with pytest.raises(ValueError, match='refusing'):
        import_k2(source_path, service.lexicons.db_path)
