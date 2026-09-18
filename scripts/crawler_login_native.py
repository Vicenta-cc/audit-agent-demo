"""Cancel a native browser prompt on this login session's private X display."""
import ctypes
import os


def dismiss_browser_prompt():
    # Page.keyboard cannot dismiss Chromium's external-application dialog.
    # Expose only Escape, never arbitrary native keys or desktop coordinates.
    display_name = os.environ.get("DISPLAY", "")
    if not display_name.startswith(":") or not display_name[1:].isdigit():
        raise RuntimeError("登录窗口的虚拟显示器不可用")
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(display_name.encode())
    if not display:
        raise RuntimeError("无法连接登录窗口")
    try:
        key = x11.XKeysymToKeycode(display, x11.XStringToKeysym(b"Escape"))
        if not key:
            raise RuntimeError("登录窗口无法取消提示")
        xtst.XTestFakeKeyEvent(display, key, 1, 0)
        xtst.XTestFakeKeyEvent(display, key, 0, 0)
        x11.XSync(display, 0)
    finally:
        x11.XCloseDisplay(display)
