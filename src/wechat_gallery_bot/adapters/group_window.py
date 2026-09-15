"""Bounded replacement for the pinned fork's first-row busy loop."""
import time
from .base import AdapterError


def open_exact_session(session, name, guard, visible, *, clock=time.monotonic, sleep=time.sleep):
    guard()
    if session.switch_chat(name) != name:
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
