import inspect
import unittest
from wechat_gallery_bot.management import group_listener
from wechat_gallery_bot.management.common import ManagementError


class GroupListenerTests(unittest.TestCase):
    def test_diagnostic_excludes_unknown_text_and_keeps_counts(self):
        raw = {"reason": "candidates", "counts": [0, 0, 1], "secret": "private",
               "nodes": [{"parent": -1, "depth": 0, "class": "private", "Name": "private"}]}
        clean = group_listener.sanitize_diagnostics(raw)
        self.assertNotIn("private", str(clean))
        self.assertEqual(clean["counts"], [0, 0, 1])
        self.assertIn("0/0/1", group_listener.describe_failure({"stage": "locate", "kind": "ManagementError", "diagnostics": clean}))

    def test_locator_accepts_changed_ancestry_and_rejects_ambiguity(self):
        class Control:
            def __init__(self, aid="", cls="", children=()):
                self.AutomationId, self.ClassName, self.children = aid, cls, children
            def GetChildren(self):
                return self.children
            def ListControl(self, **kwargs):
                return self
            def Exists(self, *_):
                return True
        title = Control("new.path.current_chat_name_label")
        count = Control("current_chat_count_label")
        view = Control(cls="mmui::MessageView")
        root = Control(cls="mmui::ChatMessagePage", children=[Control(children=[title, count]), view])
        self.assertEqual(group_listener.locate_controls(root), (title, count, view))
        root.children.append(Control("duplicate.current_chat_name_label"))
        with self.assertRaises(ManagementError):
            group_listener.locate_controls(root)

    def test_deep_window_shell_resets_page_depth_and_prunes_editors(self):
        class Control:
            def __init__(self, cls="", aid="", children=()):
                self.ClassName, self.AutomationId, self.children = cls, aid, children
            def GetChildren(self):
                if self.ClassName in {"mmui::MessageView", "mmui::ChatInputField"}:
                    raise AssertionError("must not inspect message or editor descendants")
                return self.children
            def ListControl(self, **kwargs):
                return self
            def Exists(self, *_):
                return True
        title = Control(aid="header.current_chat_name_label")
        count = Control(aid="header.current_chat_count_label")
        view = Control(cls="mmui::MessageView")
        header = Control(children=[title, count])
        for _ in range(14):
            header = Control(children=[header])
        page = Control(cls="mmui::ChatMessagePage", children=[header, view, Control(cls="mmui::ChatInputField")])
        root = page
        for _ in range(12):
            root = Control(children=[root])
        self.assertEqual(group_listener.locate_controls(root), (title, count, view))
        self.assertLessEqual(len(group_listener.diagnostics["nodes"]), 500)
        with self.assertRaises(ManagementError):
            group_listener.locate_controls(Control(children=[root, page]))
        with self.assertRaises(ManagementError):
            group_listener.locate_controls(Control())
        deep = Control()
        for _ in range(26):
            deep = Control(children=[deep])
        page.children.append(deep)
        with self.assertRaises(ManagementError):
            group_listener.locate_controls(root)
        self.assertEqual(group_listener.diagnostics["reason"], "limit")

    def test_failure_does_not_expose_raw_exception(self):
        error = group_listener.failure(PermissionError("private chat text"))
        self.assertNotIn("private", str(error))
        self.assertIn("PermissionError", group_listener.describe_failure(error))

    def test_history_baseline_and_duplicate_poll(self):
        watcher = group_listener.Observation()
        first = {"view": [1], "items": [[1, 2], [1, 3]]}
        self.assertEqual(watcher.update(first), 0)
        self.assertEqual(watcher.update(first), 0)
        self.assertEqual(watcher.update({"view": [1], "items": [[1, 3], [1, 4]]}), 1)
        self.assertEqual(watcher.count, 1)

    def test_list_replacement_stops(self):
        watcher = group_listener.Observation()
        watcher.update({"view": [1], "items": []})
        with self.assertRaises(ManagementError):
            watcher.update({"view": [2], "items": []})

    def test_bounded_snapshot(self):
        with self.assertRaises(ManagementError):
            group_listener.Observation().update({"view": [1], "items": [[i] for i in range(1001)]})

    def test_native_path_has_no_input_or_message_text_read(self):
        source = inspect.getsource(group_listener)
        for forbidden in ["SendKeys", "SendInput", ".Click(", "SetForegroundWindow", "Clipboard", "item.Name", "wxauto4"]:
            self.assertNotIn(forbidden, source)
        self.assertIn("scan_native(child=True)", source)
        self.assertIn("timeout=8", source)
