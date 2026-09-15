"""Draft experiment tests use in-memory targets only; never run native_experiment."""
import ast
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_gallery_bot.management.draft_trial import experiment, EnvironmentMonitor, run_trial, describe
from wechat_gallery_bot.management.local_operations_gui import LocalOperationsWindow
from wechat_gallery_bot.management.local_workspace import LocalWorkspace
from wechat_gallery_bot.management.window_binding import Window


class Monitor:
    def __init__(self):
        self.changed = False

    def check(self):
        if self.changed:
            raise RuntimeError("synthetic input change")


class Driver:
    def __init__(self, value=""):
        self.value, self.writes = value, []
        self.valid = True
        self.after_write = lambda value: None

    def verify(self):
        if not self.valid:
            raise RuntimeError("synthetic wrong target")

    def read(self):
        return self.value

    def write(self, value):
        self.writes.append(value)
        self.value = value
        self.after_write(value)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.driver, self.monitor = Driver(), Monitor()

    def test_writes_once_clears_once_and_never_enables_bot(self):
        report = experiment(self.driver, self.monitor)
        self.assertEqual(report["status"], "observed_pass")
        self.assertTrue(self.driver.writes[0].startswith("TIANYI_DRAFT_TEST_"))
        self.assertEqual(self.driver.writes[1:], [""])
        self.assertFalse(report["real_start_allowed"])

    def test_existing_draft_preserved_including_whitespace(self):
        for value in ("secret draft", " ", "\n", "\ufffc"):
            driver = Driver(value)
            report = experiment(driver, self.monitor)
            self.assertEqual(driver.value, value)
            self.assertEqual(driver.writes, [])
            self.assertEqual(report["status"], "not_run")
            self.assertNotIn(value, report.values())

    def test_wrong_target_no_write(self):
        self.driver.valid = False
        report = experiment(self.driver, self.monitor)
        self.assertEqual(self.driver.writes, [])
        self.assertEqual(report["status"], "not_run")

    def test_environment_changed_before_write(self):
        self.monitor.changed = True
        report = experiment(self.driver, self.monitor)
        self.assertFalse(report["write_attempted"])
        self.assertEqual(self.driver.writes, [])

    def test_focus_change_after_write_stops_without_cleanup_action(self):
        self.driver.after_write = lambda _: setattr(self.monitor, "changed", True)
        report = experiment(self.driver, self.monitor)
        self.assertEqual(report["status"], "uncertain_cleanup")
        self.assertEqual(len(self.driver.writes), 1)
        self.assertTrue(report["environment_changed"])

    def test_target_change_after_write_never_clears_other_target(self):
        self.driver.after_write = lambda _: setattr(self.driver, "valid", False)
        report = experiment(self.driver, self.monitor)
        self.assertEqual(report["status"], "uncertain_cleanup")
        self.assertEqual(len(self.driver.writes), 1)

    def test_changed_user_content_never_cleared(self):
        self.driver.after_write = lambda _: setattr(self.driver, "value", "user replacement")
        report = experiment(self.driver, self.monitor)
        self.assertEqual(self.driver.value, "user replacement")
        self.assertEqual(len(self.driver.writes), 1)
        self.assertFalse(report["cleared"])

    def test_partial_write_exception_cleans_only_own_marker(self):
        def callback(value):
            if value:
                raise RuntimeError("after side effect")
        self.driver.after_write = callback
        report = experiment(self.driver, self.monitor)
        self.assertEqual(self.driver.writes[1:], [""])
        self.assertTrue(report["cleared"])
        self.assertNotEqual(report["status"], "observed_pass")

    def test_clear_exception_is_not_claimed_success(self):
        def callback(value):
            if not value:
                raise RuntimeError("unknown clear outcome")
        self.driver.after_write = callback
        report = experiment(self.driver, self.monitor)
        self.assertEqual(report["status"], "uncertain_cleanup")
        self.assertEqual(len(self.driver.writes), 2)

    def test_monitor_latches_change_even_after_return_to_original(self):
        value = [1]
        monitor = EnvironmentMonitor(lambda: value[0])
        try:
            value[0] = 2
            with self.assertRaises(Exception):
                monitor.check()
            value[0] = 1
            with self.assertRaises(Exception):
                monitor.check()
        finally:
            monitor.close()


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.window = Window(10, 1, 100., "C:/Weixin.exe")

    def test_confirmation_required_before_subprocess(self):
        with patch("wechat_gallery_bot.management.draft_trial.subprocess.run") as run:
            with self.assertRaises(Exception):
                run_trial(self.window, confirmed=False)
            run.assert_not_called()

    def test_hidden_bounded_worker_and_fixed_group(self):
        with patch("wechat_gallery_bot.management.draft_trial.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'{"status":"not_run","real_start_allowed":false}'
            run_trial(self.window, confirmed=True)
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            self.assertNotIn("shell", run.call_args.kwargs)
            payload = json.loads(run.call_args.kwargs["input"])
            self.assertEqual(payload["group"], "同学群")
            self.assertEqual(len(run.call_args.args[0]), 3)

    def test_timeout_no_retry_and_manual_cleanup_warning(self):
        with patch("wechat_gallery_bot.management.draft_trial.subprocess.run", side_effect=TimeoutError) as run:
            result = run_trial(self.window, confirmed=True)
            self.assertEqual(result["status"], "unknown")
            self.assertIn("人工检查", describe(result))
            self.assertEqual(run.call_count, 1)

    def test_report_has_no_draft_or_window_identity(self):
        with tempfile.TemporaryDirectory() as root, patch("wechat_gallery_bot.management.draft_trial.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(experiment(Driver(), Monitor())).encode()
            path = Path(root) / "report.json"
            run_trial(self.window, confirmed=True, report_path=path)
            text = path.read_text(encoding="utf-8")
            for forbidden in ("Weixin", "hwnd", "同学群", "TIANYI_DRAFT_TEST_"):
                self.assertNotIn(forbidden, text)

    def test_native_source_has_only_direct_value_mutation(self):
        path = Path(__file__).parents[1] / "src/wechat_gallery_bot/management/draft_trial.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        banned = {"Click", "SendKeys", "SetFocus", "Invoke", "SetForegroundWindow", "ShowWindow", "SetCursorPos", "SendInput", "SendMessage", "PostMessage", "SetClipboardText", "SetClipboardFiles"}
        attributes = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertFalse(set(attributes) & banned)
        self.assertEqual(attributes.count("SetValue"), 1)


class DraftGuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = Window(10, 1, 100., "C:/Weixin.exe")
        self.locks = []
        self.gui = LocalOperationsWindow(self.root, lambda: self.window, LocalWorkspace(Path(self.tmp.name)), self.locks.append)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.pump()
        self.gui.close()
        self.tmp.cleanup()

    def pump(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if not self.gui.busy:
                return
            time.sleep(.01)
        self.fail("Tk operation timeout")

    def test_no_write_on_open_or_without_checkbox(self):
        with patch("wechat_gallery_bot.management.local_operations_gui.run_trial") as run:
            self.gui.draft_button.invoke()
            self.assertFalse(self.gui.busy)
            run.assert_not_called()
            self.assertEqual(self.locks, [])

    def test_explicit_button_does_not_enable_live_and_releases_binding_lock(self):
        with patch("wechat_gallery_bot.management.local_operations_gui.run_trial", return_value=experiment(Driver(), Monitor())) as run:
            self.gui.draft_confirmed.set(True)
            self.gui.draft_button.invoke()
            self.pump()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(self.locks, [True, False])
            self.assertFalse(self.gui.draft_confirmed.get())
            self.assertEqual(str(self.gui.live_button["state"]), "disabled")

    def test_worker_failure_releases_lock(self):
        with patch("wechat_gallery_bot.management.local_operations_gui.run_trial", side_effect=RuntimeError):
            self.gui.draft_confirmed.set(True)
            self.gui.draft_button.invoke()
            self.pump()
            self.assertEqual(self.locks, [True, False])
            self.assertFalse(self.gui.trial_active)


if __name__ == "__main__":
    unittest.main()
