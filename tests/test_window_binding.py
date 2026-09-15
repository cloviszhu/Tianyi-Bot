"""Synthetic windows + real Tk. No real WeChat account is accessed."""
import time
import tkinter as tk
import unittest
from dataclasses import replace
from unittest.mock import patch

from wechat_gallery_bot.management.common import ManagementError
from wechat_gallery_bot.management.local_gui import LocalBindingWindow
from wechat_gallery_bot.management.window_binding import BindingService, NativeProvider, Window


class Provider:
    def __init__(self):
        self.windows = [Window(100, 10, 1000., "C:/Weixin.exe"), Window(200, 20, 2000., "C:/Weixin.exe")]
        self.failed = False

    def scan(self):
        if self.failed:
            raise OSError("synthetic lock / unavailable desktop")
        return self.windows.copy()


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()
        self.now = 10
        self.service = BindingService(self.provider, lambda: self.now)
        self.generation, self.rows = self.service.refresh()

    def bind(self):
        return self.service.bind(self.generation, self.rows[1].key, True)

    def test_requires_manual_confirmation(self):
        with self.assertRaises(ManagementError):
            self.service.bind(self.generation, self.rows[1].key, False)
        self.assertIsNone(self.service.bound)

    def test_selects_second_not_first_window(self):
        self.assertEqual(self.bind().hwnd, 200)
        self.assertEqual(self.service.check().hwnd, 200)

    def test_stale_generation_rejected(self):
        self.service.refresh()
        with self.assertRaises(ManagementError):
            self.bind()

    def test_same_process_main_windows_rejected(self):
        self.provider.windows.append(replace(self.rows[1], hwnd=201))
        with self.assertRaises(ManagementError):
            self.bind()

    def test_changes_invalidate_without_fallback(self):
        for change in ({"hwnd": 201}, {"pid": 21}, {"created": 2001.}, {"executable": "C:/other.exe"}, {"minimized": True}, {"owner": 5}):
            with self.subTest(change=change):
                self.provider.windows = self.rows.copy()
                self.bind()
                self.provider.windows[1] = replace(self.rows[1], **change)
                with self.assertRaises(ManagementError):
                    self.service.check()
                self.assertIsNone(self.service.bound)

    def test_closed_window_does_not_select_other(self):
        self.bind()
        self.provider.windows.pop()
        with self.assertRaises(ManagementError):
            self.service.check()
        self.assertIsNone(self.service.bound)

    def test_scan_failure_clears_old_binding(self):
        self.bind()
        self.provider.failed = True
        with self.assertRaises(OSError):
            self.service.check()
        self.assertIsNone(self.service.bound)

    def test_long_gap_requires_reconfirmation(self):
        self.bind()
        self.now += 20
        with self.assertRaises(ManagementError):
            self.service.check()
        self.assertIsNone(self.service.bound)

    def test_refresh_clears_binding_even_if_scan_fails(self):
        self.bind()
        self.provider.failed = True
        with self.assertRaises(OSError):
            self.service.refresh()
        self.assertIsNone(self.service.bound)
        self.assertEqual(self.service.candidates, [])

    def test_native_provider_bounded_and_no_shell(self):
        with patch("wechat_gallery_bot.management.window_binding.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"[]"
            self.assertEqual(NativeProvider().scan(), [])
            self.assertEqual(run.call_args.kwargs["timeout"], 8)
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_windowless_launcher_uses_hidden_console_worker(self):
        with patch("wechat_gallery_bot.management.window_binding.sys.executable", "C:/runtime/pythonw.exe"), patch("wechat_gallery_bot.management.window_binding.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"[]"
            NativeProvider().scan()
            self.assertTrue(run.call_args.args[0][0].endswith("python.exe"))


class LocalGuiTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = LocalBindingWindow(self.root, BindingService(self.provider))
        self.addCleanup(self.gui.close)

    def pump(self):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            self.root.update()
            if not self.gui.busy:
                return
            time.sleep(.01)
        self.fail("GUI operation timed out")

    def scan(self):
        self.gui.scan_button.invoke()
        self.pump()

    def test_no_automatic_scan_or_selection(self):
        self.assertEqual(self.gui.rows, [])
        self.scan()
        self.assertEqual(self.gui.windows.current(), -1)
        self.assertIsNone(self.gui.bound)

    def test_child_unique_window_connects_without_account_confirmation(self):
        self.gui.child_mode = True
        self.provider.windows = self.provider.windows[:1]
        self.scan()
        self.assertIsNotNone(self.gui.bound)
        self.assertFalse(self.gui.confirmed.get())
        self.assertIn("已连接分身微信", self.gui.status.get())

    def test_child_multiple_windows_not_selected(self):
        self.gui.child_mode = True
        self.scan()
        self.assertIsNone(self.gui.bound)
        self.assertIn("多个", self.gui.status.get())

    def test_foreground_selection_and_confirmation(self):
        self.scan()
        self.gui.select_foreground(200)
        self.assertEqual(self.gui.windows.current(), 1)
        self.gui.bind_button.invoke()
        self.assertIsNone(self.gui.bound)
        self.gui.confirmed.set(True)
        self.gui.bind_button.invoke()
        self.pump()
        self.assertEqual(self.gui.bound.hwnd, 200)
        self.assertIn("未监听", self.gui.status.get())
        self.assertFalse(hasattr(self.gui, "start_button"))

    def test_unknown_foreground_cannot_select_first(self):
        self.scan()
        self.gui.select_foreground(999)
        self.assertEqual(self.gui.windows.current(), -1)

    def test_selection_change_clears_confirmation_and_binding(self):
        self.test_foreground_selection_and_confirmation()
        self.gui.windows.current(0)
        self.gui.selection_changed()
        self.pump()
        self.assertFalse(self.gui.confirmed.get())
        self.assertIsNone(self.gui.service.bound)

    def test_detection_failure_visible_and_unbound(self):
        self.test_foreground_selection_and_confirmation()
        self.provider.failed = True
        self.gui.poll()
        self.pump()
        self.assertIsNone(self.gui.bound)
        self.assertFalse(self.gui.confirmed.get())
        self.assertIn("取消", self.gui.status.get())


if __name__ == "__main__":
    unittest.main()
