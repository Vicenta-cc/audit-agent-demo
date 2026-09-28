"""A confirmed workspace must survive changes to its source resources."""
import sqlite3

import pytest

from backend.investigation_creation.contracts import InvestigationTaskParameters
from backend.investigation_creation.frozen_preview import confirmed_run_preview
from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import (
    creation_stack,
    _create_completed_turn,
    _publish_fake_run,
)


def change_resources(stack, artifact, change):
    resources = stack['app_service'].resource_service
    configuration = artifact['draft']['configuration']
    lexicon_id = configuration['investigation']['recall_plan']['lexicon_id']
    revision_id = configuration['judgement']['ruleset_revision_id']
    # Model later edits/deletions of the backing resources. Fixtures include old
    # policies, so remove source rows directly rather than exercising policy CRUD.
    with resources.lexicon_store._connect() as conn:
        if change == 'lexicon_deleted':
            conn.execute('DELETE FROM lexicon_keywords WHERE category_id=?', (lexicon_id,))
            conn.execute('DELETE FROM lexicon_categories WHERE id=?', (lexicon_id,))
        elif change == 'lexicon_changed':
            conn.execute("UPDATE lexicon_keywords SET keyword='后来新增词'||id WHERE category_id=?", (lexicon_id,))
        elif change == 'ruleset_deleted':
            conn.execute("UPDATE rule_sets SET status='deleted',published_revision_id='' WHERE published_revision_id=?", (revision_id,))
        elif change == 'ruleset_republished':
            conn.execute("UPDATE rule_sets SET published_revision_id='later-revision',published_version=99 WHERE published_revision_id=?", (revision_id,))
        else:
            raise AssertionError(change)


@pytest.mark.parametrize('status', ['QUEUED', 'RUNNING', 'FAILED', 'PUBLISHED'])
@pytest.mark.parametrize('change', ['lexicon_deleted', 'lexicon_changed', 'ruleset_deleted', 'ruleset_republished'])
def test_confirmed_workspace_keeps_frozen_configuration(creation_stack, monkeypatch, status, change):
    stack = creation_stack
    result = _create_completed_turn(stack)
    artifact = result['terminal']['artifact']
    principal = stack['principals']()
    if status == 'PUBLISHED':
        projection = _publish_fake_run(stack, result, idempotency_key='frozen-card')
    else:
        response = stack['client'].post(
            f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
            headers={'Idempotency-Key': 'frozen-card'},
            json={'expected_revision': artifact['draft_revision'], 'confirmed': True},
        )
        assert response.status_code == 202
        projection = response.json()
        with sqlite3.connect(stack['creation_store'].db_path) as conn:
            conn.execute('UPDATE investigation_runs SET status=? WHERE id=?', (status, projection['run_id']))
    run = stack['creation_store'].get_run(projection['run_id'], principal=principal.id)
    frozen = run.confirmed_configuration
    change_resources(stack, artifact, change)
    resources = stack['app_service'].resource_service
    resources.task_settings.save(InvestigationTaskParameters(max_notes=9, max_total_notes=9, max_comments=17), 0)

    def unexpected_live_read(*args, **kwargs):
        raise AssertionError('confirmed history must not resolve current resources or settings')

    monkeypatch.setattr(resources, 'confirmation_preview', unexpected_live_read)
    monkeypatch.setattr(resources, 'query_options', unexpected_live_read)
    monkeypatch.setattr(resources.task_settings, 'get', unexpected_live_read)
    url = f"/api/investigation-workspaces/{result['workspace_id']}/state"
    for _ in range(2):
        response = stack['client'].get(url)
        assert response.status_code == 200, response.text
        state = response.json()
        preview = state['draft_artifact']['confirmation_preview']
        assert preview['blockers'] == []
        assert preview['can_confirm'] is False
        assert preview['resolved_search_terms'] == frozen['resolved_search_terms']
        assert preview['ruleset_revision'] == frozen['ruleset_revision']
        assert state['draft_artifact']['suggestion']['ruleset_revision'] == frozen['ruleset_revision']
        assert preview['max_posts_per_keyword'] == frozen['execution']['max_notes']
        assert preview['max_comments_per_post'] == frozen['execution']['max_comments']
        assert preview['estimated_max_contents'] == frozen['execution']['max_total_notes']
        assert state['run']['run_id'] == run.id
        if status == 'PUBLISHED':
            assert state['run']['report_version_id'] == projection['report_version_id']
    assert stack['creation_store'].get_run(run.id, principal=principal.id).confirmed_configuration == frozen
    # The direct Draft view uses the same frozen projection, without weakening ownership.
    response = stack['client'].get(f"/api/investigation-drafts/{artifact['draft_id']}/confirmation-preview")
    assert response.status_code == 200
    assert response.json()['ruleset_revision'] == frozen['ruleset_revision']
    stack['principals'].current = Principal('another-account')
    assert stack['client'].get(url).status_code in (403, 404)
    assert stack['client'].get(f"/api/investigation-drafts/{artifact['draft_id']}/confirmation-preview").status_code in (403, 404)


@pytest.mark.parametrize('change', ['lexicon_deleted', 'lexicon_changed', 'ruleset_deleted'])
def test_unconfirmed_workspace_still_checks_live_resources(creation_stack, change):
    stack = creation_stack
    result = _create_completed_turn(stack)
    artifact = result['terminal']['artifact']
    change_resources(stack, artifact, change)
    response = stack['client'].get(f"/api/investigation-workspaces/{result['workspace_id']}/state")
    assert response.status_code == 200
    preview = response.json()['draft_artifact']['confirmation_preview']
    assert preview['blockers']
    assert preview['can_confirm'] is False
    response = stack['client'].post(
        f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
        headers={'Idempotency-Key': 'must-not-start'},
        json={'expected_revision': artifact['draft_revision'], 'confirmed': True},
    )
    assert response.status_code in (400, 409, 422)
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0


def test_confirmed_creator_workspace_keeps_url(creation_stack, monkeypatch):
    stack = creation_stack
    url = 'https://www.douyin.com/user/MS4wLjABAAAA-valid'
    result = _create_completed_turn(stack, content=f'调查这个博主主页 {url}')
    artifact = result['terminal']['artifact']
    response = stack['client'].post(
        f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
        headers={'Idempotency-Key': 'creator-frozen'},
        json={'expected_revision': artifact['draft_revision'], 'confirmed': True},
    )
    assert response.status_code == 202
    monkeypatch.setattr(stack['app_service'].resource_service, 'confirmation_preview',
                        lambda *a, **kw: pytest.fail('must not revalidate creator resources'))
    state = stack['client'].get(f"/api/investigation-workspaces/{result['workspace_id']}/state").json()
    preview = state['draft_artifact']['confirmation_preview']
    assert preview['mode'] == 'creator'
    assert preview['creator_url'] == url
    assert preview['resolved_search_terms'] == []
    assert preview['recall_plan']['strategy'] == 'none'
    assert preview['ruleset_revision'] is not None
    assert preview['blockers'] == []
    assert not preview['can_confirm']


def test_temporary_rules_survive_proposal_change(creation_stack, monkeypatch):
    from copy import deepcopy
    from pathlib import Path
    import json
    from test_temporary_ruleset_execution import bound, command
    from test_ruleset_proposal_approval import PRINCIPAL
    stack = creation_stack
    content = json.loads((Path(__file__).parent / 'fixtures/recruitment_fraud_ruleset.json').read_text())
    draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    frozen = deepcopy(run.confirmed_configuration)
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        # Mutable proposal storage is no longer a dependency of this confirmed run.
        conn.execute('DELETE FROM ruleset_proposals')
    monkeypatch.setattr(stack['app_service'].resource_service, 'confirmation_preview',
                        lambda *a, **kw: pytest.fail('must not revalidate temporary rules'))
    preview = stack['app_service'].get_confirmation_preview(draft.id, principal=PRINCIPAL)
    assert preview.temporary_ruleset.model_dump(mode='json') == frozen['temporary_ruleset']
    assert preview.ruleset_revision is None
    assert not preview.blockers
    assert not preview.can_confirm
    assert stack['creation_store'].get_run(run.id, principal=PRINCIPAL.id).confirmed_configuration == frozen
    with pytest.raises(ValueError, match='does not match'):
        confirmed_run_preview(run.model_copy(update={'draft_revision': run.draft_revision + 1}))
