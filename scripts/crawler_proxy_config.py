"""Local backend operations for the existing encrypted crawler proxy store.

One JSON metadata operation per invocation; transport credentials enter through
getpass or stdin only. This tool neither grants ACL access nor sends requests.
"""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CREATE_FIELDS = frozenset({'display_name', 'endpoint', 'provider', 'platform',
                          'expires_at', 'max_bound_accounts', 'max_active_accounts'})
UPDATE_FIELDS = CREATE_FIELDS - {'platform'} | {'status', 'clear_credentials'}
ERROR_CODES = frozenset({'PROXY_CONFIG_INVALID', 'PROXY_BUSY', 'PROXY_NOT_FOUND',
    'PROXY_REVISION_MISMATCH', 'PROXY_PLATFORM_UNSUPPORTED', 'PROXY_PROVIDER_UNSUPPORTED',
    'PROXY_INVALID_ENDPOINT', 'PROXY_INVALID_CREDENTIALS', 'PROXY_INVALID_TIME',
    'PROXY_INVALID_CAPACITY', 'PROXY_CAPACITY_CONFLICT', 'PROXY_PLATFORM_MISMATCH',
    'PROXY_INVALID_STATUS', 'PROXY_RESOURCE_BOUND', 'PROXY_UNAVAILABLE'})
OUTPUT_FIELDS = ('id', 'config_revision', 'status', 'has_credentials',
                 'max_bound_accounts', 'max_active_accounts', 'bound_accounts')


def apply_config(store, config, *, credentials=None):
    if not isinstance(config, dict):
        raise ValueError('PROXY_CONFIG_INVALID')
    operation = config.get('operation')
    fields = config.get('fields', {})
    if not isinstance(fields, dict):
        raise ValueError('PROXY_CONFIG_INVALID')
    if 'clear_credentials' in fields and type(fields['clear_credentials']) is not bool:
        raise ValueError('PROXY_CONFIG_INVALID')
    for key, minimum in (('expected_revision', 1), ('expected_binding_revision', 0)):
        if key in config and (type(config[key]) is not int or config[key] < minimum):
            raise ValueError('PROXY_CONFIG_INVALID')
    if operation == 'create':
        if set(config) != {'operation', 'fields'} or set(fields) - CREATE_FIELDS:
            raise ValueError('PROXY_CONFIG_INVALID')
        return store.create(**fields, credentials=credentials)
    if operation == 'update':
        if set(config) != {'operation', 'proxy_id', 'expected_revision', 'fields'} or set(fields) - UPDATE_FIELDS:
            raise ValueError('PROXY_CONFIG_INVALID')
        updates = dict(fields)
        if credentials is not None:
            updates['credentials'] = credentials
        if updates.get('status') == 'active':
            from backend.audit_agent.crawler_route import activate_proxy_route
            return activate_proxy_route(config['proxy_id'], expected_revision=config['expected_revision'],
                                        account_store=store.accounts, **updates)
        return store.update(config['proxy_id'], expected_revision=config['expected_revision'], **updates)
    if credentials is not None:
        raise ValueError('PROXY_CONFIG_INVALID')
    if operation == 'safety-policy':
        if (set(config) != {'operation', 'account_id', 'expected_binding_revision', 'route_mode'}
                or config['route_mode'] not in {'proxy_required', 'proxy_preferred'}):
            raise ValueError('PROXY_CONFIG_INVALID')
        from backend.task_admission.resources import account_lease
        with account_lease('dy', config['account_id']) as acquired:
            if not acquired:
                raise ValueError('PROXY_BUSY')
            with store.accounts._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                account = store.accounts.get(config['account_id'], connection=conn)
                if not account or account['platform'] != 'dy':
                    raise ValueError('PROXY_NOT_FOUND')
                if account['binding_revision'] != config['expected_binding_revision']:
                    raise ValueError('PROXY_REVISION_MISMATCH')
                if conn.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'", (account['id'],)).fetchone():
                    raise ValueError('PROXY_BUSY')
                conn.execute('UPDATE crawler_accounts SET route_mode=?,proxy_required=? WHERE id=?',
                    (config['route_mode'], int(config['route_mode']=='proxy_required'), account['id']))
                conn.execute("INSERT INTO crawler_route_events(account_id,event_type,reason,created_at) VALUES(?,'policy_updated','OPERATOR_POLICY',?)", (account['id'], __import__('time').time()))
            return {'account_id':account['id'], 'proxy_id':account['proxy_id'], 'binding_revision':account['binding_revision']}
    if operation == 'bind' and set(config) == {'operation', 'account_id', 'proxy_id', 'expected_binding_revision'}:
        item = store.bind(config['account_id'], config['proxy_id'], expected_binding_revision=config['expected_binding_revision'])
        return {'account_id': item['id'], 'proxy_id': item['proxy_id'], 'binding_revision': item['binding_revision']}
    if operation == 'unbind' and set(config) == {'operation', 'account_id', 'expected_binding_revision'}:
        item = store.unbind(config['account_id'], expected_binding_revision=config['expected_binding_revision'])
        return {'account_id': item['id'], 'proxy_id': item['proxy_id'], 'binding_revision': item['binding_revision']}
    raise ValueError('PROXY_CONFIG_INVALID')


def main(argv=None, *, store=None):
    # Never let argparse echo an unknown argv value (it could be a pasted secret).
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError('PROXY_CONFIG_INVALID')

    parser = SafeParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--apply', '--config', dest='config', type=Path, help='Apply one metadata JSON operation')
    action.add_argument('--list', action='store_true', help='List safe resource IDs and revisions')
    secrets = parser.add_mutually_exclusive_group()
    secrets.add_argument('--prompt-credentials', action='store_true')
    secrets.add_argument('--credentials-stdin', action='store_true', help='Read username/password JSON from stdin')
    try:
        args = parser.parse_args(argv)
        if args.list and (args.prompt_credentials or args.credentials_stdin):
            raise ValueError('PROXY_CONFIG_INVALID')
        config = json.loads(args.config.read_text()) if args.config else None
        credentials = None
        if args.prompt_credentials:
            if not sys.stdin.isatty():
                raise ValueError('PROXY_INVALID_CREDENTIALS')
            credentials = {'username': getpass.getpass('Proxy username: '), 'password': getpass.getpass('Proxy password: ')}
        elif args.credentials_stdin:
            credentials = json.load(sys.stdin)
            from backend.audit_agent.crawler_proxy_store import _credentials
            credentials = _credentials(credentials)
        if store is None:
            from backend.audit_agent.crawler_route import get_proxy_store
            store = get_proxy_store()
        if args.list:
            result = {'items': [{key: item[key] for key in OUTPUT_FIELDS} for item in store.list()]}
        else:
            item = apply_config(store, config, credentials=credentials)
            keys = OUTPUT_FIELDS if 'config_revision' in item else ('account_id', 'proxy_id', 'binding_revision')
            result = {'item': {key: item[key] for key in keys}}
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as exc:
        code = getattr(exc, 'code', None) or str(exc)
        print(json.dumps({'code': code if code in ERROR_CODES else 'PROXY_CONFIG_INVALID'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
