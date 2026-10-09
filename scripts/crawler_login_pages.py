"""Persistent headed browser lifecycle helpers."""
import asyncio


async def close_rendered_pages(context):
    # A headed persistent Chromium exits when its last tab closes. Keep a blank
    # tab until context.close() flushes the profile and releases the account lock.
    old_pages = list(context.pages)
    keeper = await context.new_page()
    for page in old_pages:
        await asyncio.wait_for(page.close(), 3)
    return keeper


async def prepare_headed_login(context, profile):
    """Same credential reset as the pinned adapter, without closing the last tab."""
    (profile["directory"] / "auth-imported").touch(mode=0o600)
    state = await asyncio.wait_for(context.storage_state(), 15)
    origins = {item["origin"] for item in state.get("origins", [])}
    origins.update("https://" + host for host in (
        "www.douyin.com", "douyin.com", "creator.douyin.com", "live.douyin.com", "douhot.douyin.com"))
    origins.update("https://" + cookie["domain"].lstrip(".")
                   for cookie in state.get("cookies", []) if cookie.get("domain"))
    await close_rendered_pages(context)
    await context.clear_cookies()
    page = await context.new_page()
    try:
        await page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<html></html>"))
        for origin in sorted(origins):
            await page.goto(origin, wait_until="domcontentloaded", timeout=10000)
            await asyncio.wait_for(page.evaluate("localStorage.clear(); sessionStorage.clear()"), 5)
    finally:
        await page.close()


async def reset_douyin_credentials_v2(context, profile):
    """Explicit reauthentication only; preserve cache, seed and other origins.

    Caller must obtain the parent's durable reset authorization first. CDP
    clears first-party credential storage without Playwright route or visiting
    synthetic pages (both can disturb the profile's normal cache lifecycle).
    """
    (profile["directory"] / "auth-imported").touch(mode=0o600)
    page = await context.new_page()
    cdp = await context.new_cdp_session(page)
    try:
        state = await asyncio.wait_for(context.storage_state(), 15)
        origins = {"https://www.douyin.com", "https://douyin.com", "https://creator.douyin.com",
                   "https://live.douyin.com", "https://douhot.douyin.com"}
        from urllib.parse import urlsplit
        for item in state.get("origins", []):
            parsed = urlsplit(item.get("origin", ""))
            if parsed.scheme == "https" and (parsed.hostname == "douyin.com" or (parsed.hostname or "").endswith(".douyin.com")):
                origins.add(item["origin"])
        for origin in sorted(origins):
            await asyncio.wait_for(cdp.send("Storage.clearDataForOrigin", {
                "origin": origin, "storageTypes": "local_storage,indexeddb,service_workers,cache_storage",
            }), 5)
        import re
        await context.clear_cookies(domain=re.compile(r"(^|\.)douyin\.com$"))
        for existing in list(context.pages):
            if existing is not page:
                await asyncio.wait_for(existing.close(), 3)
    finally:
        await cdp.detach()
        # Keep the final page alive for headed persistent Chromium.
