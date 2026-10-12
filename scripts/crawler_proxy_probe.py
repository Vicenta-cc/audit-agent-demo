"""Echo-only maintenance probe, executed by the configured crawler interpreter."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


async def probe(account_id, *, check_login=False, expected_identity='', hold_context=False):
    crawler_root = Path(os.environ['MEDIACRAWLER_DIR']).resolve()
    sys.path.insert(0, str(crawler_root))
    from tools.collection_route import load_route_from_env
    snapshot = load_route_from_env(account_id=account_id, proxy_required=True)
    from tools.bound_proxy import BoundProxySession
    from tools.cloak_browser import launch_account_context
    options={}
    if hold_context:
        options=dict(reuse_existing_policy=True,account_policy=json.loads(os.environ['MEDIACRAWLER_ASSET_ACCOUNT_POLICY']))
    session = BoundProxySession(snapshot, maintenance_only=not check_login and not hold_context, **options)
    context = None
    try:
        context, _ = await launch_account_context(account_id,
            Path(os.environ['MEDIACRAWLER_CLOAK_PROFILE_ROOT']), headless=True,
            route_snapshot=snapshot, bound_proxy_session=session)
        receipt = session.receipt
        login_result = 'uncertain'
        if check_login:
            from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
            page = await context.new_page()
            adapter = await DouyinLoginAdapter(context, page, block_media=True, bound_proxy_session=session).install()
            try:
                await adapter.navigate('self')
                try:
                    await asyncio.wait_for(adapter.identity_observed.wait(),5)
                except asyncio.TimeoutError:
                    pass
                observation = await adapter.observe_once()
                if observation.get('phase') in {'challenge_required', 'manual_required', 'sms_required'}:
                    login_result = 'challenge'
                    if not snapshot.recovery_claim:
                        from tools.bound_proxy import emit_account_risk
                        emit_account_risk(snapshot)
                elif (expected_identity and adapter.identity_source == 'douyin_self_response'
                        and not adapter.identity_conflict and adapter.identity == expected_identity):
                    login_result = 'success'
            finally:
                await adapter.close()
        print('PROXY_PROBE_RESULT ' + json.dumps(dict(schema_version=1,
            account_id=snapshot.account_id, proxy_id=snapshot.proxy_id,
            binding_revision=snapshot.binding_revision, config_revision=snapshot.config_revision,
            context_id=receipt.context_id, ip=receipt.ip, observed_at=receipt.observed_at,
            **({'login_result':login_result} if check_login else {})),
            separators=(',', ':'), allow_nan=False), flush=True)
        if hold_context:
            command=await asyncio.wait_for(asyncio.to_thread(sys.stdin.readline),1200)
            if command.strip() not in {'close',''}:
                raise ValueError('PROXY_CONFIG_INVALID')
    finally:
        if context is not None:
            await context.close()
        else:
            await session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--check-login', action='store_true')
    parser.add_argument('--expected-identity', default='')
    parser.add_argument('--hold-context',action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(probe(args.account_id, check_login=args.check_login, expected_identity=args.expected_identity,hold_context=args.hold_context))
    except Exception as exc:
        allowed = {'PROXY_BUSY','PROXY_REVISION_MISMATCH','PROXY_EXIT_CHANGED',
                   'PROXY_UNAVAILABLE','PROXY_AUTH_FAILED','PROXY_CONFIG_INVALID',
                   'PROBE_BUDGET_EXHAUSTED','SCHEDULER_POLICY_CONFLICT'}
        code = getattr(exc, 'code', None)
        code = code if code in allowed else 'PROXY_UNAVAILABLE'
        print('PROXY_PROBE_ERROR ' + json.dumps({'code':code}), flush=True)
        raise SystemExit(1)
