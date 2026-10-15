import asyncio
import tempfile
import ssl
import socketserver
import threading
import shutil
import subprocess
import json
import datetime
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CAMOUFOX_LOGIN_TESTS") != "1",
    reason="requires installed pinned Camoufox",
)


async def main():
    from camoufox.async_api import AsyncCamoufox
    from backend.audit_agent.config import settings
    import sys

    sys.path.insert(0, str(settings.media_crawler_dir))
    from tools.navigation_guard_addon import (
        build_navigation_guard,
        require_navigation_guard,
    )

    with tempfile.TemporaryDirectory(prefix="camoufox-native-policy-") as root:
        root = Path(root)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture")])
        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "native guard fixture CA")]
        )
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(ca_key, hashes.SHA256())
        )
        (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("www.douyin.com"),
                        x509.DNSName("blocked.invalid"),
                        x509.DNSName("api.ip.sb"),
                        x509.DNSName("lf-rc1.yhgfb-cn-static.com"),
                    ]
                ),
                False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "cert.pem", root / "key.pem")
        hits = []
        origin_credentials = []
        connects = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline().decode().strip()
                if not line:
                    return
                method, target, _ = line.split(" ")
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                if method != "CONNECT":
                    return
                connects.append(target)
                self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self.wfile.flush()
                try:
                    stream = tls.wrap_socket(self.connection, server_side=True)
                    file = stream.makefile("rb")
                    request = file.readline().decode().strip()
                    if not request:
                        return
                    method, path, _ = request.split(" ")
                    host = ""
                    headers = {}
                    while True:
                        line = file.readline()
                        if line in (b"\r\n", b"\n", b""):
                            break
                        name, _, value = line.decode().partition(":")
                        headers[name.lower()] = value.strip()
                        if line.lower().startswith(b"host:"):
                            host = line.decode().split(":", 1)[1].strip()
                    hits.append((host, path))
                    origin_credentials.append(
                        (host, headers.get("cookie"), headers.get("authorization"))
                    )
                    if path in {"/redirect", "/redirect-echo"}:
                        target = (
                            "https://api.ip.sb/ip"
                            if path == "/redirect-echo"
                            else "https://blocked.invalid/redirect"
                        )
                        response = (
                            "HTTP/1.1 302 Found\r\nLocation: "
                            + target
                            + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        ).encode()
                    else:
                        body = b"<html><body>fixture</body></html>"
                        response = (
                            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                            + str(len(body)).encode()
                            + b"\r\nConnection: close\r\n\r\n"
                            + body
                        )
                    stream.sendall(response)
                    stream.close()
                except (OSError, ssl.SSLError):
                    pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (root / "addon").mkdir(mode=0o700)
        guard = build_navigation_guard(root / "addon", "https://api.ip.sb/ip")
        profile = root / "profile"
        profile.mkdir(mode=0o700)
        certutil = shutil.which("certutil")
        assert certutil, "test-only NSS certutil is required"
        subprocess.run(
            [certutil, "-N", "-d", "sql:" + str(profile), "--empty-password"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                certutil,
                "-A",
                "-d",
                "sql:" + str(profile),
                "-n",
                "native guard fixture CA",
                "-t",
                "C,,",
                "-i",
                str(root / "ca.pem"),
            ],
            check=True,
            capture_output=True,
        )
        try:
            async with AsyncCamoufox(
                headless=(__import__("sys").platform == "darwin"),
                persistent_context=True,
                user_data_dir=str(root / "profile"),
                addons=[guard["directory"]],
                firefox_user_prefs=guard["prefs"],
                service_workers="block",
                proxy={"server": f"http://127.0.0.1:{server.server_address[1]}"},
            ) as c:
                await require_navigation_guard(c, guard)
                p = await c.new_page()
                await p.goto("https://www.douyin.com/allowed", timeout=10000)
                for u in (
                    "https://blocked.invalid/top",
                    "https://www.douyin.com/redirect",
                    "https://www.douyin.com/redirect-echo",
                ):
                    try:
                        await p.goto(u, timeout=3000)
                    except Exception:
                        pass
                await p.goto("https://www.douyin.com/allowed2", timeout=10000)
                await p.evaluate(
                    """() => {const f=document.createElement('iframe');f.src='https://blocked.invalid/frame';document.body.append(f);window.open('https://blocked.invalid/popup');}"""
                )
                await asyncio.sleep(1)
                assert len([x for x in hits if x[0] == "www.douyin.com"]) >= 3, hits
                assert not any("blocked.invalid" in host for host, path in hits), hits
                assert not any("blocked.invalid" in target for target in connects), (
                    connects
                )
                assert not any("api.ip.sb" in host for host, _ in hits), hits
                await c.add_cookies(
                    [
                        {
                            "name": "origin-session",
                            "value": "fixture-cookie",
                            "url": "https://api.ip.sb",
                        }
                    ]
                )
                await c.set_extra_http_headers(
                    {"Authorization": "Bearer fixture-origin-auth"}
                )
                await p.goto("https://api.ip.sb/ip", timeout=10000)
                assert len([1 for host, _ in hits if host == "api.ip.sb"]) == 1
                assert all(
                    cookie is None and authorization is None
                    for host, cookie, authorization in origin_credentials
                    if host == "api.ip.sb"
                ), origin_credentials
                from scripts.crawler_login_pages import (
                    export_douyin_auth_state,
                    reset_douyin_credentials_v2,
                )

                await c.set_extra_http_headers({})
                await p.goto("https://www.douyin.com/auth-export", timeout=10000)
                await p.evaluate("localStorage.setItem('first-party-fixture','keep')")
                await p.evaluate(
                    """() => {const f=document.createElement('iframe');f.src='https://lf-rc1.yhgfb-cn-static.com/verification';document.body.append(f)}"""
                )
                third = None
                for _ in range(50):
                    third = next(
                        (
                            frame
                            for frame in p.frames
                            if frame.url.startswith(
                                "https://lf-rc1.yhgfb-cn-static.com/"
                            )
                        ),
                        None,
                    )
                    if third:
                        await third.wait_for_load_state(
                            "domcontentloaded", timeout=5000
                        )
                        break
                    await asyncio.sleep(0.1)
                assert third is not None
                await third.evaluate(
                    "localStorage.setItem('third-party-fixture','never-export')"
                )
                await c.add_cookies(
                    [
                        {
                            "name": "sessionid",
                            "value": "first-party-fixture",
                            "domain": ".douyin.com",
                            "path": "/",
                            "secure": True,
                        },
                        {
                            "name": "verification-cookie",
                            "value": "never-export",
                            "domain": "lf-rc1.yhgfb-cn-static.com",
                            "path": "/",
                            "secure": True,
                        },
                    ]
                )
                # RED reproduction: all-origin export promotes the verification
                # iframe origin to a main-frame navigation, correctly blocked.
                with pytest.raises(Exception, match="NS_ERROR_ABORT"):
                    await asyncio.wait_for(c.storage_state(), 8)
                before_export = len(hits)
                state = await export_douyin_auth_state(c)
                assert len(hits) == before_export
                assert {cookie["domain"] for cookie in state["cookies"]} == {
                    ".douyin.com"
                }
                assert {origin["origin"] for origin in state["origins"]} == {
                    "https://www.douyin.com"
                }
                assert state["origins"][0]["localStorage"] == [
                    {"name": "first-party-fixture", "value": "keep"}
                ]
                assert (
                    await p.evaluate("localStorage.getItem('first-party-fixture')")
                    == "keep"
                )
                assert (
                    await third.evaluate("localStorage.getItem('third-party-fixture')")
                    == "never-export"
                )
                assert any(
                    cookie["name"] == "sessionid" for cookie in await c.cookies()
                )

                async def reset_document(route):
                    await route.fulfill(
                        content_type="text/html",
                        body="<!doctype html><html><body></body></html>",
                    )

                await c.route("https://*.douyin.com/**", reset_document)
                await c.route("https://douyin.com/**", reset_document)
                before_reset = len(hits)
                await reset_douyin_credentials_v2(c, {"directory": profile})
                assert len(hits) == before_reset
                assert not any(
                    cookie["name"] == "sessionid" for cookie in await c.cookies()
                )
                assert any(
                    cookie["name"] == "verification-cookie"
                    for cookie in await c.cookies()
                )
                print(
                    json.dumps(
                        {
                            "status": "PASS",
                            "headed": __import__("sys").platform != "darwin",
                            "allowed_origin_requests": len(hits),
                            "forbidden_origin_requests": 0,
                            "forbidden_connects": 0,
                            "covered": [
                                "top",
                                "iframe",
                                "http302",
                                "popup-first-request",
                            ],
                        }
                    )
                )
        finally:
            server.shutdown()
            server.server_close()


def test_native_guard_blocks_first_request_and_redirect_transport():
    asyncio.run(main())
