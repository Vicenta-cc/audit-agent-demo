"""Firefox persistent-context lifecycle and explicit credential reset."""

import asyncio
import re
from urllib.parse import urlsplit


async def close_rendered_pages(context):
    """Retain one blank tab until context.close flushes the persistent Profile."""
    old_pages = list(context.pages)
    keeper = await context.new_page()
    for page in old_pages:
        await asyncio.wait_for(page.close(), 3)
    return keeper


def _douyin_origin(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "douyin.com" or host.endswith(".douyin.com")
    )


CLEAR_ORIGIN_STORAGE = """async () => {
    localStorage.clear();
    sessionStorage.clear();
    if ('serviceWorker' in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.map(registration => registration.unregister()));
    }
    if ('caches' in window) {
        await Promise.all((await caches.keys()).map(name => caches.delete(name)));
    }
    if (typeof indexedDB.databases !== 'function') throw new Error('IDB_ENUMERATION_UNAVAILABLE');
    await Promise.all((await indexedDB.databases()).map(database => new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase(database.name);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(new Error('IDB_RESET_FAILED'));
        request.onblocked = () => reject(new Error('IDB_RESET_BLOCKED'));
    })));
}"""


async def reset_douyin_credentials_v2(context, profile):
    """Clear origin credentials through Firefox APIs after owner authorization.

    Blank synthetic documents prevent executing platform JavaScript during reset.
    Existing renderers are closed first so IndexedDB handles cannot block deletion.
    Any unsupported/failed storage operation propagates and blocks reauthentication.
    """
    # Cookie enumeration does not create hidden pages or traverse third-party
    # storage, unlike Playwright's all-origin storage_state collector.
    cookies = await asyncio.wait_for(context.cookies(), 15)
    origins = {
        "https://" + host
        for host in (
            "www.douyin.com",
            "douyin.com",
            "creator.douyin.com",
            "live.douyin.com",
            "douhot.douyin.com",
        )
    }
    origins.update(
        "https://" + cookie["domain"].lstrip(".")
        for cookie in cookies
        if _douyin_origin("https://" + cookie.get("domain", "").lstrip("."))
    )
    # Include live documents and IDB-only origins omitted from localStorage
    # state. Read only Firefox origin directory names; never touch DB payloads.
    for existing in list(context.pages):
        for frame in [existing, *getattr(existing, "frames", [])]:
            value = getattr(frame, "url", "")
            if _douyin_origin(value):
                parsed = urlsplit(value)
                origins.add(parsed.scheme + "://" + parsed.netloc)
    storage = profile["directory"] / "storage"
    if storage.is_symlink():
        raise RuntimeError("PROFILE_STORAGE_SYMLINK")
    for bucket in ("default", "permanent", "temporary"):
        directory = storage / bucket
        if directory.is_symlink():
            raise RuntimeError("PROFILE_STORAGE_SYMLINK")
        if not directory.exists():
            continue
        for entry in directory.iterdir():
            if entry.is_symlink():
                raise RuntimeError("PROFILE_STORAGE_SYMLINK")
            base = entry.name.split("^", 1)[0]
            match = re.fullmatch(r"https\+\+\+([a-zA-Z0-9.-]+)(?:\+(\d+))?", base)
            if match:
                origin = "https://" + match[1] + ((":" + match[2]) if match[2] else "")
                if _douyin_origin(origin):
                    origins.add(origin)
    keeper = await close_rendered_pages(context)
    page = await context.new_page()

    async def reset_document(route):
        if route.request.is_navigation_request() and _douyin_origin(route.request.url):
            await route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html><html></html>",
            )
        else:
            await route.abort()

    try:
        await page.route("**/*", reset_document)
        for origin in sorted(origins):
            await page.goto(origin, wait_until="domcontentloaded", timeout=10000)
            await asyncio.wait_for(page.evaluate(CLEAR_ORIGIN_STORAGE), 10)
        await context.clear_cookies(domain=re.compile(r"(^|\.)douyin\.com$"))
        (profile["directory"] / "auth-imported").touch(mode=0o600)
    finally:
        await page.close()
    return keeper


async def prepare_headed_login(context, profile):
    return await reset_douyin_credentials_v2(context, profile)


async def export_douyin_auth_state(context):
    """Back up only first-party credentials without storage_state navigation.

    The persistent Firefox Profile remains authoritative. Read already loaded
    first-party frames only: third-party verification/echo origins are neither
    evaluated nor promoted to main-frame navigations during credential export.
    """
    cookies = [
        cookie
        for cookie in await context.cookies()
        if isinstance(cookie.get("domain"), str)
        and re.fullmatch(r"\.?[A-Za-z0-9.-]+", cookie["domain"])
        and (
            cookie["domain"].lstrip(".").lower() == "douyin.com"
            or cookie["domain"].lstrip(".").lower().endswith(".douyin.com")
        )
    ]
    origins = {}
    for page in list(context.pages):
        frames = getattr(page, "frames", None)
        for frame in frames if frames is not None else [page]:
            url = frame.url
            if not _douyin_origin(url):
                continue
            parsed = urlsplit(url)
            expected_origin = parsed.scheme + "://" + parsed.netloc
            state = await frame.evaluate("""() => ({origin:location.origin,
                localStorage:Object.keys(localStorage).sort().map(name=>({name,value:localStorage.getItem(name)}))})""")
            if (
                not isinstance(state, dict)
                or set(state) != {"origin", "localStorage"}
                or state["origin"] != expected_origin
                or not _douyin_origin(state["origin"])
                or not isinstance(state["localStorage"], list)
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"name", "value"}
                    or not isinstance(item["name"], str)
                    or not isinstance(item["value"], str)
                    for item in state["localStorage"]
                )
            ):
                raise ValueError("AUTH_EXPORT_ORIGIN_CHANGED")
            if expected_origin in origins and origins[expected_origin] != state:
                raise ValueError("AUTH_EXPORT_ORIGIN_CONFLICT")
            origins[expected_origin] = state
    if not origins:
        raise ValueError("AUTH_EXPORT_FIRST_PARTY_CONTEXT_MISSING")
    return {"cookies": cookies, "origins": [origins[key] for key in sorted(origins)]}
