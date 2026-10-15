"""Transient editable inventory for the current cropped login region."""

from __future__ import annotations

import uuid
from contextlib import suppress

from scripts.crawler_douyin_login_adapter import document_allowed

INPUT_TYPES = {"text", "tel", "email", "url", "search", "number"}

# Firefox exposes its actual native editor caret through hit testing, including
# input/textarea anonymous editor content retargeted to the original element.
# No copied text layout, focus/selection mutation, or text payload is involved.
NATIVE_CARET = r"""e => {
  const start = e.selectionStart, end = e.selectionEnd;
  if (start === null || start !== end || !document.caretPositionFromPoint) return null;
  const cleanTransform = node => {
    for (let n = node; n; n = n.parentElement || n.getRootNode().host) {
      const s = getComputedStyle(n);
      if (s.perspective !== 'none') return false;
      if (s.transform !== 'none') {
        const m = new DOMMatrixReadOnly(s.transform);
        if (!m.is2D || m.a <= 0 || m.d <= 0 || Math.abs(m.b) > 1e-8 || Math.abs(m.c) > 1e-8) return false;
      }
    }
    return true;
  };
  if (!cleanTransform(e)) return null;
  const r = e.getBoundingClientRect(), style = getComputedStyle(e);
  if (style.writingMode !== 'horizontal-tb') return null;
  const color = style.caretColor === 'auto' ? style.color : style.caretColor;
  if (!color || color === 'transparent' || /rgba\([^)]*,\s*0\s*\)/.test(color)) return null;
  const scrollLeft = e.scrollLeft, scrollTop = e.scrollTop;
  const sx = r.width / e.offsetWidth, sy = r.height / e.offsetHeight;
  const contentLeft = r.left + e.clientLeft * sx;
  const contentTop = r.top + e.clientTop * sy;
  const left = Math.max(0, contentLeft), top = Math.max(0, contentTop);
  const right = Math.min(innerWidth, contentLeft + e.clientWidth * sx);
  const bottom = Math.min(innerHeight, contentTop + e.clientHeight * sy);
  if (right <= left || bottom <= top) return null;
  const ys = [(top + bottom) / 2];
  const stepY = Math.max(2, parseFloat(style.fontSize) / 2);
  for (let y = top + .5; y < bottom; y += stepY) ys.push(y);
  let attempts = 0;
  const probe = (x, y) => {
    if (++attempts > 4096) return null;
    const p = document.caretPositionFromPoint(x, y);
    if (!p || p.offsetNode !== e || p.offset !== start || typeof p.getClientRect !== 'function') return null;
    const c = p.getClientRect();
    if (!c || ![c.x,c.y,c.width,c.height].every(Number.isFinite) || c.height < 0 || c.width < 0 || c.x < left || c.x > right) return null;
    let caretTop = c.y, caretHeight = c.height;
    if (caretHeight === 0 && e.tagName === 'INPUT' && e.value.length === 0) {
      // Gecko returns the real baseline for an empty single-line editor.
      // Use this browser's font bounds, never the input box centre/font size.
      const ctx = document.createElement('canvas').getContext('2d');
      if (!ctx || !style.font) return null;
      ctx.font = style.font;
      const metrics = ctx.measureText('M');
      const a = metrics.fontBoundingBoxAscent, d = metrics.fontBoundingBoxDescent;
      if (![a,d].every(Number.isFinite) || a < 0 || d < 0 || a+d <= 0) return null;
      caretTop = c.y - a * sy;
      caretHeight = (a+d) * sy;
    }
    if (caretHeight <= 0) return null;
    const y1 = Math.max(caretTop, top), y2 = Math.min(caretTop+caretHeight, bottom);
    if (y2 <= y1 || e.selectionStart !== start || e.selectionEnd !== end || e.scrollLeft !== scrollLeft || e.scrollTop !== scrollTop) return null;
    return {x:(c.x-r.x)/r.width, y:(y1-r.y)/r.height, width:c.width/r.width, height:(y2-y1)/r.height, color};
  };
  for (const y of ys) {
    // Native offset binary search is fast for ordinary LTR/RTL single lines.
    for (const direction of [1,-1]) {
      let lo = left + .25, hi = right - .25;
      for (let i=0;i<12 && hi>=lo;i++) {
        const x=(lo+hi)/2, result=probe(x,y);
        if(result)return result;
        const p=document.caretPositionFromPoint(x,y);
        if(!p || p.offsetNode!==e)break;
        if((p.offset<start)===(direction===1))lo=x+.1;else hi=x-.1;
      }
    }
    // Bidi/wrapped text is not monotonic: accept only an exact native hit.
    for(let x=left+.25;x<right && attempts<4096;x+=1) {
      const result=probe(x,y);if(result)return result;
    }
    if(attempts>=4096)break;
  }
  return null;
}"""

AXIS_ALIGNED_FRAME = r"""e => {
  for(let n=e;n;n=n.parentElement || n.getRootNode().host) {
    const s=getComputedStyle(n);
    if(s.perspective!=='none')return false;
    if(s.transform!=='none') {
      const m=new DOMMatrixReadOnly(s.transform);
      if(!m.is2D || m.a<=0 || m.d<=0 || Math.abs(m.b)>1e-8 || Math.abs(m.c)>1e-8)return false;
    }
  }
  return true;
}"""


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
                        caret = None
                        if state["focused"]:
                            caret = await self.caret(
                                self.nodes[field_id], frame, box, region
                            )
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
                                "caret": caret,
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

    async def caret(self, handle, frame, box, region):
        child = frame
        while child.parent_frame is not None:
            element = await child.frame_element()
            try:
                if not await element.evaluate(AXIS_ALIGNED_FRAME):
                    return None
            finally:
                await element.dispose()
            child = child.parent_frame
        geometry = await handle.evaluate(NATIVE_CARET)
        if geometry is None:
            return None
        rect = {
            "x": (box["x"] + geometry["x"] * box["width"] - region.x) / region.width,
            "y": (box["y"] + geometry["y"] * box["height"] - region.y) / region.height,
            "width": geometry["width"] * box["width"] / region.width,
            "height": geometry["height"] * box["height"] / region.height,
        }
        if (
            rect["x"] < 0
            or rect["y"] < 0
            or rect["x"] + rect["width"] > 1
            or rect["y"] + rect["height"] > 1
        ):
            return None
        return {"rect": rect, "color": geometry["color"]}

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
