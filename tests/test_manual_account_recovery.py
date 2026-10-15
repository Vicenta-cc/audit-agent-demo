"""Explicit human recovery cannot replay automatic checks or clear another incident."""
import time

import pytest

from test_account_safety_fencing import safety, expired_incident
from backend.audit_agent.config import settings
from backend.audit_agent import crawler_account_store as store_module
from backend.task_admission.resources import account_lease


@pytest.fixture(autouse=True)
def isolated_profile_locks(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    monkeypatch.setattr(settings,'task_resource_lock_dir',tmp_path/'locks')


def manual_required(store,a):
    store.save_auth_state(a['id'],'fixture-old-auth',platform_account_id='123456789')
    expired_incident(store,a)
    claim=store.claim_recovery(a['id'])
    assert claim
    store.finish_recovery(a['id'],claim,'challenge')
    assert store.claim_recovery(a['id']) is None


def begin(store,a,operation='manual-operation',owner='owner'):
    with account_lease('dy',a['id']) as held:
        assert held
        return store.begin_manual_recovery(a['id'],operation,owner_id=owner)


def test_automatic_challenge_then_explicit_trusted_manual_auth_transaction(safety):
    store,a,b=safety
    manual_required(store,a)
    original=store.get(a['id'])
    claim=begin(store,a)
    assert claim and begin(store,a) is None
    assert store.admit_route(a['id'],'203.0.113.10',1,'manual-browser',recovery_claim=claim,login_bootstrap=True)
    store.release_route_admission('manual-browser')
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        store.save_douyin_auth_state(a['id'],'fixture-new-auth','123456789',connection=db,expected_revision=original['auth_state_revision'])
        store.mark_direct_validated(a['id'],1,connection=db)
        assert store.finish_manual_recovery(a['id'],'manual-operation',claim,'success',owner_id='owner',verified_identity='123456789',connection=db)
        # A separate reader sees the old claim until trusted auth+release commit.
        assert store.get(a['id'])['recovery_state']=='manual_claimed'
    after=store.get(a['id'])
    assert after['route_state']=='active' and after['recovery_state']=='manual_success'
    assert after['risk_count']==original['risk_count']==1
    assert store.claim_recovery(a['id']) is None
    assert store.admit_route(b['id'],'203.0.113.10',1,'after-manual-success')


@pytest.mark.parametrize('result,identity',[('success','wrong-identity'),('challenge',''),('failed',''),('cancelled',''),('uncertain','')])
def test_manual_wrong_identity_failure_and_cancel_stay_paused_without_auto_retry(safety,result,identity):
    store,a,b=safety
    manual_required(store,a)
    claim=begin(store,a)
    assert not store.finish_manual_recovery(a['id'],'manual-operation',claim,result,owner_id='owner',verified_identity=identity)
    after=store.get(a['id'])
    assert after['route_state']=='manual_required' and after['risk_count']==1
    assert after['platform_account_id']=='123456789'
    assert store.claim_recovery(a['id']) is None
    assert begin(store,a) is None
    with pytest.raises(ValueError): store.admit_route(b['id'],'203.0.113.10',1,'after-failed-manual')


def test_manual_claim_operation_and_owner_fence(safety):
    store,a,_=safety
    manual_required(store,a)
    claim=begin(store,a)
    assert not store.finish_manual_recovery(a['id'],'manual-operation',claim,'success',owner_id='foreign',verified_identity='123456789')
    assert store.get(a['id'])['recovery_state']=='manual_claimed'
    with pytest.raises(ValueError,match='OPERATION_CONFLICT'):
        begin(store,a,owner='foreign')
    assert store.get(a['id'])['risk_count']==1


def test_human_login_before_cooldown_does_not_erase_frozen_deadline(safety,monkeypatch):
    store,a,b=safety
    store.save_auth_state(a['id'],'fixture-old-auth',platform_account_id='123456789')
    store.record_risk(a['id'])
    deadline=time.time()+60
    store.pause_account_exit(a['id'],until=deadline)
    claim=begin(store,a)
    # Explicit user login is allowed; generic collection remains blocked.
    assert store.admit_route(a['id'],'203.0.113.10',1,'human-during-cooldown',recovery_claim=claim,login_bootstrap=True)
    store.release_route_admission('human-during-cooldown')
    with pytest.raises(ValueError): store.admit_route(b['id'],'203.0.113.10',1,'normal-during-cooldown')
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        store.save_douyin_auth_state(a['id'],'fixture-new-auth','123456789',connection=db)
        assert store.finish_manual_recovery(a['id'],'manual-operation',claim,'success',owner_id='owner',verified_identity='123456789',connection=db)
    assert store.get(a['id'])['recovery_state']=='manual_verified_waiting'
    assert not store.release_verified_manual(a['id'])
    monkeypatch.setattr(store_module.time,'time',lambda:deadline+1)
    assert store.release_verified_manual(a['id'])
    assert store.get(a['id'])['route_state']=='active' and store.get(a['id'])['risk_count']==1
    assert store.claim_recovery(a['id']) is None
    assert store.admit_route(b['id'],'203.0.113.10',1,'after-frozen-cooldown')


def test_manual_success_keeps_other_unresolved_account_exit_pause(safety):
    store,a,b=safety
    manual_required(store,a)
    store.save_auth_state(b['id'],'fixture-other-auth',platform_account_id='987654321')
    expired_incident(store,b)
    claim=begin(store,a)
    assert store.finish_manual_recovery(a['id'],'manual-operation',claim,'success',owner_id='owner',verified_identity='123456789')
    assert store.get(a['id'])['route_state']=='active'
    with pytest.raises(ValueError): store.admit_route(a['id'],'203.0.113.10',1,'other-incident-still-paused')
    assert store.get(b['id'])['route_state']=='challenge_paused'


def test_in_check_challenge_keeps_claim_and_never_reopens_auto_loop(safety):
    store,a,_=safety
    store.save_auth_state(a['id'],'fixture-auth',platform_account_id='123456789')
    expired_incident(store,a)
    claim=store.claim_recovery(a['id'])
    incident=store.get(a['id'])['recovery_incident']
    store.pause_account_exit(a['id'],until=time.time()+60)
    assert store.get(a['id'])['recovery_claim']==claim
    assert store.get(a['id'])['recovery_incident']==incident
    store.finish_recovery(a['id'],claim,'challenge')
    assert store.get(a['id'])['route_state']=='manual_required'
    assert store.claim_recovery(a['id']) is None


def test_explicit_stopped_coordinator_settles_orphan_claim_without_unpausing(safety):
    store,a,b=safety
    manual_required(store,a)
    claim=begin(store,a)
    store.admit_route(a['id'],'203.0.113.10',1,'orphan-login',recovery_claim=claim,login_bootstrap=True)
    assert store.begin_manual_recovery(a['id'],'new-login',owner_id='owner') is None
    # The runtime coordinator separately proves both account/Profile FD fences.
    with account_lease('dy',a['id']) as stopped:
        assert stopped
        assert store.release_admissions_for_stopped_account(a['id'])==1
    assert store.get(a['id'])['route_state']=='manual_required'
    assert store.get(a['id'])['recovery_state']=='uncertain'
    assert store.claim_recovery(a['id']) is None
    assert begin(store,a,'new-login')
    with pytest.raises(ValueError): store.admit_route(b['id'],'203.0.113.10',1,'orphan-no-unpause')


def test_manual_claim_cannot_migrate_to_another_actual_exit(safety):
    store,a,_=safety
    manual_required(store,a)
    claim=begin(store,a)
    other=store.observe_shared_exit('203.0.113.11',service_exit=True)
    with pytest.raises(ValueError):
        store.admit_route(a['id'],other['shared_exit_key'],other['shared_exit_generation'],'manual-switched-exit',recovery_claim=claim,login_bootstrap=True)
    assert store.get(a['id'])['shared_exit_key']=='203.0.113.10'
    assert store.get(a['id'])['recovery_state']=='manual_claimed'
