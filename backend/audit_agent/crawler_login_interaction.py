"""Bounded page interaction protocol (no browser shortcuts, URLs or script execution)."""
import math

VIEW_WIDTH = 1000
VIEW_HEIGHT = 760
KEYS = {"Enter", "Backspace", "Delete", "Tab", "Shift+Tab", "Escape", "ArrowLeft",
        "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Control+a"}


def validate_login_input(event: dict) -> dict:
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("无效的登录操作")
    kind = event.get("type")
    if kind in {"click", "pointer_down", "pointer_up", "pointer_move"}:
        values = (event.get("x"), event.get("y"))
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            raise ValueError("无效的点击位置")
        x, y = values
        if not (0 <= x < VIEW_WIDTH and 0 <= y < VIEW_HEIGHT):
            raise ValueError("点击位置超出登录窗口")
        return {"type": kind, "x": x, "y": y}
    if kind == "text" and isinstance(event.get("text"), str) and 0 < len(event["text"]) <= 256:
        if any(ord(c) < 32 for c in event["text"]):
            raise ValueError("输入包含不支持的控制字符")
        return {"type": kind, "text": event["text"]}
    if kind == "key" and isinstance(event.get("key"), str) and event["key"] in KEYS:
        return {"type": kind, "key": event["key"]}
    if kind == "scroll" and type(event.get("delta")) in (float, int):
        delta = event["delta"]
        if math.isfinite(delta) and abs(delta) <= 760:
            return {"type": kind, "delta": delta}
    raise ValueError("不支持的登录操作")
