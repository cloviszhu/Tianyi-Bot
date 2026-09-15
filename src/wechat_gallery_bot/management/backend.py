"""All live discovery is guest-only and only triggered by explicit Connect."""
import ctypes
import json
import subprocess
import sys
from pathlib import Path

from .common import ManagementError


def in_virtualbox_guest() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json -Compress"],
        capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=True,
    )
    value = json.loads(result.stdout.decode("utf-8-sig", errors="replace"))
    return "virtualbox" in str(value.get("Model", "")).lower()


def desktop_active(*, allow_remote=False) -> bool:
    if sys.platform != "win32":
        return False
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    if user.GetSystemMetrics(0x1000) and not allow_remote:
        return False
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    handle = user.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS
    if not handle:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        return bool(user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed))) and name.value.lower() == "default"
    finally:
        user.CloseDesktop(handle)


class LiveBackend:
    simulated = False

    def __init__(self):
        if not in_virtualbox_guest():
            raise ManagementError("真实服务只允许在VirtualBox虚拟机内启动；主机请选择离线演示。")

    def discover(self) -> list[dict]:
        if not desktop_active():
            raise ManagementError("虚拟机桌面已锁定、未登录或处于RDP会话；请打开控制台人工处理。")
        from ..adapters.wxauto_adapter import check_backend
        check_backend()
        import win32gui
        import win32process
        import psutil
        import uiautomation as uia

        handles = []
        win32gui.EnumWindows(lambda hwnd, _: handles.append(hwnd) if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Qt51514QWindowIcon" else None, None)
        result = []
        with uia.UIAutomationInitializerInThread():
            for hwnd in handles:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    process = psutil.Process(pid)
                    if process.name().lower() not in {"weixin.exe", "wechat.exe"}:
                        continue
                    control = uia.ControlFromHandle(hwnd)
                    if control.ClassName != "mmui::MainWindow":
                        continue
                    created = process.create_time()
                    result.append({"id": f"{hwnd}:{pid}:{created}", "hwnd": hwnd, "pid": pid,
                                   "created": created, "title": win32gui.GetWindowText(hwnd)})
                except (OSError, psutil.Error):
                    continue
        return result

    def validate(self, candidate: dict) -> None:
        if not desktop_active():
            raise ManagementError("虚拟机桌面不可操作；机器人已停止，请解锁后重新连接并确认小号。")
        # Read-only checks on each send; no other account/window is auto-selected.
        import psutil
        import win32gui
        import win32process
        try:
            _, pid = win32process.GetWindowThreadProcessId(candidate["hwnd"])
            valid = (win32gui.IsWindow(candidate["hwnd"]) and win32gui.IsWindowVisible(candidate["hwnd"])
                     and not win32gui.IsIconic(candidate["hwnd"]) and pid == candidate["pid"]
                     and psutil.Process(pid).create_time() == candidate["created"]
                     and win32gui.GetWindowText(candidate["hwnd"]) == candidate["title"])
        except Exception:
            valid = False
        if not valid:
            raise ManagementError("绑定窗口已关闭、最小化或变化；请重新连接并确认小号。")

    def run(self, adapter, handler, stop, candidate, guard, ready):
        import uiautomation as uia
        with uia.UIAutomationInitializerInThread():
            adapter.run(handler, stop_event=stop, hwnd=candidate["hwnd"], guard=guard, on_ready=ready)


class SimulatedBackend:
    simulated = True

    def __init__(self):
        self.available = True
        self.identity = "simulated-small-account"

    def discover(self):
        return [{"id": self.identity, "hwnd": 0, "pid": 0, "created": 0, "title": "模拟微信窗口（不会连接账号）"}] if self.available else []

    def validate(self, candidate):
        if not self.available or candidate["id"] != self.identity:
            raise ManagementError("模拟桌面或绑定窗口已失效。")

    def run(self, adapter, handler, stop, candidate, guard, ready):
        ready()
        while not stop.wait(0.05):
            guard()
