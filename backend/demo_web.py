"""Serve the built SPA and stream API traffic, including SSE, to one local API."""
import os
from pathlib import Path
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from starlette.responses import FileResponse, StreamingResponse
from starlette.staticfiles import StaticFiles
from starlette.background import BackgroundTask

DIST = Path(__file__).resolve().parents[1] / 'Audit_assistant' / 'dist'
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade', 'host'}

@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{int(os.environ["XHS_AUDIT_BACKEND_PORT"])}', timeout=None, trust_env=False) as client:
        app.state.client = client
        yield

app = FastAPI(lifespan=lifespan)

@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
async def proxy(request: Request, path: str):
    url = httpx.URL(path='/api/' + path, query=request.url.query.encode())
    upstream_request = app.state.client.build_request(request.method, url, headers={k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}, content=request.stream())
    response = await app.state.client.send(upstream_request, stream=True)
    return StreamingResponse(response.aiter_raw(), status_code=response.status_code, headers={k: v for k, v in response.headers.items() if k.lower() not in HOP_HEADERS}, background=BackgroundTask(response.aclose))

app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

@app.get('/{path:path}')
def spa(path: str):
    candidate = (DIST / path).resolve()
    if candidate.is_relative_to(DIST) and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(DIST / 'index.html')
