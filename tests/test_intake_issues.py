import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from wechat_gallery_bot.adapters.wxauto_adapter import WxAutoAdapter
from wechat_gallery_bot.management.child_intake import issue_message, failure_record, failure_message, code_locations


class IntakeIssuesTests(unittest.TestCase):
    def test_code_coordinates_exclude_locals_source_and_absolute_paths(self):
        space = {"__name__": "wxauto4.ui.session"}
        exec(compile("def fail():\n    private_account = 'secret'\n    raise Exception(private_account)\n", "C:/private/user/source.py", "exec"), space)
        try:
            space["fail"]()
        except Exception as exc:
            result = failure_record("group_listener", exc)
        self.assertEqual(result["locations"], [{"module": "wxauto4.ui.session", "line": 3}])
        self.assertNotIn("secret", str(result))
        self.assertNotIn("private", str(result))

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

    def test_startup_error_chain_preserves_types_without_private_details(self):
        try:
            try:
                raise AttributeError("private account path")
            except AttributeError as cause:
                raise RuntimeError("private message") from cause
        except RuntimeError as exc:
            record = failure_record("wechat_constructor", exc)
        self.assertEqual(record["kinds"], ["RuntimeError", "AttributeError"])
        self.assertIn("连接微信控件", failure_message(record))
        self.assertNotIn("private", str(record))

    def test_unknown_exception_and_stage_never_leak_custom_names(self):
        secret_type = type("private_account", (Exception,), {})
        record = failure_record("private_stage", secret_type("private_message"))
        self.assertEqual(record, {"stage": "request", "kinds": ["Exception"]})

    def test_suppressed_context_not_reported_and_cycles_bounded(self):
        outer = ValueError("outer")
        outer.__context__ = TypeError("hidden")
        outer.__suppress_context__ = True
        self.assertEqual(failure_record("data_lock", outer)["kinds"], ["ValueError"])
        outer.__cause__ = outer
        self.assertEqual(failure_record("data_lock", outer)["kinds"], ["ValueError"])
