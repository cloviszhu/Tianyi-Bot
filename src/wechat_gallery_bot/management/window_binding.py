"""Read-only, ephemeral host window binding. No chat or wxauto dependency.

This is a window identity, NOT proof of the account logged into that window.
All service methods are called serially by the GUI worker.
"""
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .common import ManagementError


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    created: float
    executable: str
    minimized: bool = False
    owner: int = 0

    @property
    def key(self):
        return (self.hwnd, self.pid, self.created, self.executable)


def scan_native(*, child=False):
    """Read only top-level structural metadata, never names or descendants."""
    from .backend import desktop_active
    if child:
        from .child_binding import require_child_context, process_session
        session = require_child_context()
    accessible = desktop_active(allow_remote=True) if child else desktop_active()
    if not accessible:
        raise ManagementError("桌面已锁定、不可访问或处于远程会话，请解锁后重新识别。")
    import psutil
    import win32gui
    import win32process
    import uiautomation as uia
    handles = []
    win32gui.EnumWindows(lambda h, _: handles.append(h) if win32gui.IsWindowVisible(h) else None, None)
    result = []
    with uia.UIAutomationInitializerInThread():
        for hwnd in handles:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if child and process_session(pid) != session:
                    continue
                process = psutil.Process(pid)
                executable = process.exe()
                if executable.replace("\\", "/").rsplit("/", 1)[-1].lower() not in {"weixin.exe", "wechat.exe"}:
                    continue
                control = uia.ControlFromHandle(hwnd)
                if control.ClassName != "mmui::MainWindow" or control.ProcessId != pid:
                    continue
                result.append(Window(hwnd, pid, process.create_time(), executable,
                                     bool(win32gui.IsIconic(hwnd)), win32gui.GetWindow(hwnd, 4)))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Missing or inaccessible processes cannot become binding candidates.
                continue
    if child and require_child_context() != session:
        raise ManagementError("分身身份改变，请重新连接。")
    return result


class NativeProvider:
    child = False
    def scan(self):
        # A stuck UIA provider cannot freeze the desktop GUI or keep Python alive.
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            # pythonw has no stdout even when redirected; the hidden worker needs JSON stdout.
            executable = executable.with_name("python.exe")
        result = subprocess.run(
            [str(executable), "-m", __name__, "--scan-child" if self.child else "--scan"], capture_output=True,
            timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise ManagementError("无法读取窗口结构：请确认已解锁、微信已打开且识别组件已安装。未绑定任何新窗口。")
        return [Window(**item) for item in json.loads(result.stdout.decode("utf-8"))]


class BindingService:
    def __init__(self, provider=None, clock=time.monotonic):
        self.provider = provider or NativeProvider()
        self.clock = clock
        self.generation = 0
        self.candidates = []
        self.bound = None
        self.last_check = None

    def unbind(self):
        self.bound = None
        self.last_check = None

    def refresh(self):
        self.unbind()
        self.generation += 1
        self.candidates = []
        self.candidates = self.provider.scan()
        return self.generation, list(self.candidates)

    def _validate(self, candidate, current):
        matches = [w for w in current if w.key == candidate.key]
        same_process = [w for w in current if w.pid == candidate.pid]
        if len(matches) != 1 or len(same_process) != 1:
            raise ManagementError("窗口已变化，或同一进程有多个主窗口；无法安全区分，请重新识别。")
        if matches[0].minimized or matches[0].owner:
            raise ManagementError("窗口已最小化或不是独立主窗口，请展开并重新识别。")

    def bind(self, generation, key, confirmed):
        self.unbind()
        if not confirmed or generation != self.generation:
            raise ManagementError("请重新选择窗口并人工核对小号。")
        matches = [w for w in self.candidates if w.key == key]
        if len(matches) != 1:
            raise ManagementError("选择已失效，请重新识别。")
        self._validate(matches[0], self.provider.scan())
        self.bound = matches[0]
        self.last_check = self.clock()
        return self.bound

    def check(self):
        if self.bound is None:
            return None
        try:
            now = self.clock()
            if now - self.last_check > 15 or now < self.last_check:
                raise ManagementError("检查中断过久（可能休眠），请重新识别并核对小号。")
            self._validate(self.bound, self.provider.scan())
            self.last_check = now
            return self.bound
        except Exception:
            self.unbind()
            raise


if __name__ == "__main__":
    try:
        payload = [asdict(w) for w in scan_native(child="--scan-child" in sys.argv)]
        sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
    except Exception:
        # Never emit account/window names, executable paths or raw UIA exceptions.
        sys.exit(1)
