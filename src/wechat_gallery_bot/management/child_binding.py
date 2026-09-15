"""User-launched child-only window binding. Never launches WeChat or sends input.

The controller record prevents accidental host use, not a same-user security boundary.
WTSGetChildSessionId is intentionally not used to identify oneself from inside a child.
"""
import ctypes
import os
import time
import math
from pathlib import Path

from .common import ManagementError
from .window_binding import NativeProvider, BindingService


def process_session(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    value = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(pid, ctypes.byref(value)):
        raise ManagementError("无法核对进程所属桌面。")
    return value.value


def validate_context(record, current, console, now=None):
    try:
        child = int(record["owned_session"])
        parent = int(record["parent_session"])
        age = (time.time() if now is None else now) - float(record["published_unix"])
        if (record["phase"] != "connected" or current != child or current == parent
                or current == console or parent != console or child <= 0
                or not math.isfinite(age) or age < 0 or age > 8):
            raise ValueError()
        return child
    except (KeyError, ValueError, TypeError):
        raise ManagementError("请在已连接的分身内打开此入口；主机或未知会话禁止连接微信。") from None


def require_child_context(*, input_enabled=False):
    stage = "读取连接记录"
    try:
        path = Path(os.environ["LOCALAPPDATA"]) / "TianyiBotSessionTrial/recovery.txt"
        if path.stat().st_size > 4096:
            raise ValueError()
        pairs = [line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines()]
        if any(len(pair) != 2 for pair in pairs) or len(dict(pairs)) != len(pairs):
            raise ValueError()
        record = dict(pairs)
        if input_enabled and record.get("input_isolation") != "verified-v1":
            raise ManagementError("控制器尚未发布输入隔离检查结果，请正常结束分身后使用新版控制器。")
        stage = "检查主机发布状态（需要0.6.3控制器）"
        if "published_unix" not in record:
            raise ValueError()
        stage = "核对当前桌面归属"
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WTSGetActiveConsoleSessionId.restype = ctypes.c_uint32
        return validate_context(record, process_session(os.getpid()),
                                kernel.WTSGetActiveConsoleSessionId())
    except Exception as exc:
        code = getattr(exc, "winerror", None)
        suffix = f"，系统错误码 {code}" if isinstance(code, int) else ""
        raise ManagementError(f"连接检查未通过：{stage}（{type(exc).__name__}{suffix}）。未连接微信；不代表控制器已经退出。") from None


class ChildProvider(NativeProvider):
    child = True


def main():
    import tkinter as tk
    from tkinter import messagebox
    from .local_gui import LocalBindingWindow
    root = tk.Tk()
    root.withdraw()
    try:
        require_child_context()
    except ManagementError as exc:
        messagebox.showerror("分身微信管理", str(exc), parent=root)
        root.destroy()
        return
    root.deiconify()
    window = LocalBindingWindow(root, BindingService(ChildProvider()), child_mode=True)
    from .child_control import attach_control
    try:
        attach_control(window)
    except Exception:
        window.status.set("本机管理通道未启动；分身界面仍可使用，未自动开启收发。")
    root.after(200, window.refresh)
    root.mainloop()


if __name__ == "__main__":
    main()
