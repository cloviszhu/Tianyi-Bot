"""Child-only automation; sends require explicit per-run GUI consent."""
import hashlib
import json
from .base import AdapterError
from .wxauto_adapter import WxAutoAdapter
from ..management.child_input import ChildInputGuard


class ChildWxAutoAdapter(WxAutoAdapter):
    def _check_chat(self, chat):
        try:
            return super()._check_chat(chat)
        except AdapterError:
            # The pinned fork hardcodes full AutomationIds in ChatInfo.
            # Reuse the bounded suffix-based structural locator on 4.1.
            from ..management.group_listener import locate_controls
            self._before_input()
            root = chat._api.control
            title, count, listing = locate_controls(root)
            name = title.Name
            if (name not in self.groups or chat.who != name or root.Name != name
                    or root.ProcessId != chat._api.parent.pid
                    or not all(c.Exists(0) for c in (title, count, listing))):
                raise AdapterError("群窗口结构与配置不匹配。")
            return name

    def _prepare_client(self, wx):
        from wxauto4 import uia
        from wxauto4.param import WxResponse
        from .group_window import open_exact_session, open_group_window, find_scoped_group
        session = wx._api._session_api
        def open_group(name):
            result = open_exact_session(session, name, self._before_input, uia.IsElementInWindow, prefer_visible=True)
            return WxResponse.success(data={"nickname": result})
        # Per-client instance only. Never modify installed upstream files.
        session.open_separate_window = open_group
        wx._api.get_sub_wnd = lambda name: find_scoped_group(wx._api, name, self._before_input)
        # MainWnd otherwise calls the incompatible search path BEFORE the
        # SessionBox hook above. Keep this replacement scoped to this client.
        wx._api.open_separate_window = lambda name: open_group_window(
            wx._api, name, self._before_input, open_group)

    def _event_id(self, message):
        # Upstream id is UIA runtimeid, not a server message identifier. Scope it
        # to the WeChat process incarnation, stable across bot-worker restarts.
        origin = getattr(self, "_event_origin", None)
        if origin is None or message.id is None:
            raise AdapterError("缺少可用于本轮去重的窗口或消息标识。")
        payload = [origin, message.id]
        return "uia-process-v1:" + hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()

    def _set_event_origin(self, window):
        self._event_origin = [window.pid, window.created, window.executable.casefold()]

    def run(self, handler, *, window, stop_event, on_ready=None, on_checked=None, send_groups=()):
        allowed = frozenset(send_groups)
        if not allowed.issubset(self.groups):
            raise AdapterError("发送授权超出监听群配置。")
        self._send_groups = frozenset()
        self._set_event_origin(window)
        check = ChildInputGuard(window, stop_event)
        def guard():
            check()
            if on_checked is not None:
                on_checked()
        guard()  # Before importing wxauto, opening chats, or starting its listener.
        self._send_groups = allowed
        try:
            return super().run(handler, stop_event=stop_event, hwnd=window.hwnd,
                               guard=guard, on_ready=on_ready)
        finally:
            self._send_groups = frozenset()

    def _before_input(self):
        if self._guard is None:
            raise AdapterError("分身操作必须经过会话隔离检查。")
        super()._before_input()

    def send_text(self, chat_key, text):
        self._require_send(chat_key)
        return super().send_text(chat_key, text)

    def send_image(self, chat_key, path):
        self._require_send(chat_key)
        return super().send_image(chat_key, path)

    def _require_send(self, chat_key):
        if chat_key not in getattr(self, "_send_groups", ()) or not self._ready:
            raise AdapterError("本轮未授权此群发送。")
        self._before_input()
