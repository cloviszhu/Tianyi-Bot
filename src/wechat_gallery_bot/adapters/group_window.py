"""Bounded replacement for the pinned fork's first-row busy loop."""
import time
from .base import AdapterError


def find_scoped_group(api, name, guard):
    """Do not pin native Qt patch-version class names when locating a child."""
    import win32gui
    import win32process
    from wxauto4 import uia
    from wxauto4.ui.main import WeChatSubWnd
    guard()
    handles = []
    def collect(hwnd, _):
        if win32process.GetWindowThreadProcessId(hwnd)[1] == api.pid:
            handles.append(hwnd)
    win32gui.EnumWindows(collect, None)
    matches = []
    for hwnd in handles:
        control = uia.ControlFromHandle(hwnd)
        if (control.ProcessId == api.pid and control.ClassName == WeChatSubWnd._ui_cls_name
                and control.Name == name):
            matches.append(hwnd)
    if len(matches) > 1:
        raise AdapterError("同进程存在多个同名群窗口。")
    if not matches:
        return None
    guard()
    result = WeChatSubWnd(matches[0], api)
    if result.pid != api.pid or result.nickname != name:
        raise AdapterError("群窗口创建期间身份变化。")
    return result


def open_exact_session(session, name, guard, visible, *, clock=time.monotonic, sleep=time.sleep, prefer_visible=False):
    guard()
    already_visible = prefer_visible and any(item.name == name and visible(session.session_list, item.control)
                                             for item in session.get_session())
    if not already_visible and session.switch_chat(name) != name:
        raise AdapterError("配置群未精确匹配，未打开其他会话。")
    deadline = clock() + 5
    while clock() < deadline:
        guard()
        matches = [item for item in session.get_session()
                   if item.name == name and visible(session.session_list, item.control)]
        if len(matches) > 1:
            raise AdapterError("存在同名可见会话，无法唯一定位。")
        if matches:
            guard()
            # Re-read the live row name before the input boundary, not its cached
            # SessionElement.content. No prefix matching or first-row fallback.
            lines = [line for line in str(matches[0].control.Name).splitlines() if line.strip()]
            if not lines or lines[0] != name:
                raise AdapterError("会话行已变化，取消打开。")
            matches[0].double_click()
            return name
        sleep(.1)
    raise AdapterError("等待配置群会话行超时，未打开其他会话。")


def open_group_window(api, name, guard, open_session, *, clock=time.monotonic, sleep=time.sleep):
    guard()
    existing = api.get_sub_wnd(name)
    if existing is not None:
        return existing
    guard()
    api._show()  # Child desktop only; a covering GUI would intercept clicks.
    guard()
    open_session(name)
    deadline = clock() + 5
    while clock() < deadline:
        existing = api.get_sub_wnd(name)
        if existing is not None:
            return existing
        sleep(.1)
    raise AdapterError("等待配置群独立窗口超时。")
