"""Exercise the real resource UI/API against disposable stores, never backend.main."""
from pathlib import Path
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from resource_experiment import environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',default=shutil.which('node'))
    parser.add_argument('--chromium',default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH',''))
    args=parser.parse_args()
    if not args.node:parser.error('--node is required when Node is not on PATH')
    with tempfile.TemporaryDirectory(prefix='m3-resource-browser-') as directory:
        temp=Path(directory)
        isolated=environment(temp)
        os.environ.clear();os.environ.update(isolated)
        sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
        from test_investigation_creation_conversation import creation_stack
        from backend.resource_management.api import create_resource_router
        import uvicorn
        fixture=creation_stack.__wrapped__(temp)
        stack=next(fixture)
        app=stack['client'].app
        app.include_router(create_resource_router(stack['app_service'],stack['conversation'],stack['principals']))
        sock=socket.socket();sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='warning'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
        thread.start()
        evidence=ROOT/'docs/evidence/m3-resource-lifecycle'
        env={**isolated,'VITE_API_PROXY_TARGET':f'http://127.0.0.1:{port}','RESOURCE_EVIDENCE':str(evidence),'PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH':args.chromium}
        try:
            result=subprocess.run([args.node,'tests/resourceLifecycle.smoke.mjs'],cwd=ROOT/'Audit_assistant',env=env)
            with stack['creation_store']._connect() as conn:
                counts={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('investigation_drafts','investigation_runs')}
            assert all(v==0 for v in counts.values()),counts
            (evidence/'browser-result.json').write_text(json.dumps({'exit_code':result.returncode,'task_counts':counts,'storage':'disposable','provider_calls':0,'crawler_calls':0},indent=2)+'\n')
            if result.returncode:raise SystemExit(result.returncode)
        finally:
            server.should_exit=True;thread.join(timeout=10)
            next(fixture,None)

if __name__=='__main__':main()
