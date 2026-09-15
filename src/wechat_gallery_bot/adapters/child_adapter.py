"""Child-only automation; sends require explicit per-run GUI consent."""
from .base import AdapterError
from .wxauto_adapter import WxAutoAdapter
from ..management.child_input import ChildInputGuard


class ChildWxAutoAdapter(WxAutoAdapter):
    def run(self, handler, *, window, stop_event, on_ready=None, on_checked=None, send_groups=()):
        allowed = frozenset(send_groups)
        if not allowed.issubset(self.groups):
            raise AdapterError("发送授权超出监听群配置。")
        self._send_groups = frozenset()
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
