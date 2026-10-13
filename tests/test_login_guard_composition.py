"""Offline real TLS/proxy experiment for Playwright and CDP interception."""

import json
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_route as app
from test_managed_route_integration import managed, store_operation


@pytest.mark.parametrize("mode", ["route_only", "cdp_only", "mixed"])
def test_real_login_hydration_guard_composition(tmp_path, mode):
    import cv2
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from proxy_fixture import local_proxy

    with local_proxy(tmp_path / "network") as fixture:
        parent = fixture.https.RequestHandlerClass
        qr = cv2.QRCodeEncoder_create().encode(
            "https://fixture.invalid/guard-composition"
        )
        qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        qr = cv2.resize(qr, (180, 180), interpolation=cv2.INTER_NEAREST)
        _, png = cv2.imencode(".png", qr)
        script_url = fixture.url("/bootstrap.js", host="script")
        style_url = fixture.url("/app.css", host="style")
        image_url = fixture.url("/qr.png", host="image")

        class Origin(parent):
            def do_GET(self):
                if self.path not in {
                    "/hydration",
                    "/bootstrap.js",
                    "/app.css",
                    "/qr.png",
                }:
                    return super().do_GET()
                with fixture.lock:
                    link = fixture.upstreams.get(self.client_address[1])
                if link is None:
                    fixture.traps.append({"path": self.path})
                    self.send_error(403)
                    return
                fixture.origins.append(
                    dict(
                        path=self.path,
                        host=self.headers.get("Host"),
                        peer=self.client_address[1],
                        proxy_event=link,
                        request_id="origin-" + str(len(fixture.origins) + 1),
                        proxy_authorization=self.headers.get("Proxy-Authorization"),
                    )
                )
                if self.path == "/bootstrap.js":
                    time.sleep(0.15)
                    content_type = "application/javascript"
                    body = b"window.hydrated=true;document.querySelector('#login').onclick=()=>{document.querySelector('#panel').style.display='block'}"
                elif self.path == "/app.css":
                    content_type = "text/css"
                    body = b"#login{width:80px;height:40px;background:red}"
                elif self.path == "/qr.png":
                    content_type = "image/png"
                    body = png.tobytes()
                else:
                    content_type = "text/html; charset=utf-8"
                    body = (
                        f'<html><head><link rel="stylesheet" href="{style_url}"></head><body><div id="login" role="button">登录</div>'
                        '<div id="panel" class="douyin_login_new_class" style="display:none;width:400px;height:300px">'
                        f'<div id="animate_qrcode_container"><img id="qr" src="{image_url}"></div></div>'
                        f'<script src="{script_url}"></script></body></html>'
                    ).encode()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        fixture.https.RequestHandlerClass = Origin
        accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
        account = accounts.create(platform="dy", display_name="fixture")
        accounts.save_auth_state(account["id"], "fixture-ciphertext")
        accounts.observe_shared_exit(fixture.exit_ip)
        contract = app.route_contract()
        snap = replace(
            managed(tmp_path),
            account_id=account["id"],
            profile_key=contract.derive_profile_key(account["id"]),
            host="127.0.0.1",
            port=fixture.proxy.server_port,
            username=fixture.username,
            password=fixture.password,
            observed_exit_ip=fixture.exit_ip,
            scope_ids=contract.derive_scope_ids(
                "dy", account["id"], "p", fixture.exit_ip
            ),
            safety_db_path=str(accounts.db_path),
        )
        snap = replace(snap, snapshot_digest=contract.snapshot_digest(snap))
        accounts.admit_route(account["id"], fixture.exit_ip, 1, snap.operation_id)
        store = app.RouteOperationStore(tmp_path / "ops.db")
        store_operation(store, snap)
        bundle = SimpleNamespace(
            snapshot=snap,
            route_operation_store=store,
            _lock=threading.RLock(),
            account_store=accounts,
            proxy_store=None,
        )
        route_pipe, receipt_pipe = (
            app.RouteConfigPipe(snap),
            app.RouteReceiptPipe(bundle),
        )
        script = r"""
import asyncio,json,os,socket,sys
from pathlib import Path
from urllib.parse import urlsplit
from cloakbrowser.config import get_binary_path
assert Path(get_binary_path()).is_file()
os.environ['CLOAKBROWSER_BINARY_PATH']=str(get_binary_path())
os.environ['CLOAKBROWSER_AUTO_UPDATE']='false'
config=json.load(sys.stdin)
original=socket.getaddrinfo
def local_dns(host,*args,**kwargs):
 local=isinstance(host,str) and host.endswith('.proxy-test.invalid')
 return original('127.0.0.1' if local else host,*args,**kwargs)
socket.getaddrinfo=local_dns
from tools.bound_proxy import initialize_bound_route,BoundProxySession
from tools.cloak_browser import launch_account_context
from scripts import crawler_douyin_login_adapter as module
allowed=module.document_allowed
module.document_allowed=lambda url,top_level: urlsplit(url).hostname=='origin.proxy-test.invalid' or allowed(url,top_level=top_level)
async def main():
 snap=initialize_bound_route()
 session=BoundProxySession(snap,echo_url=config['echo'],verify=config['ca'],budget_id='composition',policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
 context,profile=await launch_account_context(snap.account_id,Path(config['profiles']),headless=True,route_snapshot=snap,bound_proxy_session=session,browser_args=['--ignore-certificate-errors-spki-list='+config['spki'],'--host-resolver-rules=MAP *.proxy-test.invalid 127.0.0.1','--proxy-bypass-list=<-loopback>'])
 adapter=None
 try:
  if config['mode']=='cdp_only':
   await context.unroute(session._echo_pattern,session._echo_context_handler)
  page=await context.new_page()
  adapter=module.DouyinLoginAdapter(context,page,bound_proxy_session=session)
  if config['mode']!='route_only':await adapter.install()
  await page.goto(config['target'],wait_until='domcontentloaded',timeout=5000)
  await page.wait_for_function('window.hydrated===true && document.readyState==="complete"',timeout=5000)
  await adapter.open_login()
  result=await adapter.observe()
  evidence=dict(mode=config['mode'],ready=await page.evaluate('document.readyState'),hydrated=await page.evaluate('window.hydrated'),phase=result['phase'],blocked_documents=adapter.blocked_documents)
  print(json.dumps(evidence),flush=True)
  assert result['phase']=='qr_ready'
 finally:
  if adapter:await adapter.close()
  await context.close()
asyncio.run(main())
"""
        crawler = str(app.settings.media_crawler_dir.resolve())
        env = {
            **os.environ,
            **route_pipe.env,
            **receipt_pipe.env,
            "PYTHONPATH": os.pathsep.join(
                [str(Path(__file__).resolve().parents[1]), crawler]
            ),
            "MEDIACRAWLER_ACCOUNT_ID": account["id"],
            "MEDIACRAWLER_PROXY_REQUIRED": "true",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
        child = subprocess.Popen(
            [
                "/Users/hose/project/zhanyun/media-crawler/.venv/bin/python",
                "-c",
                script,
            ],
            env=env,
            cwd=crawler,
            pass_fds=(*route_pipe.pass_fds, *receipt_pipe.pass_fds),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            route_pipe.start(child)
            receipt_pipe.start(child)
            config = dict(
                mode=mode,
                echo=fixture.url(host="echo"),
                target=fixture.url("/hydration"),
                ca=fixture.ca,
                spki=fixture.spki,
                profiles=str(tmp_path / "profiles"),
            )
            out, err = child.communicate(json.dumps(config), timeout=25)
            assert fixture.password not in out + err
            assert child.returncode == 0, err[-2500:]
            evidence = json.loads(out.strip().splitlines()[-1])
            assert evidence == dict(
                mode=mode,
                ready="complete",
                hydrated=True,
                phase="qr_ready",
                blocked_documents=0,
            )
            assert {"/hydration", "/bootstrap.js", "/app.css", "/qr.png"} <= {
                item["path"] for item in fixture.origins
            }
            fixture.assert_routed()
            print("LOGIN_GUARD_COMPOSITION " + json.dumps(evidence))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            route_pipe.close()
            receipt_pipe.close()
