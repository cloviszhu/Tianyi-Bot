"""Exercise the real driver code against synthetic UIA trees, never WeChat."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wechat_gallery_bot.management.draft_trial import (
    NativeDraftDriver, belongs_to_root, diagnose_driver, describe, run_trial, structural_outline,
)
from wechat_gallery_bot.management.window_binding import Window


class Node:
    def __init__(self, number, parent=None):
        self.number, self.parent = number, parent
        self.ProcessId = 1
        self.ClassName = "mmui::MainWindow"
        self.ControlType = 50026
        self.IsEnabled = True
        self.Name = "同学群"
        self.present = True
        self.available = True
        self.children = []
        self.pattern = SimpleNamespace(IsReadOnly=False, Value="")
        self.routes = {}

    def Exists(self, _):
        return self.present

    def GetRuntimeId(self):
        return [42, self.number]

    def GetParentControl(self):
        return self.parent

    def GroupControl(self, **kwargs):
        return self.routes[kwargs["ClassName"]]

    CustomControl = GroupControl
    EditControl = GroupControl

    def TextControl(self, **kwargs):
        return self.routes[kwargs["AutomationId"].rsplit(".", 1)[-1]]

    def GetPropertyValue(self, key):
        assert key == 30043
        return self.available

    def GetChildren(self):
        return self.children

    def GetValuePattern(self):
        return self.pattern


class DriverDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.window = Window(10, 1, 100., "C:/Weixin.exe")
        self.root = Node(1)
        self.page = Node(2, self.root)
        self.info = Node(3, self.page)
        self.title = Node(4, self.info)
        self.count = Node(5, self.info)
        self.split = Node(6, self.page)
        self.edit = Node(7, self.split)
        self.root.routes["mmui::ChatMessagePage"] = self.page
        self.page.routes.update({"mmui::ChatInfoView": self.info, "mmui::XSplitterView": self.split})
        self.info.routes.update(current_chat_name_label=self.title, current_chat_count_label=self.count)
        self.split.routes["mmui::ChatInputField"] = self.edit
        # Deliberately lacks wxauto's IsElementInWindow extension.
        self.uia = SimpleNamespace(ControlFromHandle=lambda hwnd: self.root)

    def diagnose(self):
        with patch("wechat_gallery_bot.management.draft_trial.scan_native", return_value=[self.window]):
            return diagnose_driver(NativeDraftDriver(self.window, self.uia))

    def test_standard_api_tree_passes_without_wxauto_extension(self):
        result = self.diagnose()
        self.assertEqual(result["status"], "diagnostic_ready")
        self.assertFalse(result["write_attempted"])
        self.assertFalse(result["real_start_allowed"])

    def test_outline_redacts_unknown_classes_and_does_not_read_names(self):
        self.page.ClassName = "private account class"
        self.root.children = [self.page]
        result = structural_outline(self.root, self.root)
        self.assertEqual(result["nodes"][1]["class"], "other")
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("同学群", json.dumps(result, ensure_ascii=False))

    def test_outline_rejects_foreign_root(self):
        self.root.children = [Node(88)]
        with self.assertRaises(Exception):
            structural_outline(self.root, self.root)

    def test_outline_has_node_and_depth_limits(self):
        self.root.children = [self.page, self.info]
        result = structural_outline(self.root, self.root, max_nodes=2)
        self.assertEqual(len(result["nodes"]), 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(structural_outline(self.root, self.root, max_depth=0)["nodes"]), 1)

    def test_header_failure_gets_metadata_without_write(self):
        self.info.present = False
        result = self.diagnose()
        self.assertEqual(result["structure"]["status"], "metadata_only")
        self.assertFalse(result["write_attempted"])
        self.assertIn("无需重复草稿测试", describe(result))

    def test_each_missing_control_reports_specific_stage(self):
        for node, stage in [(self.page, "page"), (self.info, "header"), (self.title, "group_name"), (self.count, "group_type"), (self.edit, "input")]:
            with self.subTest(stage=stage):
                node.present = False
                result = self.diagnose()
                node.present = True
                self.assertEqual(result["failure"]["stage"], stage)
                self.assertFalse(result["write_attempted"])

    def test_wrong_name_is_distinct_from_missing_group_marker(self):
        self.title.Name = "private name not to log"
        result = self.diagnose()
        self.assertEqual(result["failure"]["stage"], "group_name")
        self.assertNotIn(self.title.Name, json.dumps(result))

    def test_same_pid_foreign_root_is_rejected(self):
        self.edit.parent = Node(99)
        result = self.diagnose()
        self.assertEqual(result["failure"]["stage"], "ownership")

    def test_foreign_pid_rejected(self):
        self.edit.ProcessId = 2
        self.assertEqual(self.diagnose()["failure"]["stage"], "ownership")

    def test_cycle_is_bounded(self):
        self.split.parent = self.edit
        self.assertFalse(belongs_to_root(self.root, self.edit))

    def test_depth_limit_is_bounded(self):
        self.assertFalse(belongs_to_root(self.root, self.edit, max_depth=2))

    def test_disabled_input_distinct_from_missing_pattern(self):
        self.edit.IsEnabled = False
        self.assertEqual(self.diagnose()["failure"]["stage"], "enabled")
        self.edit.IsEnabled = True
        self.edit.available = False
        self.assertEqual(self.diagnose()["failure"]["stage"], "value_available")

    def test_readonly_distinct_from_attachments(self):
        self.edit.pattern.IsReadOnly = True
        self.assertEqual(self.diagnose()["failure"]["stage"], "read_only")
        self.edit.pattern.IsReadOnly = False
        self.edit.children = [Node(8, self.edit)]
        self.assertEqual(self.diagnose()["failure"]["stage"], "attachments")

    def test_private_exception_is_not_logged(self):
        self.edit.GetValuePattern = lambda: (_ for _ in ()).throw(AttributeError("secret account and draft"))
        result = self.diagnose()
        self.assertEqual(result["failure"], {"stage": "value_pattern", "error_kind": "AttributeError"})
        self.assertNotIn("secret", json.dumps(result))
        self.assertIn("Value接口获取", describe(result))

    def test_draft_value_read_failure_is_classified(self):
        class BrokenPattern:
            IsReadOnly = False
            @property
            def Value(self):
                raise RuntimeError("secret")
        self.edit.pattern = BrokenPattern()
        self.assertEqual(self.diagnose()["failure"]["stage"], "draft_read")

    def test_existing_draft_diagnostic_never_writes(self):
        self.edit.pattern.Value = "secret draft"
        result = self.diagnose()
        self.assertEqual(result["reason"], "existing_draft")
        self.assertFalse(result["clear_attempted"])
        self.assertNotIn("secret", json.dumps(result))

    def test_installed_standard_library_contract_without_controls(self):
        import uiautomation as uia
        self.assertTrue(callable(uia.Control.GetParentControl))
        self.assertTrue(callable(uia.Control.GetRuntimeId))
        self.assertFalse(hasattr(uia, "IsElementInWindow"))

    def test_read_only_worker_mode_does_not_request_write_consent(self):
        with patch("wechat_gallery_bot.management.draft_trial.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'{"status":"diagnostic_ready","reason":"read_only_preflight_passed"}'
            result = run_trial(self.window, confirmed=False, readonly=True)
            data = json.loads(run.call_args.kwargs["input"])
            self.assertTrue(data["readonly"])
            self.assertFalse(data["confirmed"])
            self.assertEqual(result["mode"], "read_only")

    def test_read_only_timeout_does_not_warn_about_unwritten_marker(self):
        with patch("wechat_gallery_bot.management.draft_trial.subprocess.run", side_effect=TimeoutError):
            result = run_trial(self.window, confirmed=False, readonly=True)
            self.assertIn("未调用写入", describe(result))
            self.assertNotIn("手动清除", describe(result))


if __name__ == "__main__":
    unittest.main()
