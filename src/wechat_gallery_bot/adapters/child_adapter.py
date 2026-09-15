"""Child-only automation boundary. Sending is deliberately unavailable in this build."""
from .base import AdapterError
from .wxauto_adapter import WxAutoAdapter
from ..management.child_input import ChildInputGuard


class ChildWxAutoAdapter(WxAutoAdapter):
    def run(self, handler, *, window, stop_event, on_ready=None):
        guard = ChildInputGuard(window, stop_event)
        guard()  # Before importing wxauto, opening chats, or starting its listener.
        return super().run(handler, stop_event=stop_event, hwnd=window.hwnd,
                           guard=guard, on_ready=on_ready)

    def _before_input(self):
        if self._guard is None:
            raise AdapterError("分身操作必须经过会话隔离检查。")
        super()._before_input()

    def send_text(self, chat_key, text):
        raise AdapterError("本构建尚未授权真实群发送。")

    def send_image(self, chat_key, path):
        raise AdapterError("本构建尚未授权真实群发送。")
