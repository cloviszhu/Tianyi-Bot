import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from wechat_gallery_bot.management import message_observation as module


def row(identity, text="1", cls="mmui::ChatTextItemView"):
    return module.parse_row(SimpleNamespace(ClassName=cls, AutomationId=identity, Name=text))


class MessageObservationTests(unittest.TestCase):
    def test_gui_retries_are_bounded_and_binding_change_stops(self):
        from wechat_gallery_bot.management.local_operations_gui import LocalOperationsWindow
        from wechat_gallery_bot.management.common import ManagementError
        gui = LocalOperationsWindow.__new__(LocalOperationsWindow)
        gui.closed, gui.listening, gui.busy = False, True, False
        gui.listen_generation, gui.listen_failures = 1, 0
        gui.listen_window, gui.listen_group = "window", "test"
        gui.binding_getter = lambda: "window"
        gui.observation = module.MessageObservation()
        notes, pending = [], []
        gui.listen_note = SimpleNamespace(set=notes.append)
        gui.schedule = lambda delay, callback: pending.append(callback)
        gui.submit = lambda read, done: done(read())
        with patch("wechat_gallery_bot.management.group_listener.read_snapshot", side_effect=ManagementError("test")):
            gui.listen_tick(1)
            for _ in range(3):
                pending.pop(0)()
        self.assertFalse(gui.listening)
        self.assertEqual(gui.listen_failures, 4)
        self.assertFalse(pending)
        gui.listening = True
        gui.binding_getter = lambda: "different"
        gui.listen_tick(gui.listen_generation)
        self.assertFalse(gui.listening)

    def test_types_and_unknown_are_not_guessed(self):
        self.assertEqual(row("a")["kind"], "text")
        self.assertEqual(row("a", "图片", "mmui::ChatBubbleItemView")["kind"], "image")
        self.assertEqual(row("a", "secret", "unexpected")["text"], "")
        self.assertEqual(row("", "时间")["kind"], "unknown")

    def test_repeated_text_distinct_identity_counts_and_poll_deduplicates(self):
        obs = module.MessageObservation()
        a, b, c = row("a"), row("b"), row("c")
        self.assertEqual(obs.update({"messages": [a]}), 0)
        self.assertEqual(obs.update({"messages": [a,b,c]}), 2)
        self.assertEqual(obs.update({"messages": [a,b,c]}), 0)
        self.assertEqual(obs.count, 2)

    def test_history_prepend_and_tail_window_slide(self):
        obs = module.MessageObservation()
        a,b,c,d = [row(k) for k in "abcd"]
        obs.update({"messages": [b,c]})
        self.assertEqual(obs.update({"messages": [a,b,c]}), 0)
        self.assertEqual(obs.update({"messages": [c,d]}), 1)
        self.assertEqual(obs.gaps, 0)

    def test_changed_identity_or_discontinuity_rebaselines(self):
        obs = module.MessageObservation()
        obs.update({"messages": [row("a")]})
        self.assertEqual(obs.update({"messages": [row("b")]}), 0)
        self.assertEqual(obs.gaps, 1)
        obs.interrupt()
        obs.interrupt()
        self.assertEqual(obs.gaps, 2)
        self.assertEqual(obs.update({"messages": [row("b"),row("c")]}), 0)
        self.assertEqual(obs.update({"messages": [row("c"),row("d")]}), 1)

    def test_ambiguous_duplicate_rows_not_counted(self):
        obs = module.MessageObservation()
        obs.update({"messages": [row("a")]})
        self.assertEqual(obs.update({"messages": [row("a"),row("a")]}), 0)
        self.assertEqual(obs.gaps, 1)

    def test_unknown_rows_and_recalled_payload(self):
        obs = module.MessageObservation()
        obs.update({"messages": [row("a")]})
        self.assertEqual(obs.update({"messages": [row("a"),row("b",cls="unknown")]}), 0)
        self.assertEqual(obs.unknown, 1)
        self.assertEqual(obs.update({"messages": [row("a"),row("b","recalled")]}), 0)
        self.assertEqual(obs.gaps, 1)

    def test_parser_has_no_disk_network_or_ui_input(self):
        source = inspect.getsource(module)
        for forbidden in ["open(", "write_text", "requests", ".Click(", "SendKeys", "ScreenShot", "Clipboard"]:
            self.assertNotIn(forbidden, source)
