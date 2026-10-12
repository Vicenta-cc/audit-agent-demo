"""Transient editable inventory for the current cropped login region."""

from __future__ import annotations

import uuid
from contextlib import suppress

from scripts.crawler_douyin_login_adapter import document_allowed

INPUT_TYPES = {"text", "tel", "email", "url", "search", "number"}


class EditNotExecuted(ValueError):
    pass


class EditableTargets:
    def __init__(self):
        self.nodes = {}
        self.revisions = {}
        self.frames = {}

    async def clear(self):
        for handle in self.nodes.values():
            try:
                await handle.dispose()
            except Exception:
                pass
        self.nodes.clear()
        self.revisions.clear()
        self.frames.clear()

    async def inventory(self, page, region):
        found = []
        seen = set()
        for frame in page.frames:
            if frame is not page.main_frame and not document_allowed(
                frame.url, top_level=False
            ):
                continue
            handles = await frame.locator("input, textarea").element_handles()
            try:
                for handle in handles:
                    try:
                        state = await handle.evaluate(
                            """e=>{let root=e.getRootNode();return {connected:e.isConnected, disabled:e.disabled,readOnly:e.readOnly,type:e.tagName==='TEXTAREA'?'textarea':e.type,inputMode:e.inputMode,maxLength:e.maxLength,value:e.value,selectionStart:e.selectionStart,selectionEnd:e.selectionEnd,selectionDirection:e.selectionDirection,focused:(()=>{let n=e;while(n){let r=n.getRootNode();if(r.activeElement!==n)return false;if(!r.host)return true;n=r.host}return false})()}}"""
                        )
                        if state["focused"]:
                            ancestor = frame
                            while ancestor.parent_frame is not None:
                                element = await ancestor.frame_element()
                                focused = await element.evaluate(
                                    "e=>{let n=e;while(n){let r=n.getRootNode();if(r.activeElement!==n)return false;if(!r.host)return true;n=r.host}return false}"
                                )
                                await element.dispose()
                                if not focused:
                                    state["focused"] = False
                                    break
                                ancestor = ancestor.parent_frame
                        box = await handle.bounding_box()
                        if (
                            not state["connected"]
                            or state["disabled"]
                            or state["readOnly"]
                            or not box
                            or state["type"] not in INPUT_TYPES | {"textarea"}
                            or len(state["value"].encode("utf-16-le")) // 2 > 256
                            or not self.inside(box, region)
                            or not await handle.is_enabled()
                        ):
                            await handle.dispose()
                            continue
                        if not await self.exposed(handle, frame, box):
                            await handle.dispose()
                            continue
                        field_id = None
                        for existing_id, old in self.nodes.items():
                            if self.frames.get(existing_id) is not frame:
                                continue
                            if await handle.evaluate("(e,old)=>e===old", old):
                                field_id = existing_id
                                break
                        if field_id is None:
                            field_id = uuid.uuid4().hex
                            self.nodes[field_id] = handle
                            self.frames[field_id] = frame
                        else:
                            await handle.dispose()
                        seen.add(field_id)
                        maximum = state["maxLength"]
                        found.append(
                            {
                                "field_id": field_id,
                                "rect": {
                                    "x": (box["x"] - region.x) / region.width,
                                    "y": (box["y"] - region.y) / region.height,
                                    "width": box["width"] / region.width,
                                    "height": box["height"] / region.height,
                                },
                                "input_type": state["type"],
                                "input_mode": state["inputMode"],
                                "max_length": min(256, maximum)
                                if maximum >= 0
                                else 256,
                                "value": state["value"],
                                "selection_start": state["selectionStart"],
                                "selection_end": state["selectionEnd"],
                                "selection_direction": state["selectionDirection"],
                                "focused": state["focused"],
                                "applied_edit_revision": self.revisions.get(
                                    field_id, 0
                                ),
                            }
                        )
                        if len(found) == 4:
                            break
                    except Exception as exc:
                        text = str(exc)
                        if not any(
                            marker in text
                            for marker in (
                                "Element is not attached",
                                "Frame was detached",
                                "Execution context was destroyed",
                                "Cannot find context",
                            )
                        ):
                            raise
            finally:
                for candidate in handles:
                    if not any(candidate is owned for owned in self.nodes.values()):
                        with suppress(Exception):
                            await candidate.dispose()
            if len(found) == 4:
                break
        for field_id in list(self.nodes):
            if field_id not in seen:
                await self.nodes.pop(field_id).dispose()
                self.revisions.pop(field_id, None)
                self.frames.pop(field_id, None)
        return found

    @staticmethod
    def inside(box, region):
        return (
            box["width"] > 0
            and box["height"] > 0
            and box["x"] >= region.x
            and box["y"] >= region.y
            and box["x"] + box["width"] <= region.x + region.width
            and box["y"] + box["height"] <= region.y + region.height
        )

    async def exposed(self, handle, frame, box):
        if not await handle.is_visible():
            return False
        visible = await handle.evaluate(
            """e=>{for(let n=e;n;n=n.parentElement||(n.getRootNode().host||null)){const s=getComputedStyle(n);if(s.display==='none'||s.visibility!=='visible'||Number(s.opacity)===0)return false}let r=e.getBoundingClientRect();let hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);while(hit&&hit.shadowRoot){let deeper=hit.shadowRoot.elementFromPoint(r.x+r.width/2,r.y+r.height/2);if(!deeper||deeper===hit)break;hit=deeper}return hit===e}"""
        )
        if not visible:
            return False
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        child = frame
        while child.parent_frame is not None:
            parent = child.parent_frame
            origin_x = origin_y = 0
            if parent.parent_frame is not None:
                parent_element = await parent.frame_element()
                try:
                    parent_box = await parent_element.bounding_box()
                    border = await parent_element.evaluate(
                        "e=>({x:e.clientLeft,y:e.clientTop})"
                    )
                    if not parent_box:
                        return False
                    origin_x, origin_y = (
                        parent_box["x"] + border["x"],
                        parent_box["y"] + border["y"],
                    )
                finally:
                    await parent_element.dispose()
            element = await child.frame_element()
            try:
                hit = await element.evaluate(
                    """(e,p)=>{for(let n=e;n;n=n.parentElement||(n.getRootNode().host||null)){const s=getComputedStyle(n);if(s.display==='none'||s.visibility!=='visible'||Number(s.opacity)===0)return false}let hit=document.elementFromPoint(p.x,p.y);while(hit&&hit.shadowRoot){let deeper=hit.shadowRoot.elementFromPoint(p.x,p.y);if(!deeper||deeper===hit)break;hit=deeper}return hit===e}""",
                    {"x": x - origin_x, "y": y - origin_y},
                )
                if not hit:
                    return False
            finally:
                await element.dispose()
            child = parent
        return True

    async def edit(self, command, page, region):
        fields = await self.inventory(page, region)
        field = next(
            (item for item in fields if item["field_id"] == command["field_id"]), None
        )
        if not field or not field["focused"]:
            raise EditNotExecuted("EDIT_TARGET_CHANGED")
        revision = command["client_edit_revision"]
        if revision <= self.revisions.get(command["field_id"], 0):
            raise EditNotExecuted("EDIT_REVISION_STALE")
        handle = self.nodes[command["field_id"]]
        if "\n" in command["value"] and field["input_type"] != "textarea":
            raise EditNotExecuted("EDIT_VALUE_INVALID")
        if len(command["value"].encode("utf-16-le")) // 2 > field["max_length"]:
            raise EditNotExecuted("EDIT_VALUE_TOO_LONG")
        await handle.fill(command["value"], timeout=1500)
        state = await handle.evaluate(
            """(e,s)=>{const focused=(()=>{let n=e;while(n){let r=n.getRootNode();if(r.activeElement!==n)return false;if(!r.host)return true;n=r.host}return false})();if(focused&&typeof e.setSelectionRange==='function'&&!['number','email'].includes(e.type))e.setSelectionRange(s.start,s.end,s.direction);return {connected:e.isConnected,focused,value:e.value}}""",
            {
                "start": command["selection_start"],
                "end": command["selection_end"],
                "direction": command.get("selection_direction", "none"),
            },
        )
        if (
            not state["connected"]
            or not state["focused"]
            or state["value"] != command["value"]
        ):
            raise ValueError("EDIT_TARGET_CHANGED")
        refreshed = await self.inventory(page, region)
        if not any(
            item["field_id"] == command["field_id"] and item["focused"]
            for item in refreshed
        ):
            raise ValueError("EDIT_TARGET_CHANGED")
        self.revisions[command["field_id"]] = revision
        return {"field_id": command["field_id"], "client_edit_revision": revision}
