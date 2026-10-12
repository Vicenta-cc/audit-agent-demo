"""Echo-only maintenance probe, executed by the configured crawler interpreter."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


async def probe(account_id):
    crawler_root = Path(os.environ['MEDIACRAWLER_DIR']).resolve()
    sys.path.insert(0, str(crawler_root))
    from tools.collection_route import load_route_from_env
    snapshot = load_route_from_env(account_id=account_id, proxy_required=True)
    from tools.bound_proxy import BoundProxySession
    from tools.cloak_browser import launch_account_context
    session = BoundProxySession(snapshot, maintenance_only=True)
    context = None
    try:
        context, _ = await launch_account_context(account_id,
            Path(os.environ['MEDIACRAWLER_CLOAK_PROFILE_ROOT']), headless=True,
            route_snapshot=snapshot, bound_proxy_session=session)
        receipt = session.receipt
        print('PROXY_PROBE_RESULT ' + json.dumps(dict(schema_version=1,
            account_id=snapshot.account_id, proxy_id=snapshot.proxy_id,
            binding_revision=snapshot.binding_revision, config_revision=snapshot.config_revision,
            context_id=receipt.context_id, ip=receipt.ip, observed_at=receipt.observed_at),
            separators=(',', ':'), allow_nan=False), flush=True)
    finally:
        if context is not None:
            await context.close()
        else:
            await session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--account-id', required=True)
    args = parser.parse_args()
    try:
        asyncio.run(probe(args.account_id))
    except Exception as exc:
        allowed = {'PROXY_BUSY','PROXY_REVISION_MISMATCH','PROXY_EXIT_CHANGED',
                   'PROXY_UNAVAILABLE','PROXY_AUTH_FAILED','PROXY_CONFIG_INVALID',
                   'PROBE_BUDGET_EXHAUSTED','SCHEDULER_POLICY_CONFLICT'}
        code = getattr(exc, 'code', None)
        code = code if code in allowed else 'PROXY_UNAVAILABLE'
        print('PROXY_PROBE_ERROR ' + json.dumps({'code':code}), flush=True)
        raise SystemExit(1)
