"""Actual pinned Firefox native caret layout; no platform network or accounts."""

import asyncio
import base64
import json
from pathlib import Path
import os
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CAMOUFOX_LOGIN_TESTS") != "1", reason="requires pinned Camoufox"
)


def test_native_editor_caret_geometry_and_projection(tmp_path, monkeypatch):
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_login_editables import EditableTargets

    monkeypatch.setenv(
        "MEDIACRAWLER_CAMOUFOX_PROFILE_GENERATION", "caret-geometry-fixture"
    )

    async def run():
        browser = load_account_browser()
        context, _ = await browser.launch_account_context(
            "caret-geometry", tmp_path, headless=True
        )
        try:
            page = await context.new_page()
            await page.set_viewport_size({"width": 900, "height": 650})
            await page.route("**/*", lambda route: route.abort())
            await page.set_content("""<style>body{margin:0} input,textarea {font:24px Arial; padding:8px; width:220px; height:40px; margin:10px; caret-color:rgb(210,0,80)} textarea{height:90px}</style>
            <input id="text" value="abcdef"><input id="tel" type="tel" value="123456"><textarea id="area">first line\nsecond line\nthird line</textarea>""")
            inventory = EditableTargets()
            region = SimpleNamespace(x=0, y=0, width=900, height=650)
            for selector, offset in [
                ("#text", 0),
                ("#text", 2),
                ("#text", 6),
                ("#tel", 3),
                ("#area", 13),
            ]:
                field = page.locator(selector)
                await field.focus()
                await field.evaluate("(e,n)=>e.setSelectionRange(n,n)", offset)
                before = await field.evaluate(
                    "e=>[e.value,e.selectionStart,e.selectionEnd,e.scrollLeft,e.scrollTop,document.activeElement===e]"
                )
                fields = await inventory.inventory(page, region)
                focused = next(item for item in fields if item["focused"])
                caret = focused["caret"]
                assert caret is not None
                assert caret["rect"]["width"] == 0
                assert caret["rect"]["height"] > 0
                assert caret["color"] == "rgb(210, 0, 80)"
                actual = await field.evaluate(
                    """e=>{const r=e.getBoundingClientRect();for(let y=r.y+.5;y<r.bottom;y+=2)for(let x=r.x+.5;x<r.right;x+=1){const p=document.caretPositionFromPoint(x,y);if(p?.offsetNode===e&&p.offset===e.selectionStart){const c=p.getClientRect();return {x:c.x,y:c.y,height:c.height}}}return null}"""
                )
                assert actual is not None
                assert abs(caret["rect"]["x"] * 900 - actual["x"]) < 0.1
                assert abs(caret["rect"]["y"] * 650 - actual["y"]) < 0.1
                assert before == await field.evaluate(
                    "e=>[e.value,e.selectionStart,e.selectionEnd,e.scrollLeft,e.scrollTop,document.activeElement===e]"
                )
            # Empty INPUT native rect.y is its real baseline; browser font bounds
            # must reconstruct the exact same caret as native offset zero with M.
            for font in ["24px Arial", "18px Times New Roman", "32px monospace"]:
                for kind in ["text", "tel"]:
                    field = page.locator("#" + kind)
                    await field.evaluate(
                        "(e,font)=>{e.style.font=font;e.style.height='60px'}", font
                    )
                    await field.fill("")
                    empty = next(
                        item
                        for item in await inventory.inventory(page, region)
                        if item["focused"]
                    )["caret"]
                    assert empty is not None
                    await field.fill("M")
                    await field.evaluate("e=>e.setSelectionRange(0,0)")
                    filled = next(
                        item
                        for item in await inventory.inventory(page, region)
                        if item["focused"]
                    )["caret"]
                    assert filled is not None
                    for axis in ["x", "y", "height"]:
                        assert abs(empty["rect"][axis] - filled["rect"][axis]) < 0.0001
            # Horizontal editor scrolling and alignment use the native geometry.
            field = page.locator("#text")
            await field.fill("0123456789" * 8)
            await field.evaluate(
                "e=>{e.style.textAlign='right';e.setSelectionRange(75,75);e.scrollLeft=e.scrollWidth}"
            )
            assert next(
                item
                for item in await inventory.inventory(page, region)
                if item["focused"]
            )["caret"]
            await field.evaluate("e=>e.setSelectionRange(0,2)")
            assert (
                next(
                    item
                    for item in await inventory.inventory(page, region)
                    if item["focused"]
                )["caret"]
                is None
            )
            await field.evaluate(
                "e=>{e.setSelectionRange(75,75);e.style.transform='rotate(2deg)'}"
            )
            assert (
                next(
                    item
                    for item in await inventory.inventory(page, region)
                    if item["focused"]
                )["caret"]
                is None
            )
            await field.evaluate(
                "e=>{e.style.transform='';e.dir='rtl';e.value='אבגדהוז';e.setSelectionRange(3,3);e.scrollLeft=0}"
            )
            assert next(
                item
                for item in await inventory.inventory(page, region)
                if item["focused"]
            )["caret"]
            area = page.locator("#area")
            await area.fill("line\n" * 29 + "last")
            await area.press("ControlOrMeta+End")
            assert await area.evaluate("e=>e.scrollTop") > 0
            assert next(
                item
                for item in await inventory.inventory(page, region)
                if item["focused"]
            )["caret"]
            # Native local-frame coordinates map through a scrolled, scaled iframe.
            await page.unroute("**/*")
            await page.route(
                "**/*",
                lambda route: route.fulfill(
                    body='<input style="margin:30px;font:24px Arial;caret-color:red" value="abcdef">',
                    content_type="text/html",
                ),
            )
            await page.set_content(
                '<body style="height:1400px;margin:0"><iframe style="margin:140px 0 0 60px;border:8px solid black;width:380px;height:180px;transform:scale(.8);transform-origin:top left" src="https://www.douyin.com/caret-frame"></iframe></body>'
            )
            frame = page.frames[-1]
            remote = frame.locator("input")
            await remote.focus()
            await remote.evaluate("e=>e.setSelectionRange(2,2)")
            await page.evaluate("scrollTo(0,80)")
            selected = next(
                item
                for item in await inventory.inventory(page, region)
                if item["focused"]
            )
            assert selected["caret"] is not None
            native = await remote.evaluate(
                """e=>{const r=e.getBoundingClientRect();for(let x=r.x+.5;x<r.right;x+=.5){const p=document.caretPositionFromPoint(x,r.y+r.height/2);if(p?.offsetNode===e&&p.offset===2){const c=p.getClientRect();return {fraction:(c.x-r.x)/r.width,height:c.height/r.height}}}}"""
            )
            global_box = await remote.bounding_box()
            assert (
                abs(
                    selected["caret"]["rect"]["x"] * 900
                    - (global_box["x"] + native["fraction"] * global_box["width"])
                )
                < 0.1
            )
            assert (
                abs(
                    selected["caret"]["rect"]["height"] * 650
                    - native["height"] * global_box["height"]
                )
                < 0.1
            )
            # Optional evidence export is an isolated empty-field fixture only.
            evidence = os.getenv("CAMOUFOX_CARET_FIXTURE_JSON")
            if evidence:
                await page.set_viewport_size({"width": 320, "height": 180})
                await page.set_content(
                    '<body style="margin:0;background:white"><b style="font:12px Arial">FIXTURE · empty SMS field</b><input aria-label="fixture SMS" placeholder="Code" style="position:absolute;left:32px;top:22px;box-sizing:border-box;width:230px;height:28px;font:16px Arial;padding:4px;border:1px solid #c8cdd4;border-radius:4px;background:#f5f6f8;outline:none;box-shadow:none;caret-color:#111"></body>'
                )
                await page.locator("input").focus()
                fields = await inventory.inventory(
                    page, SimpleNamespace(x=0, y=0, width=320, height=180)
                )
                assert (
                    len(fields) == 1 and fields[0]["value"] == "" and fields[0]["caret"]
                )
                png = await page.screenshot(type="png", caret="initial")
                Path(evidence).write_text(
                    json.dumps(
                        {"field": fields[0], "png": base64.b64encode(png).decode()}
                    )
                )
            await inventory.clear()
        finally:
            await context.close()

    asyncio.run(run())
