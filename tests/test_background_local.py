"""No real WeChat: synthetic topology, strict probe doubles, real core + Tk."""
import ast
import tempfile
import time
import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from wechat_gallery_bot.adapters.base import AdapterError
from wechat_gallery_bot.adapters.window_scope import Surface, WindowScope
from wechat_gallery_bot.management.background_probe import probe_controls
from wechat_gallery_bot.management.controller import GuestController
from wechat_gallery_bot.management.local_operations_gui import LocalOperationsWindow
from wechat_gallery_bot.management.local_workspace import LocalWorkspace


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.main = Surface(10, 1, 100., "main", 0, "main-generation")
        self.group = Surface(11, 1, 100., "group", 10, "group-generation", "测试群")
        self.preview = Surface(12, 1, 100., "preview", 11, "preview-generation")
        self.menu = Surface(13, 1, 100., "menu", 12, "menu-generation")
        self.scope = WindowScope(self.main, ["测试群"])
        self.snapshot = [self.main, self.group, self.preview, self.menu]

    def test_full_owner_chain(self):
        self.assertEqual(self.scope.select("group", self.main, self.snapshot, group="测试群"), self.group)
        self.assertEqual(self.scope.select("preview", self.group, self.snapshot, before=[self.main, self.group]), self.preview)
        self.assertEqual(self.scope.select("menu", self.preview, self.snapshot, before=self.snapshot[:-1]), self.menu)

    def test_same_name_other_account_not_selected(self):
        foreign = Surface(20, 2, 200., "group", 10, "other", "测试群")
        self.assertEqual(self.scope.select("group", self.main, [foreign] + self.snapshot, group="测试群"), self.group)

    def test_no_other_account_fallback(self):
        foreign = replace(self.group, pid=2)
        with self.assertRaises(AdapterError):
            self.scope.select("group", self.main, [self.main, foreign], group="测试群")
        self.assertTrue(self.scope.invalid)

    def test_unknown_owner_and_cycles_fail(self):
        for owner in (0, 999, self.group.hwnd):
            scope = WindowScope(self.main, ["测试群"])
            surface = replace(self.group, owner=owner)
            with self.assertRaises(AdapterError):
                scope.validate(surface, [self.main, surface])

    def test_recreated_window_rejected(self):
        with self.assertRaises(AdapterError):
            self.scope.validate(self.group, [self.main, replace(self.group, identity="recreated")])

    def test_same_pid_multiple_main_rejected(self):
        with self.assertRaises(AdapterError):
            self.scope.validate(self.group, self.snapshot + [replace(self.main, hwnd=99)])

    def test_duplicate_group_rejected(self):
        with self.assertRaises(AdapterError):
            self.scope.select("group", self.main, self.snapshot + [replace(self.group, hwnd=90)], group="测试群")

    def test_unlisted_group_rejected(self):
        with self.assertRaises(AdapterError):
            self.scope.select("group", self.main, self.snapshot, group="其他群")

    def test_stale_preview_or_menu_rejected(self):
        for kind, parent in [("preview", self.group), ("menu", self.preview)]:
            with self.assertRaises(AdapterError):
                WindowScope(self.main, ["测试群"]).select(kind, parent, self.snapshot, before=self.snapshot)

    def test_error_is_latched_even_if_topology_recovers(self):
        with self.assertRaises(AdapterError):
            self.scope.validate(self.group, [])
        with self.assertRaises(AdapterError):
            self.scope.validate(self.group, self.snapshot)


class ProbeControl:
    def __init__(self, exists=True, supported=True):
        self.exists, self.supported = exists, supported
        self.properties = []

    def GroupControl(self, **kwargs):
        assert set(kwargs) == {"ClassName"}
        return self

    CustomControl = GroupControl
    EditControl = GroupControl

    def Exists(self, timeout):
        return self.exists

    def GetPropertyValue(self, property_id):
        self.properties.append(property_id)
        return self.supported

    def __getattr__(self, name):
        raise AssertionError("Probe must not use names, values or actions: " + name)


class ProbeTests(unittest.TestCase):
    def test_interface_presence_does_not_enable_live(self):
        control = ProbeControl()
        report = probe_controls(control)
        self.assertTrue(report["value_pattern_advertised"])
        self.assertEqual(control.properties, [30043])
        self.assertFalse(report["real_start_allowed"])
        self.assertFalse(report["background_text_verified"])

    def test_missing_control_does_not_fallback(self):
        control = ProbeControl(exists=False)
        report = probe_controls(control)
        self.assertFalse(report["input_found"])
        self.assertEqual(control.properties, [])

    def test_background_modules_have_no_input_or_wxauto_actions(self):
        package = Path(__file__).parents[1] / "src" / "wechat_gallery_bot"
        banned = {"Click", "DoubleClick", "MiddleClick", "RightClick", "SendKeys", "SetFocus", "SetForegroundWindow", "SetCursorPos", "SendInput", "SetClipboardText", "SetClipboardFiles", "Invoke", "SetValue"}
        for relative in ("management/background_probe.py", "management/local_workspace.py", "adapters/window_scope.py"):
            tree = ast.parse((package / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, banned)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [i.name for i in node.names]
                    self.assertFalse(any(name.startswith("wxauto4") for name in names))


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = LocalWorkspace(Path(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.workspace.close)

    def wait(self, predicate):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.01)
        self.fail("offline operation timed out")

    def test_live_rejected_without_worker(self):
        with self.assertRaises(AdapterError):
            self.workspace.start_live()
        self.assertIsNone(self.workspace.local._thread)

    def test_legacy_live_controller_cannot_bypass_background_gate(self):
        class NeverRead:
            simulated = False
            def validate(self, candidate):
                raise AssertionError("Must reject before real access")
        self.workspace.local.backend = NeverRead()
        with self.assertRaises(AdapterError):
            self.workspace.local.start({"confirmed": True})
        self.assertIsNone(self.workspace.local._thread)

    def test_config_and_demo_add_get_without_live_gallery_pollution(self):
        self.workspace.configure(["未来授权的群"], 45, 10)
        self.workspace.start_demo()
        self.wait(lambda: self.workspace.status()["demo"] == "running")
        self.workspace.scenario()
        self.wait(lambda: self.workspace.status()["events"] == 3)
        self.assertEqual(self.workspace.status()["image_replies"], 1)
        self.assertEqual(self.workspace.demo.galleries()[0]["count"], 1)
        self.assertEqual(self.workspace.local.galleries(), [])
        self.assertTrue((self.workspace.root / "offline-demo/demo-input.png").is_file())
        self.workspace.stop_demo()
        self.wait(lambda: self.workspace.status()["demo"] == "stopped")

    def test_config_rejects_invalid_limits(self):
        with self.assertRaises(Exception):
            self.workspace.configure([], 0, 10)

    def test_second_workspace_cannot_share_data(self):
        with self.assertRaises(RuntimeError):
            LocalWorkspace(self.workspace.root)

    def test_close_releases_lock_and_blocks_restart(self):
        self.workspace.start_demo()
        self.assertTrue(self.workspace.close())
        with self.assertRaises(ManagementError):
            self.workspace.start_demo()
        reopened = LocalWorkspace(self.workspace.root)
        reopened.close()


class OperationsGuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = LocalOperationsWindow(self.root, lambda: None, LocalWorkspace(Path(self.tmp.name)))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.pump(lambda: not self.gui.busy)
        self.gui.close()
        self.gui.executor.shutdown(wait=True)
        self.gui = self.root = None
        __import__("gc").collect()  # Finalize destroyed Tcl interpreters on the Tk thread.
        self.tmp.cleanup()

    def pump(self, predicate):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Tk operation timed out")

    def test_real_start_disabled_and_probe_requires_binding(self):
        self.assertEqual(str(self.gui.live_button["state"]), "disabled")
        self.gui.probe_button.invoke()
        self.assertIn("先", self.gui.note.get())
        self.assertFalse(self.gui.busy)

    def test_widgets_save_demo_and_real_preview(self):
        self.gui.groups.insert("1.0", "未来测试群")
        self.gui.save_button.invoke()
        self.pump(lambda: not self.gui.busy)
        self.assertEqual(self.gui.workspace.local.settings["groups"], ["未来测试群"])
        self.gui.demo_start.invoke()
        self.pump(lambda: not self.gui.busy and self.gui.workspace.status()["demo"] == "running")
        self.gui.poll()
        self.gui.scenario_button.invoke()
        self.pump(lambda: not self.gui.busy and self.gui.workspace.status()["events"] == 3)
        self.gui.dataset.current(1)
        self.gui.refresh_gallery()
        self.pump(lambda: not self.gui.busy)
        self.gui.tree.selection_set("0")
        self.gui.select_gallery()
        self.pump(lambda: not self.gui.busy and self.gui.photo is not None)
        self.assertEqual(self.gui.rows["0"]["count"], 1)
        self.assertLessEqual(self.gui.photo.width(), 360)
        self.gui.poll()
        self.gui.stop_button.invoke()
        self.pump(lambda: not self.gui.busy and self.gui.workspace.status()["demo"] == "stopped")


from wechat_gallery_bot.management.common import ManagementError

if __name__ == "__main__":
    unittest.main()
