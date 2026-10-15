"""Headed login with page-only frames and a bounded input channel."""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager, nullcontext, suppress
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from backend.audit_agent.crawler_login_interaction import (
    VIEW_WIDTH,
    VIEW_HEIGHT,
    validate_login_input,
)
from scripts import crawler_account_login as login
from scripts.crawler_login_pages import close_rendered_pages


@contextmanager
def virtual_display():
    if sys.platform != "linux" or not shutil.which("Xvfb"):
        raise RuntimeError("交互登录需要 Linux 虚拟显示组件 Xvfb")
    # Browser Unix-domain socket paths must remain shorter than 108 bytes.
    with tempfile.TemporaryDirectory(prefix="xhs-login-", dir="/tmp") as temp:
        old_display, old_tmp = os.environ.get("DISPLAY"), os.environ.get("TMPDIR")
        read_fd, write_fd = os.pipe()
        process = None
        try:
            process = subprocess.Popen(
                [
                    "Xvfb",
                    "-displayfd",
                    str(write_fd),
                    "-screen",
                    "0",
                    f"{VIEW_WIDTH}x{VIEW_HEIGHT + 100}x24",
                    "-nolisten",
                    "tcp",
                ],
                pass_fds=(write_fd,),
                stdout=sys.stderr,
                stderr=sys.stderr,
            )
            os.close(write_fd)
            write_fd = -1
            if not select.select([read_fd], [], [], 10)[0]:
                raise RuntimeError("虚拟显示器启动超时")
            display = os.read(read_fd, 32).decode().strip()
            if not display.isdigit() or process.poll() is not None:
                raise RuntimeError("虚拟显示器启动失败")
            os.environ["DISPLAY"] = ":" + display
            os.environ["TMPDIR"] = temp
            yield
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for key, value in (("DISPLAY", old_display), ("TMPDIR", old_tmp)):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def platform_page(page) -> bool:
    parsed = urlsplit(page.url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "douyin.com" or host.endswith(".douyin.com")
    )


async def current_page(context):
    for page in reversed(context.pages):
        if not page.is_closed() and platform_page(page):
            return page
    return None


async def frames(context):
    while True:
        page = await current_page(context)
        if page:
            try:
                jpeg = await page.screenshot(type="jpeg", quality=80, timeout=2000)
                login.emit("frame", jpeg=base64.b64encode(jpeg).decode("ascii"))
            except Exception:
                pass  # Navigation must not terminate the login session.
        await asyncio.sleep(0.55)


async def inputs(context):
    reader = asyncio.StreamReader(limit=8192)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader),
        sys.stdin,
    )
    try:
        while line := await reader.readline():
            try:
                command = validate_login_input(json.loads(line))
                page = await current_page(context)
                if command["type"] == "dismiss_browser_prompt":
                    from scripts.crawler_login_native import dismiss_browser_prompt

                    await asyncio.to_thread(dismiss_browser_prompt)
                elif page:
                    await asyncio.wait_for(page.bring_to_front(), 3)
                    await asyncio.wait_for(apply_input(page, command), 3)
            except (ValueError, TypeError, asyncio.TimeoutError):
                continue
            except Exception:
                continue  # A page may be replaced while an input is in flight.
    finally:
        transport.close()


async def apply_input(page, command):
    kind = command["type"]
    if kind == "text":
        await page.keyboard.insert_text(command["text"])
    elif kind == "key":
        await page.keyboard.press(command["key"])
    elif kind == "scroll":
        await page.mouse.wheel(0, command["delta"])
    else:
        await page.mouse.move(command["x"], command["y"])
        if kind == "click":
            await page.mouse.click(command["x"], command["y"])
        elif kind == "pointer_down":
            await page.mouse.down()
        elif kind == "pointer_up":
            await page.mouse.up()


async def has_auth(context):
    cookies = {
        c["name"]: c["value"]
        for c in await asyncio.wait_for(context.cookies("https://www.douyin.com"), 5)
    }
    # Look for actual session credentials first; a renderer read can stall on redirects.
    return bool(
        cookies.get("sessionid")
        or cookies.get("sessionid_ss")
        or cookies.get("LOGIN_STATUS") == "1"
    )


async def run_interactive_login(
    account_id: str, timeout: int, expected_account_id: str = ""
):
    with virtual_display() if sys.platform == "linux" else nullcontext():
        try:
            state, account_identity = await asyncio.wait_for(
                interactive_flow(account_id, expected_account_id),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            login.emit("expired", message="登录会话已超时，请重新打开登录窗口")
            return
    login.emit("success", auth_state=state, platform_account_id=account_identity)


async def interactive_flow(account_id, expected_account_id):
    tasks = []
    identity = ""
    async with login.login_context("dy", account_id, headless=False) as context:
        from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter

        page = await context.new_page()
        adapter = await DouyinLoginAdapter(context, page).install()
        await page.set_viewport_size({"width": VIEW_WIDTH, "height": VIEW_HEIGHT})
        page.set_default_timeout(3000)
        context.on("dialog", lambda dialog: dialog.dismiss())
        tasks = [
            asyncio.create_task(frames(context)),
            asyncio.create_task(inputs(context)),
        ]
        try:
            login.emit("interactive")
            gate = (
                getattr(context, "_bound_proxy_session", None)
                or login.load_request_scheduler().configured_gate()
            )
            async with gate.slot("login_navigation", timeout=60):
                try:
                    await page.goto(
                        "https://www.douyin.com",
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                except login.PlaywrightTimeoutError:
                    pass
            # Open the form once; QR detection is not a prerequisite for further verification.
            for selector in login.PLATFORMS["dy"]["login_triggers"]:
                try:
                    await page.locator(selector).first.click(timeout=3000)
                    break
                except Exception:
                    pass
            while True:
                await login.report_douyin_challenge(page, context)
                try:
                    authenticated = await has_auth(context)
                except Exception:
                    authenticated = False
                if authenticated:
                    login.emit("finalizing", duration_seconds=3)
                    await asyncio.sleep(3)
                    if not await has_auth(context):
                        login.emit("interactive")
                        continue
                    try:
                        identity = await asyncio.wait_for(
                            login.identify_logged_in_account("dy", context), 4
                        )
                    except asyncio.TimeoutError:
                        identity = ""
                    if (
                        expected_account_id
                        and identity
                        and identity != expected_account_id
                    ):
                        from scripts.crawler_login_pages import (
                            reset_douyin_credentials_v2,
                        )
                        from backend.audit_agent.crawler_browser import (
                            account_profile_directory,
                        )

                        await adapter.close()
                        await reset_douyin_credentials_v2(
                            context,
                            {"directory": account_profile_directory(account_id)},
                        )
                        raise RuntimeError(
                            "登录的抖音账号与所选账号不一致，请使用原账号重新登录"
                        )
                    break
                await asyncio.sleep(0.8)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.close()
            state = await asyncio.wait_for(context.storage_state(), 15)
            # Close the active renderer before the persistent context flushes its Profile.
            with suppress(Exception):
                await close_rendered_pages(context)
    return state, identity
