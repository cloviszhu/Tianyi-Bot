import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from wechat_gallery_bot.adapters.wxauto_adapter import WxAutoAdapter
from wechat_gallery_bot.management.child_intake import issue_message


class IntakeIssuesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = WxAutoAdapter(("group",), Path(self.temp.name))
        self.adapter._ready = True
        self.adapter.on_issue = Mock()
        self.chat = SimpleNamespace(who="group", ChatInfo=lambda: {
            "chat_type": "group", "chat_name": "group"})
        self.message = SimpleNamespace(attr="friend", type="image", sender="group", id=1)

    def test_missing_sender_visible_and_never_guessed(self):
        handler = Mock()
        self.adapter._dispatch(self.message, self.chat, handler)
        handler.assert_not_called()
        self.adapter.on_issue.assert_called_once_with("sender_unavailable")
        self.assertIsNone(self.adapter._active)

    def test_callback_error_reports_stage_not_sensitive_exception(self):
        self.message.sender = "valid"
        self.adapter._dispatch(self.message, self.chat, Mock(side_effect=ValueError("secret body")))
        self.adapter.on_issue.assert_called_once_with("handler")

    def test_valid_event_is_delivered_without_issue(self):
        self.message.sender = "valid"
        handler = Mock()
        self.adapter._dispatch(self.message, self.chat, handler)
        handler.assert_called_once()
        self.adapter.on_issue.assert_not_called()

    def test_gui_only_displays_allowlisted_labels(self):
        self.assertNotIn("secret", issue_message("secret", 3))
        self.assertIn("3", issue_message("sender_unavailable", 3))
        self.assertIn("发送者", issue_message("sender_unavailable", 3))
