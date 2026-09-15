"""Fail-closed input scope for user-started child-session workers.

The controller journal is an accident guard, not a same-user security sandbox.
This module does not launch a session, activate windows, or modify Windows settings.
"""
import threading

from .common import ManagementError


class ChildInputGuard:
    def __init__(self, window, stop=None):
        self.window = window
        self.stop = stop if stop is not None else threading.Event()
        self.session = None

    def __call__(self):
        from .child_binding import require_child_context, process_session
        from .backend import desktop_active
        try:
            if self.stop.is_set():
                raise ManagementError("分身自动化已停止。")
            session = require_child_context(input_enabled=True)
            if self.session is not None and session != self.session:
                raise ManagementError("分身已改变，不能迁移运行中的机器人。")
            if not desktop_active(allow_remote=True):
                raise ManagementError("分身桌面不可操作。")
            import psutil
            import win32gui
            import win32process
            w = self.window
            if not win32gui.IsWindow(w.hwnd):
                raise ManagementError("微信窗口已关闭。")
            _, pid = win32process.GetWindowThreadProcessId(w.hwnd)
            if pid != w.pid or process_session(pid) != session:
                raise ManagementError("微信不属于当前分身。")
            process = psutil.Process(pid)
            if process.create_time() != w.created or process.exe().casefold() != w.executable.casefold():
                raise ManagementError("微信进程已变化。")
            handles = []
            def inspect(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "mmui::MainWindow":
                    _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if process_session(owner_pid) == session:
                        handles.append(hwnd)
            win32gui.EnumWindows(inspect, None)
            if handles != [w.hwnd] or win32gui.IsIconic(w.hwnd):
                raise ManagementError("分身内必须只有一个展开的微信主窗口。")
            # Recheck the lease after inspecting the target; never continue after loss.
            if require_child_context(input_enabled=True) != session or self.stop.is_set():
                raise ManagementError("分身连接已失效。")
            self.session = session
        except Exception:
            self.stop.set()
            raise ManagementError("分身输入隔离检查失败，已停止；不会转到主机操作。") from None
