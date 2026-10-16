"""Creator link normalization happens at editable input boundaries only."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks, HTTPException

from backend.audit_agent.creator_url import CreatorUrlValidationError
from backend.investigation_creation.approval import UseRuleSetProposalInput
from backend.investigation_creation.contracts import UpdateDraftCommand
from backend.investigation_creation.errors import ConfigurationValidationError
from test_investigation_draft_configuration_m3 import (
    m3_stack, _create_draft, _principal, _judgement, _ruleset, _temporary_configuration, _counts,
)
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_approval import approve, PRINCIPAL
from test_ruleset_proposal_presentation import content, run, evidence

SHORT = "https://v.douyin.com/profileShare/"
FULL = "https://www.douyin.com/user/MS4wLjABAAAA-valid"
OTHER = "https://www.douyin.com/user/MS4wLjABAAAA-other"


def creator_configuration(stack, value=SHORT):
    return {"platform": "dy", "investigation": {"mode": "creator", "creator_url": value},
            "judgement": _judgement(_ruleset(stack))}


def resolver_before_fence(monkeypatch, service, result=FULL):
    original = service.resource_service.authoritative_draft_fence
    in_fence = False

    @contextmanager
    def fence():
        nonlocal in_fence
        in_fence = True
        try:
            with original() as connection:
                yield connection
        finally:
            in_fence = False

    def resolve(platform, value, **kwargs):
        assert not in_fence, "Network resolution must precede resource and SQLite write locks"
        assert platform == "dy"
        if value == SHORT:
            return result
        return value

    mocked = Mock(side_effect=resolve)
    monkeypatch.setattr(service.resource_service, "authoritative_draft_fence", fence)
    monkeypatch.setattr("backend.investigation_creation.service.resolve_creator_url", mocked)
    return mocked


def test_create_update_and_preview_only_persist_resolved_url(m3_stack, monkeypatch):
    service = m3_stack["service"]
    resolver = resolver_before_fence(monkeypatch, service)
    configuration = creator_configuration(m3_stack)
    draft = _create_draft(m3_stack, configuration)
    assert draft.configuration.investigation.creator_url == FULL
    assert configuration["investigation"]["creator_url"] == SHORT
    assert service.get_confirmation_preview(draft.id, principal=_principal(m3_stack)).creator_url == FULL
    assert resolver.call_count == 1
    resolver.side_effect = lambda platform, value: OTHER
    updated = service.update_draft(UpdateDraftCommand(draft_id=draft.id,
        expected_revision=draft.current_revision, configuration=configuration), principal=_principal(m3_stack))
    assert updated.configuration.investigation.creator_url == OTHER
    assert service.get_confirmation_preview(draft.id, principal=_principal(m3_stack)).creator_url == OTHER
    assert resolver.call_count == 2


def test_create_failure_does_not_write_draft_or_enter_fence(m3_stack, monkeypatch):
    monkeypatch.setattr("backend.investigation_creation.service.resolve_creator_url",
                        Mock(side_effect=CreatorUrlValidationError("分享链接未指向主页")))
    fence = Mock(side_effect=AssertionError("must not acquire resource fence"))
    monkeypatch.setattr(m3_stack["resources"], "authoritative_draft_fence", fence)
    with pytest.raises(ConfigurationValidationError) as caught:
        _create_draft(m3_stack, creator_configuration(m3_stack))
    assert caught.value.code == "INVALID_CREATOR_URL"
    assert _counts(m3_stack["store"]) == (0, 0)
    fence.assert_not_called()


def test_update_failure_preserves_revision_and_existing_url(m3_stack, monkeypatch):
    draft = _create_draft(m3_stack, creator_configuration(m3_stack, FULL))
    monkeypatch.setattr("backend.investigation_creation.service.resolve_creator_url",
                        Mock(side_effect=CreatorUrlValidationError("分享链接已失效")))
    with pytest.raises(ConfigurationValidationError) as caught:
        m3_stack["service"].update_draft(UpdateDraftCommand(draft_id=draft.id,
            expected_revision=draft.current_revision, configuration=creator_configuration(m3_stack)),
            principal=_principal(m3_stack))
    assert caught.value.code == "INVALID_CREATOR_URL"
    saved = m3_stack["service"].get_draft(draft.id, principal=_principal(m3_stack))
    assert saved.current_revision == draft.current_revision
    assert saved.configuration.investigation.creator_url == FULL


def test_search_does_not_resolve_and_other_platform_behavior_stays(m3_stack, monkeypatch):
    resolver = Mock(side_effect=AssertionError("search must not resolve profiles"))
    monkeypatch.setattr("backend.investigation_creation.service.resolve_creator_url", resolver)
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["招聘收费"]))
    m3_stack["service"].update_draft(UpdateDraftCommand(draft_id=draft.id,
        expected_revision=draft.current_revision, title="updated"), principal=_principal(m3_stack))
    resolver.assert_not_called()
    bad = creator_configuration(m3_stack, FULL)
    bad["platform"] = "xhs"
    with pytest.raises(ConfigurationValidationError) as caught:
        _create_draft(m3_stack, bad)
    assert caught.value.code == "PLATFORM_MISMATCH"
    resolver.assert_not_called()


def test_full_profile_is_preserved(m3_stack):
    value = FULL + "?from_tab_name=main&custom=value"
    draft = _create_draft(m3_stack, creator_configuration(m3_stack, value))
    assert draft.configuration.investigation.creator_url == value


def test_real_resolver_api_preview_freeze_and_crawler_command(m3_stack, monkeypatch, tmp_path):
    """Only the upstream HTTP hop is fake; all application contracts are real."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.investigation_creation import create_investigation_creation_router
    from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand

    upstream = Mock(return_value='https://www.iesdouyin.com/share/user/123456?sec_uid=MS4wLjABAAAA-valid')
    monkeypatch.setattr('backend.audit_agent.creator_url._read_share_redirect', upstream)
    app = FastAPI()
    app.include_router(create_investigation_creation_router(m3_stack['service'],
        principal_provider=m3_stack['principals']))
    with TestClient(app) as client:
        response = client.post('/api/investigation-drafts', json={
            'title': '主页调查', 'objective': '调查公开主页作品',
            'configuration': creator_configuration(m3_stack,
                f'4- 长按复制此条消息，打开抖音搜索，查看TA的更多作品。 {SHORT} 1@4.com :3pm'),
        })
        assert response.status_code == 201, response.text
        draft = response.json()
        assert draft['configuration']['investigation']['creator_url'] == FULL
        preview = client.get(f"/api/investigation-drafts/{draft['id']}/confirmation-preview")
        assert preview.status_code == 200
        assert preview.json()['creator_url'] == FULL
        assert upstream.call_count == 1
        # Confirmation and command assembly must work even after the short link
        # becomes unavailable. No upstream re-resolution is allowed here.
        upstream.side_effect = AssertionError('frozen task must never resolve links')
        run = m3_stack['service'].confirm_and_queue(ConfirmAndQueueCommand(
            draft_id=draft['id'], expected_revision=1, confirmed=True,
            idempotency_key='resolved-profile-freeze',
        ), principal=_principal(m3_stack))
        execution = run.confirmed_configuration['execution']
        assert run.confirmed_configuration['creator_url'] == FULL
        assert execution['creator_url'] == execution['creator_id'] == FULL
        assert execution['crawl_mode'] == 'creator'
        adapter = MediaCrawlerAdapter(tmp_path)
        monkeypatch.setattr(adapter, '_build_runner', lambda: ['python', 'main.py'])
        command = Mock(return_value=CrawlOutput(platform='dy', contents=[], comments=[],
                                               output_dir=tmp_path, command=[]))
        monkeypatch.setattr(adapter, '_run_command', command)
        adapter.run_creator(platform='dy', creator_id=execution['creator_id'],
            max_notes=execution['max_notes'], max_comments=execution['max_comments'],
            max_concurrency=execution['max_concurrency'],
            max_items_per_minute=execution['max_items_per_minute'],
            get_sub_comment=execution['get_sub_comment'], save_root=tmp_path)
        arguments = command.call_args.kwargs['command']
        assert arguments[arguments.index('--creator_id') + 1] == FULL
        assert upstream.call_count == 1


def test_real_resolver_api_failure_does_not_save_a_draft(m3_stack, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.investigation_creation import create_investigation_creation_router

    upstream = Mock(return_value='https://www.douyin.com/video/123?sec_uid=MS4wLjABAAAA-valid')
    monkeypatch.setattr('backend.audit_agent.creator_url._read_share_redirect', upstream)
    app = FastAPI()
    app.include_router(create_investigation_creation_router(m3_stack['service'],
        principal_provider=m3_stack['principals']))
    with TestClient(app) as client:
        response = client.post('/api/investigation-drafts', json={
            'title': '主页调查', 'objective': '调查公开主页作品',
            'configuration': creator_configuration(m3_stack),
        })
        assert response.status_code == 400, response.text
        assert response.json()['detail']['code'] == 'INVALID_CREATOR_URL'
        assert _counts(m3_stack['store']) == (0, 0)


def test_proposal_adoption_create_resolves_before_fence(creation_stack, content, monkeypatch):
    stack = creation_stack
    first = run(stack, content)
    resolver = resolver_before_fence(monkeypatch, stack["app_service"])
    creation = {"title": "作者调查", "objective": "调查公开内容", "configuration": {
        "platform": "dy", "investigation": {"mode": "creator", "creator_url": SHORT},
    }}
    result, _, _ = approve(stack, first, arguments={"create_draft": creation})
    assert result["status"] == "ok", result
    assert result["data"]["draft"]["configuration"]["investigation"]["creator_url"] == FULL
    assert result["data"]["confirmation_preview"]["creator_url"] == FULL
    resolver.assert_called_once_with("dy", SHORT)


def test_proposal_adoption_resolution_failure_does_not_create_draft(creation_stack, content, monkeypatch):
    from test_ruleset_proposals import rows
    first = run(creation_stack, content)
    monkeypatch.setattr("backend.investigation_creation.service.resolve_creator_url",
                        Mock(side_effect=CreatorUrlValidationError("视频链接不是作者主页")))
    creation = {"title": "作者调查", "objective": "调查公开内容", "configuration": {
        "platform": "dy", "investigation": {"mode": "creator", "creator_url": SHORT},
    }}
    result, _, _ = approve(creation_stack, first, arguments={"create_draft": creation})
    assert result["status"] == "error", result
    assert not rows(creation_stack["creation_store"], "investigation_drafts")
    assert not rows(creation_stack["creation_store"], "ruleset_proposal_approvals")


def test_proposal_adoption_patch_resolves_before_fence(creation_stack, content, monkeypatch):
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    assert result["status"] == "ok", result
    draft = result["data"]["draft"]
    # Exercise the existing-draft adoption patch using the still-current approval turn.
    configuration = deepcopy(draft["configuration"])
    configuration["platform"] = "dy"
    configuration["investigation"] = {"mode": "creator", "creator_url": SHORT}
    resolver = resolver_before_fence(monkeypatch, stack["app_service"])
    updated = stack["app_service"].use_ruleset_proposal(UseRuleSetProposalInput(
        presentation_id=evidence(first)["presentation_id"], draft_id=draft["id"], expected_revision=1),
        session_id=first["session_id"], turn_id=turn.id, tool_call_id="profile-patch",
        principal=PRINCIPAL, draft_patch={"title": "作者调查", "objective": "公开内容", "configuration": configuration})
    assert updated.configuration.investigation.creator_url == FULL
    resolver.assert_called_once_with("dy", SHORT)


@pytest.mark.parametrize("context_url,expected", [(SHORT, FULL), (FULL, FULL), (OTHER, None)])
def test_legacy_job_relation_context_matches_resolved_identity(m3_stack, monkeypatch, context_url, expected):
    from backend import main
    from backend.audit_agent.job_store import JobStore
    from backend.investigation_creation.principal import Principal
    jobs = JobStore(m3_stack["crawler_accounts"].db_path)
    monkeypatch.setattr(main, "crawler_account_store", m3_stack["crawler_accounts"])
    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "AuditPipeline", Mock())
    monkeypatch.setattr(main, "enrich_job", lambda job: job)
    monkeypatch.setattr(main, "create_revision_from_payload", lambda *args, **kwargs: {"version": 1})
    monkeypatch.setattr(main, "relation_context_parent_result", lambda context: {"id": 1})
    monkeypatch.setattr(main, "_require_result", lambda *args, **kwargs: {"id": 1})
    monkeypatch.setattr(main, "resolve_creator_url_contract", lambda platform, value, **kwargs: FULL)
    # Capture the final context just after identity validation; this avoids unrelated relation storage.
    validated = []
    original = main.ensure_relation_context_matches_creator
    def capture(context, creator_url):
        validated.append(original(context, creator_url))
        raise HTTPException(status_code=418, detail="identity captured before job write")
    monkeypatch.setattr(main, "ensure_relation_context_matches_creator", capture)
    with pytest.raises(HTTPException) as caught:
        main.create_job(main.CrawlRequest(platform="dy", crawl_mode="creator", creator_url=SHORT,
            relation_context={"source": "comment_user_analysis", "parent_audit_result_id": 1,
                              "suspect_profile_url": context_url}), BackgroundTasks(), principal=Principal("admin", role="admin"))
    if expected:
        assert caught.value.status_code == 418
        assert validated[0]["suspect_profile_url"] == expected
    else:
        assert caught.value.status_code == 400
        assert not validated
