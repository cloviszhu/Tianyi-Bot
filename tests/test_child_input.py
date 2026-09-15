import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wechat_gallery_bot.adapters.base import AdapterError
from wechat_gallery_bot.adapters.child_adapter import ChildWxAutoAdapter
from wechat_gallery_bot.management.child_input import ChildInputGuard
from wechat_gallery_bot.management.common import ManagementError
from wechat_gallery_bot.management.window_binding import Window
from wechat_gallery_bot.management.child_intake import IntakeProcess


class ChildInputTests(unittest.TestCase):
    def setUp(self):
        self.window = Window(50, 60, 1.0, "C:/Weixin.exe")
        self.stop = threading.Event()
        self.guard = ChildInputGuard(self.window, self.stop)
        self.gui = SimpleNamespace(IsWindow=lambda h: True, IsWindowVisible=lambda h: True,
            GetClassName=lambda h: "mmui::MainWindow", IsIconic=lambda h: False,
            EnumWindows=lambda fn, arg: fn(50, arg))
        self.modules = {"win32gui": self.gui,
            "win32process": SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 60)),
            "psutil": SimpleNamespace(Process=lambda p: SimpleNamespace(create_time=lambda: 1.0, exe=lambda: "C:/Weixin.exe"))}
        self.context = Mock(return_value=4)
        self.session = Mock(return_value=4)
        for p in (patch.dict("sys.modules", self.modules),
                  patch("wechat_gallery_bot.management.child_binding.require_child_context", self.context),
                  patch("wechat_gallery_bot.management.child_binding.process_session", self.session),
                  patch("wechat_gallery_bot.management.backend.desktop_active", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_child_and_isolation_lease_checked_twice(self):
        self.guard()
        self.assertEqual(self.guard.session, 4)
        self.assertEqual(self.context.call_count, 2)
        self.context.assert_called_with(input_enabled=True)

    def test_host_or_session_change_permanently_stops(self):
        self.guard()
        self.context.return_value = 5
        with self.assertRaises(ManagementError):
            self.guard()
        self.context.return_value = 4
        with self.assertRaises(ManagementError):
            self.guard()
        self.assertTrue(self.stop.is_set())

    def test_other_session_window_rejected(self):
        self.session.return_value = 1
        with self.assertRaises(ManagementError):
            self.guard()

    def test_multiple_windows_and_minimized_rejected(self):
        for multiple in (True, False):
            self.stop.clear()
            self.gui.EnumWindows = lambda fn, arg: [fn(h, arg) for h in ([50, 51] if multiple else [50])]
            self.gui.IsIconic = lambda h: not multiple
            with self.assertRaises(ManagementError):
                self.guard()

    def test_expired_lease_prevents_backend_import(self):
        self.context.side_effect = ManagementError("expired")
        with tempfile.TemporaryDirectory() as root:
            adapter = ChildWxAutoAdapter(("group",), Path(root))
            with patch("wechat_gallery_bot.adapters.wxauto_adapter.importlib.import_module") as load:
                with self.assertRaises(ManagementError):
                    adapter.run(Mock(), window=self.window, stop_event=self.stop)
            load.assert_not_called()

    def test_send_denied_even_with_valid_child(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = ChildWxAutoAdapter(("group",), Path(root))
            adapter._guard = self.guard
            for fn, value in ((adapter.send_text, "hi"), (adapter.send_image, Path(root) / "a.png")):
                with self.assertRaises(AdapterError):
                    fn("group", value)

    def test_copy_cannot_start_without_guard(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = ChildWxAutoAdapter(("group",), Path(root))
            with self.assertRaises(AdapterError):
                adapter._before_input()


class IntakeSupervisorTests(unittest.TestCase):
    def running(self):
        runner = IntakeProcess()
        runner.process = Mock()
        runner.process.poll.return_value = None
        runner.process.stdin = Mock()
        runner.started_at = 90
        runner.window = "child-window"
        return runner

    def test_stop_is_nonblocking_then_kills_only_owned_worker(self):
        runner = self.running()
        runner.ready = True
        with patch("wechat_gallery_bot.management.child_intake.time.monotonic", return_value=100):
            runner.stop()
        runner.process.stdin.close.assert_called_once()
        runner.process.terminate.assert_not_called()
        with patch("wechat_gallery_bot.management.child_intake.time.monotonic", return_value=106):
            runner.poll("child-window")
        runner.process.terminate.assert_called_once()

    def test_window_loss_and_start_timeout_request_stop(self):
        for window, now in ((None, 100), ("child-window", 125)):
            runner = self.running()
            with patch("wechat_gallery_bot.management.child_intake.time.monotonic", return_value=now):
                runner.poll(window)
            self.assertEqual(runner.stopping_at, now)
            runner.process.stdin.close.assert_called_once()

    def test_double_start_rejected_before_process_creation(self):
        runner = self.running()
        with patch("wechat_gallery_bot.management.child_intake.subprocess.Popen") as spawn:
            with self.assertRaises(ManagementError):
                runner.start(None, None, None)
        spawn.assert_not_called()

    def test_ready_process_without_recent_checks_is_stopped(self):
        runner = self.running()
        runner.ready = True
        runner.checked_at = 100
        with patch("wechat_gallery_bot.management.child_intake.time.monotonic", return_value=113):
            runner.poll("child-window")
        self.assertEqual(runner.stopping_at, 113)
        self.assertIn("12秒", runner.message)

    def test_idle_but_fresh_checks_remain_running(self):
        runner = self.running()
        runner.ready = True
        runner.checked_at = 112
        with patch("wechat_gallery_bot.management.child_intake.time.monotonic", return_value=113):
            runner.poll("child-window")
        self.assertIsNone(runner.stopping_at)

    def test_zero_exit_without_terminal_report_is_not_online(self):
        runner = self.running()
        runner.ready = True
        runner.process.poll.return_value = 0
        runner.process.returncode = 0
        self.assertIn("异常退出", runner.poll("child-window"))
        self.assertFalse(runner.ready)


if __name__ == "__main__":
    unittest.main()
