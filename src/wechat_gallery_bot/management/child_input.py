"""Fail-closed input scope for user-started child-session workers.

The controller journal is an accident guard, not a same-user security sandbox.
This module does not launch a session, activate windows, or modify Windows settings.
"""
import threading

from .common import ManagementError

GUARD_STEPS = {
    "stop": "停止标志", "lease": "分身连接及隔离记录", "desktop": "桌面可访问性",
    "window": "目标窗口存在性", "process": "进程所属会话", "identity": "进程创建时间及路径",
    "enumeration": "UIA主窗口识别", "unique": "唯一窗口及最小化状态", "recheck": "连接记录复核",
}

class ChildInputGuard:
    def __init__(self, window, stop=None):
        self.window = window
        self.stop = stop if stop is not None else threading.Event()
        self.session = None

    def __call__(self):
        from .child_binding import require_child_context, process_session
        from .backend import desktop_active
        stage = "stop"
        try:
            if self.stop.is_set():
                raise ManagementError("分身自动化已停止。")
            stage = "lease"
            session = require_child_context(input_enabled=True)
            if self.session is not None and session != self.session:
                raise ManagementError("分身已改变，不能迁移运行中的机器人。")
            stage = "desktop"
            if not desktop_active(allow_remote=True):
                raise ManagementError("分身桌面不可操作。")
            import psutil
            import win32gui
            import win32process
            import uiautomation as uia
            w = self.window
            stage = "window"
            if not win32gui.IsWindow(w.hwnd):
                raise ManagementError("微信窗口已关闭。")
            stage = "process"
            _, pid = win32process.GetWindowThreadProcessId(w.hwnd)
            if pid != w.pid or process_session(pid) != session:
                raise ManagementError("微信不属于当前分身。")
            stage = "identity"
            process = psutil.Process(pid)
            if process.create_time() != w.created or process.exe().casefold() != w.executable.casefold():
                raise ManagementError("微信进程已变化。")
            stage = "enumeration"
            handles = []
            def inspect(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
                if process_session(owner_pid) != session:
                    return
                try:
                    executable = psutil.Process(owner_pid).exe()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return  # Cannot be accepted as a target.
                if executable.replace("\\", "/").rsplit("/", 1)[-1].lower() not in {"weixin.exe", "wechat.exe"}:
                    return
                # Same classifier as scan_native: UIA ClassName is not the
                # native Qt HWND class returned by win32gui.GetClassName.
                control = uia.ControlFromHandle(hwnd)
                if control.ClassName == "mmui::MainWindow" and control.ProcessId == owner_pid:
                    handles.append(hwnd)
            with uia.UIAutomationInitializerInThread():
                win32gui.EnumWindows(inspect, None)
            stage = "unique"
            if handles != [w.hwnd] or win32gui.IsIconic(w.hwnd):
                raise ManagementError("分身内必须只有一个展开的微信主窗口。")
            # Recheck the lease after inspecting the target; never continue after loss.
            stage = "recheck"
            if require_child_context(input_enabled=True) != session or self.stop.is_set():
                raise ManagementError("分身连接已失效。")
            self.session = session
        except Exception:
            self.stop.set()
            error = ManagementError("分身输入隔离检查失败，已停止；不会转到主机操作。")
            error.guard_step = stage
            raise error from None
