"""Read-only trial preparation by default. Never launches browsers or platform I/O."""
from __future__ import annotations

import argparse
import ast
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

CONTRACT_VERSION = 1
RUNTIME_CONTROLS = ('api_total_40', 'media_files_2', 'media_bytes_20mb',
                    'media_elapsed_300s', 'probe_total_96', 'browser_growth_stop',
                    'active_accounts_1', 'rotation_disabled', 'auto_tasks_disabled')


def _absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('ABSOLUTE_PATH_REQUIRED')
    return path.resolve()


def _load(path):
    return json.loads(Path(path).read_text()) if path else None


def _contract_version(path):
    try:
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCHEMA_VERSION' for t in node.targets):
                return ast.literal_eval(node.value)
    except (OSError, SyntaxError, ValueError):
        pass
    return None


def _scheduler(path, now):
    """Do not create or migrate the shared scheduler while checking it."""
    if not path.is_file():
        return 'SCHEDULER_MISSING'
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
            policy = conn.execute("SELECT limits_json FROM request_scheduler_policy WHERE platform='dy'").fetchone()
            row = conn.execute("SELECT cooldown_until FROM request_scheduler_state WHERE platform='dy'").fetchone()
            if not policy:
                return 'GLOBAL_DY_POLICY_MISSING'
            if row and row[0] > now:
                return 'GLOBAL_DY_COOLDOWN_ACTIVE'
    except sqlite3.Error:
        return 'SCHEDULER_INVALID'
    return None


def prepare(*, app_root, crawler_root, trial_root, trial_id, scheduler_db,
            app_python, crawler_python, browser_binary, echo_url='', evidence_path=None,
            resource_path=None, dedicated_test_account=False, write=False, now=None):
    now = time.time() if now is None else now
    paths = {key: _absolute(value) for key, value in dict(app_root=app_root,
        crawler_root=crawler_root, trial_root=trial_root, scheduler_db=scheduler_db,
        app_python=app_python, crawler_python=crawler_python, browser_binary=browser_binary).items()}
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', trial_id):
        raise ValueError('INVALID_TRIAL_ID')
    directory = paths['trial_root'] / trial_id
    blockers = []
    if not dedicated_test_account:
        blockers.append('DEDICATED_TEST_ACCOUNT_AUTHORIZATION_REQUIRED')
    # Existing roots are never adopted: no overwrite, profile copy, DB snapshot or parallel run.
    if directory.exists():
        blockers.append('TRIAL_DIRECTORY_ALREADY_EXISTS')
    if any(directory == p or p in directory.parents or directory in p.parents
           for p in (paths['app_root'], paths['crawler_root'])):
        blockers.append('TRIAL_ROOT_OVERLAPS_SOURCE')
    for key in ('app_python', 'crawler_python', 'browser_binary'):
        if not paths[key].is_file() or not os.access(paths[key], os.X_OK):
            blockers.append(key.upper() + '_UNAVAILABLE')
    version = _contract_version(paths['crawler_root'] / 'tools/collection_route.py')
    bridge = paths['app_root'] / 'backend/audit_agent/crawler_route.py'
    if version != CONTRACT_VERSION or not bridge.is_file() or 'validate_route_envelope' not in bridge.read_text():
        blockers.append('CONTRACT_VERSION_UNVERIFIED')
    parsed = urlsplit(echo_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        blockers.append('FIXED_HTTPS_ECHO_URL_REQUIRED')
    scheduler_error = _scheduler(paths['scheduler_db'], now)
    if scheduler_error:
        blockers.append(scheduler_error)
    evidence = _load(evidence_path)
    controls = {}
    if not isinstance(evidence, dict):
        blockers.append('MANDATORY_OFFLINE_EVIDENCE_REQUIRED')
    else:
        suites = evidence.get('mandatory_suites', [])
        if (not isinstance(suites, list) or not suites or any(not isinstance(s, dict) or s.get('collected', 0) <= 0 or
                s.get('passed') != s.get('collected') or s.get('skipped') != 0 or
                s.get('exit_code') != 0 or not s.get('test_nodes') for s in suites)):
            blockers.append('MANDATORY_SUITE_NOT_PROVEN')
        if evidence.get('contract_version') != CONTRACT_VERSION or evidence.get('browser_started') is not True or evidence.get('tls_ca_loaded') is not True:
            blockers.append('BROWSER_CONTRACT_TLS_NOT_PROVEN')
        for repo in ('app', 'crawler'):
            if evidence.get(repo + '_root') != str(paths[repo + '_root']) or not evidence.get(repo + '_revision'):
                blockers.append(repo.upper() + '_EVIDENCE_IDENTITY_MISSING')
        controls = evidence.get('runtime_controls', {})
        if not isinstance(controls, dict):
            controls = {}
    missing = [c for c in RUNTIME_CONTROLS if not isinstance(controls.get(c), dict) or
               controls[c].get('verified') is not True or not controls[c].get('test_node') or
               not controls[c].get('config_path')]
    if missing:
        blockers.append('RUNTIME_LIMITS_NOT_PROVEN')
    resource = _load(resource_path)
    resource_state = 'waiting_resource'
    if isinstance(resource, dict):
        # Never echo resource input or accept credential material into a manifest.
        allowed = {'proxy_id', 'product', 'sticky_http', 'expires_at', 'owner_authorized', 'credentials_in_ui'}
        if set(resource) - allowed:
            raise ValueError('RESOURCE_METADATA_ONLY')
        if (resource.get('sticky_http') is not True or resource.get('owner_authorized') is not True or
                resource.get('credentials_in_ui') is not True or not resource.get('proxy_id') or
                type(resource.get('expires_at')) not in (float, int) or not math.isfinite(resource['expires_at']) or resource['expires_at'] <= now):
            blockers.append('RESOURCE_NOT_VERIFIED')
        else:
            resource_state = 'pending_live_probe'
    else:
        blockers.append('WAITING_RESOURCE')
    # This tool does not perform lease acquisition, live probes or operational limit activation.
    blockers.append('LIVE_MAINTENANCE_AND_PROBE_GATE_PENDING')
    manifest = dict(schema_version=1, trial_id=trial_id, dry_run=not write,
        offline_ready=not any(b != 'WAITING_RESOURCE' and b != 'LIVE_MAINTENANCE_AND_PROBE_GATE_PENDING' for b in blockers),
        trial_ready=False, status=resource_state, decision='NO-GO', blockers=blockers,
        paths={**{k: str(v) for k, v in paths.items()}, 'trial_directory': str(directory),
               'storage_db': str(directory / 'data/audit_index.sqlite3'),
               'account_operation_db': str(directory / 'data/audit_index.sqlite3'),
               'output': str(directory / 'outputs'), 'profile_root': str(directory / 'profiles')},
        runtime_environment={'CRAWLER_PROXY_ENABLED': 'true', 'CRAWLER_PROXY_ECHO_URL': echo_url,
            'CRAWLER_PROXY_TRIAL_ID': trial_id, 'CRAWLER_PROXY_TRIAL_JOB_IDS': '',
            'MEDIACRAWLER_DIR': str(paths['crawler_root']), 'XHS_AUDIT_DATA_DIR': str(directory / 'data'),
            'XHS_AUDIT_OUTPUTS_DIR': str(directory / 'outputs'), 'CRAWLER_BROWSER_PROFILE_ROOT': str(directory / 'profiles'),
            'REQUEST_SCHEDULER_DB': str(paths['scheduler_db'])},
        limits={'echo_total': 96, 'active_accounts': 1, 'api_actions_total': 40,
            'media_files': 2, 'media_bytes': 20 * 1024 * 1024, 'media_seconds': 300,
            'probe_ttl_seconds': 300, 'page_requests': 300, 'page_bytes': 50 * 1024 * 1024,
            'page_requests_per_60s': 120, 'page_lifetime_seconds': 1200,
            'rotation_enabled': False, 'auto_tasks_enabled': False},
        required_external_constraints=missing, evidence_path=str(_absolute(evidence_path)) if evidence_path else None,
        live_profile_reuse='NOT_IMPLEMENTED: exclusive maintenance and coherent backup required',
        ready_label='requires_live_gate' if not missing else 'requires_runtime_control_evidence_and_live_gate',
        scheduler_mode='shared_existing_global_dy_no_copy_or_reset')
    if write:
        if not dedicated_test_account or 'TRIAL_ROOT_OVERLAPS_SOURCE' in blockers or directory.exists():
            raise ValueError('ISOLATED_AUTHORIZED_DIRECTORY_REQUIRED')
        directory.mkdir(parents=True, mode=0o700)
        directory.chmod(0o700)
        for name in ('data', 'outputs', 'profiles'):
            (directory / name).mkdir(mode=0o700)
        output = directory / 'manifest.json'
        with output.open('x') as stream:
            output.chmod(0o600)
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return manifest


def rollback_precheck(*, account_db, account_ids, proxy_ids, active_pids, process_inventory_complete=False):
    """Read-only bounded scope check; inventory completeness is external evidence.

    Does not acquire maintenance leases or authorize rollback. Empty inventory
    without explicit complete evidence fails closed.
    """
    blockers = []
    if not account_ids or not proxy_ids or not process_inventory_complete:
        blockers.append('ROLLBACK_SCOPE_OR_PROCESS_EVIDENCE_MISSING')
    for pid in active_pids:
        if type(pid) is not int or pid <= 0:
            blockers.append('ROLLBACK_PROCESS_EVIDENCE_INVALID')
            continue
        try:
            os.kill(pid, 0)
            blockers.append('ROLLBACK_ACTIVE_PROCESS')
        except ProcessLookupError:
            pass
        except PermissionError:
            blockers.append('ROLLBACK_ACTIVE_PROCESS')
    try:
        with sqlite3.connect(_absolute(account_db).as_uri() + '?mode=ro', uri=True) as conn:
            for proxy_id in proxy_ids:
                row = conn.execute('SELECT status FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
                if not row or row[0] != 'maintenance':
                    blockers.append('ROLLBACK_RESOURCE_NOT_PAUSED')
                bound = {row[0] for row in conn.execute('SELECT id FROM crawler_accounts WHERE proxy_id=?', (proxy_id,))}
                if not bound.issubset(set(account_ids)):
                    blockers.append('ROLLBACK_BOUND_ACCOUNT_SCOPE_INCOMPLETE')
            for account_id in account_ids:
                row = conn.execute('SELECT proxy_required,proxy_id FROM crawler_accounts WHERE id=?', (account_id,)).fetchone()
                if not row or not row[0] or row[1] not in proxy_ids:
                    blockers.append('ROLLBACK_ACCOUNT_SCOPE_INVALID')
    except sqlite3.Error:
        blockers.append('ROLLBACK_DATABASE_INVALID')
    return {'decision': 'NO-GO' if blockers else 'MAINTENANCE_LEASE_GATE_PENDING',
            'rollback_ready': False, 'blockers': sorted(set(blockers)),
            'scope': {'account_ids': list(account_ids), 'proxy_ids': list(proxy_ids)},
            'profile_action': 'preserve', 'database_action': 'preserve_additive_schema_history'}


def validate_live_gate(*, manifest_path, live_proof_path, now=None):
    """Cross-check recent live evidence against current isolated resource rows.

    This is a workflow opinion, not authorization; it never starts network work
    or replaces actual resource leases, owner permissions or dispatch checks.
    """
    now = time.time() if now is None else now
    manifest = _load(manifest_path)
    proof = _load(live_proof_path)
    blockers = []
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('paths'), dict) or
            any(not isinstance(manifest['paths'].get(key), str) for key in ('account_operation_db', 'scheduler_db'))):
        raise ValueError('LIVE_MANIFEST_INVALID')
    if manifest.get('offline_ready') is not True:
        blockers.append('OFFLINE_GATE_PENDING')
    allowed = {'trial_id', 'account_id', 'proxy_id', 'binding_revision', 'config_revision',
               'received_at', 'maintenance_released', 'runtime_controls_active', 'browser', 'http'}
    if not isinstance(proof, dict) or set(proof) - allowed:
        raise ValueError('LIVE_PROOF_METADATA_ONLY')
    if proof.get('trial_id') != manifest.get('trial_id') or proof.get('maintenance_released') is not True or proof.get('runtime_controls_active') is not True:
        blockers.append('LIVE_RUNTIME_GATE_PENDING')
    received = proof.get('received_at')
    if type(received) not in (int, float) or not math.isfinite(received) or not 0 <= now - received <= 300:
        blockers.append('LIVE_PROOF_STALE')
    try:
        database = _absolute(manifest['paths']['account_operation_db'])
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as c:
            resource = c.execute('SELECT status,expires_at,config_revision,observed_exit_ip,observed_exit_at,cooldown_until,stop_reason FROM proxy_resources WHERE id=?', (proof.get('proxy_id'),)).fetchone()
            account = c.execute('SELECT proxy_required,proxy_id,binding_revision,status FROM crawler_accounts WHERE id=?', (proof.get('account_id'),)).fetchone()
        if (not resource or resource[0] != 'active' or resource[1] is None or resource[1] <= now or
                resource[2] != proof.get('config_revision') or resource[5] and resource[5] > now or resource[6]):
            blockers.append('LIVE_RESOURCE_NOT_ACTIVE')
        if (not account or not account[0] or account[1] != proof.get('proxy_id') or
                account[2] != proof.get('binding_revision') or account[3] != 'active'):
            blockers.append('LIVE_ACCOUNT_BINDING_INVALID')
        channels = [proof.get(name) for name in ('browser', 'http')]
        fields = {'context_id', 'ip', 'observed_at', 'config_revision'}
        if any(not isinstance(channel, dict) or set(channel) != fields or
               not channel.get('context_id') or channel.get('config_revision') != proof.get('config_revision') or
               type(channel.get('observed_at')) not in (int, float) or
               not math.isfinite(channel['observed_at']) or not 0 <= now - channel['observed_at'] <= 300 for channel in channels):
            blockers.append('LIVE_DUAL_PROBE_INVALID')
        elif (channels[0]['ip'] != str(ipaddress.ip_address(channels[0]['ip'])) or
              channels[0]['context_id'] != channels[1]['context_id'] or
              channels[0]['ip'] != channels[1]['ip'] or not resource or
              channels[0]['ip'] != resource[3] or resource[4] is None or not 0 <= now - resource[4] <= 300):
            blockers.append('LIVE_DUAL_PROBE_MISMATCH')
    except (sqlite3.Error, KeyError, TypeError):
        blockers.append('LIVE_DATABASE_UNVERIFIED')
    scheduler_error = _scheduler(_absolute(manifest['paths']['scheduler_db']), now)
    if scheduler_error:
        blockers.append(scheduler_error)
    return dict(decision='NO-GO' if blockers else 'LIVE_EVIDENCE_CONSISTENT',
        trial_ready=not blockers, ready_label='requires_live_gate' if blockers else 'manual_trial_candidate',
        blockers=sorted(set(blockers)), authorization=False, network_started=False,
        note='Read-only workflow opinion; owner permissions, leases and runtime dispatch checks still required')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    for name in ('app-root', 'crawler-root', 'trial-root', 'trial-id', 'scheduler-db',
                 'app-python', 'crawler-python', 'browser-binary'):
        prepare_parser.add_argument('--' + name, required=True)
    prepare_parser.add_argument('--echo-url', default='')
    prepare_parser.add_argument('--evidence-path')
    prepare_parser.add_argument('--resource-path', help='Metadata only; never username/password')
    prepare_parser.add_argument('--dedicated-test-account', action='store_true')
    prepare_parser.add_argument('--write', action='store_true', help='Create private isolated trial directory; never starts runtime')
    validate_parser = commands.add_parser('validate')
    validate_parser.add_argument('--manifest-path', required=True)
    validate_parser.add_argument('--live-proof-path', required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        result = prepare(**args) if command == 'prepare' else validate_live_gate(**args)
    except (ValueError, TypeError, OSError, json.JSONDecodeError):
        print(json.dumps({'decision': 'NO-GO', 'code': 'TRIAL_INPUT_INVALID'}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0  # Read-only preparation success is not a trial-readiness exit code.


if __name__ == '__main__':
    raise SystemExit(main())
