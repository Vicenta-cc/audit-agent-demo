"""Local two-backend gateway. Routing uses durable task ownership, never UI state."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length'}


def production_ids(registry: dict) -> set[str]:
    """Only registered production tasks and their descendants belong to production."""
    ids = set(registry.get('workspace_ids', [])) | set(registry.get('draft_ids', []))
    root = Path(registry['data_directory'])
    drafts = list(registry.get('draft_ids', []))
    sessions = list(registry.get('workspace_ids', []))
    if drafts:
        with sqlite3.connect((root / 'investigation_creation.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=5) as c:
            c.row_factory = sqlite3.Row
            for r in c.execute('SELECT id,job_id,report_version_id,report_session_id FROM investigation_runs WHERE draft_id IN (' + ','.join('?' for _ in drafts) + ')', drafts):
                ids.update(str(v) for v in r if v)
                if r['report_session_id']:
                    sessions.append(r['report_session_id'])
    if sessions:
        with sqlite3.connect((root / 'investigation.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=5) as c:
            for (turn_id,) in c.execute('SELECT id FROM investigation_turns WHERE session_id IN (' + ','.join('?' for _ in sessions) + ')', sessions):
                ids.add(turn_id)
    return ids


def backend_for_path(path: str, owned: set[str]) -> str:
    segments = set(unquote(path).strip('/').split('/'))
    return 'production' if segments & owned else 'demo'


def create_app(registry_path: Path, demo_url: str, production_url: str,
               transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, read=None), transport=transport) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan)
    urls = {'demo': demo_url.rstrip('/'), 'production': production_url.rstrip('/')}

    def registry():
        return json.loads(registry_path.read_text())

    @app.get('/api/runtime-status')
    async def runtime_status():
        async def check(name, url):
            try:
                r = await app.state.client.get(url + '/api/config', timeout=5)
                r.raise_for_status()
                payload = r.json()
                return name, {'available': True, 'data_directory': payload.get('runtime_data_dir'),
                              'outputs_directory': payload.get('outputs_dir'),
                              'boundary_error': payload.get('runtime_boundary_error')}
            except httpx.HTTPError:
                return name, {'available': False}
        return dict(await asyncio.gather(*(check(n, u) for n, u in urls.items())))

    @app.get('/api/investigation-workspaces')
    async def workspaces(request: Request):
        async def read_all(name):
            items = []
            offset = 0
            try:
                while True:
                    r = await app.state.client.get(urls[name] + '/api/investigation-workspaces',
                                                   params={'limit': 100, 'offset': offset}, timeout=15)
                    r.raise_for_status()
                    page = r.json()
                    rows = page.get('items', [])
                    items.extend(rows)
                    if not page.get('has_more') or not rows:
                        return items, None
                    offset += len(rows)
            except (httpx.HTTPError, ValueError):
                return [], name
        demo, production = await asyncio.gather(read_all('demo'), read_all('production'))
        allowed = set(registry().get('workspace_ids', []))
        rows = demo[0] + [r for r in production[0] if r['workspace_session_id'] in allowed]
        rows.sort(key=lambda r: (r.get('updated_at', ''), r['workspace_session_id']), reverse=True)
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
            limit = min(100, max(1, int(request.query_params.get('limit', 50))))
        except ValueError:
            return JSONResponse({'detail': 'Invalid pagination'}, status_code=422)
        unavailable = [name for name in [demo[1], production[1]] if name]
        return JSONResponse({'items': rows[offset:offset+limit], 'has_more': len(rows) > offset+limit,
                             'unavailable_runtimes': unavailable},
                            status_code=503 if len(unavailable) == 2 else 200)

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS', 'HEAD'])
    async def forward(path: str, request: Request):
        try:
            owned = production_ids(registry())
            name = backend_for_path(path, owned)
            # Audit result numeric IDs can collide across databases; route these
            # reads by the explicit originating job, never by the result ID.
            if request.method == 'GET' and (path == 'api/audit-results' or path.startswith('api/audit-results/')):
                name = 'production' if request.query_params.get('job_id') in owned else 'demo'
        except (OSError, sqlite3.Error, ValueError):
            # Never misroute a mutation when ownership cannot be established.
            return JSONResponse({'detail': 'Task routing registry is unavailable'}, status_code=503)
        url = urls[name] + '/' + path
        if request.url.query:
            url += '?' + request.url.query
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        try:
            upstream_request = app.state.client.build_request(request.method, url, headers=headers,
                                                               content=await request.body())
            upstream = await app.state.client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            return JSONResponse({'detail': f'{name} backend is unavailable; request was not sent to another backend'}, status_code=502)
        outgoing = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS}
        outgoing['x-task-runtime'] = name
        outgoing['cache-control'] = 'no-store'
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 headers=outgoing, background=BackgroundTask(upstream.aclose))
    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(Path(os.environ['DUAL_RUNTIME_REGISTRY']),
                          os.environ['DEMO_API_URL'], os.environ['PRODUCTION_API_URL']),
                host='127.0.0.1', port=int(os.environ.get('GATEWAY_PORT', '8148')))
