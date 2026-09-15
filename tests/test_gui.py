"""Real Tk widgets + real loopback TLS; WeChat is explicitly simulated."""
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wechat_gallery_bot.management.client import ManagementClient
from wechat_gallery_bot.management.gui import ManagerWindow
from wechat_gallery_bot.management.session import ServiceSession
from wechat_gallery_bot.services.gallery_service import GalleryService
from wechat_gallery_bot.storage.sqlite_repository import SQLiteRepository


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.session = ServiceSession(self.path, "127.0.0.1", 0, "127.0.0.1", simulated=True)
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = ManagerWindow(self.root, ManagementClient(self.session.pairing), self.path / "host.json")
        self.addCleanup(self.cleanup)
        self.pump(lambda: self.gui.online and not self.gui.busy)

    def cleanup(self):
        if not self.gui.closed:
            self.gui.close()
        self.session.close()
        self.tmp.cleanup()

    def pump(self, predicate, seconds=6):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Tk/TLS operation did not complete")

    def start_bot(self):
        self.gui.reconnect_button.invoke()
        self.pump(lambda: not self.gui.busy)
        self.assertEqual(self.gui.windows.current(), -1)
        self.gui.windows.current(0)
        self.gui.account_label.set("人工核对模拟小号")
        self.gui.confirmed.set(True)
        self.gui.bind_button.invoke()
        self.pump(lambda: not self.gui.busy)
        self.gui.groups.insert("1.0", "模拟测试群")
        self.gui.save_button.invoke()
        self.pump(lambda: not self.gui.busy)
        with patch("wechat_gallery_bot.management.gui.messagebox.askyesno", return_value=True):
            self.gui.start_button.invoke()
        self.pump(lambda: not self.gui.busy)
        self.gui.refresh()
        self.pump(lambda: not self.gui.busy and self.gui.latest["state"] == "running")

    def test_widgets_bind_config_start_stop(self):
        self.start_bot()
        self.assertIn("模拟", self.gui.banner.get())
        self.assertIn("人工核对模拟小号", self.gui.account_status.get())
        self.assertEqual(str(self.gui.save_button["state"]), "disabled")
        self.gui.stop_button.invoke()
        self.pump(lambda: not self.gui.busy)
        self.pump(lambda: self.session.controller.status()["state"] == "stopped")

    def test_close_gui_does_not_stop_and_new_window_reads_running(self):
        self.start_bot()
        self.gui.close()
        self.assertEqual(self.session.controller.status()["state"], "running")
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = ManagerWindow(self.root, ManagementClient(self.session.pairing), self.path / "host.json")
        self.pump(lambda: self.gui.online and not self.gui.busy)
        self.assertEqual(self.gui.latest["state"], "running")

    def test_gallery_real_photoimage_preview(self):
        source = self.path / "input.png"
        Image.new("RGB", (800, 600), "#258c9c").save(source)
        repository = SQLiteRepository(self.path / "bot.db")
        try:
            GalleryService(repository, self.path / "images").add("模拟群", "预览", source, "模拟用户")
        finally:
            repository.close()
        self.gui.refresh_galleries()
        self.pump(lambda: not self.gui.busy)
        self.gui.gallery_tree.selection_set("0")
        self.gui.select_gallery()
        self.pump(lambda: self.gui.preview_image is not None and not self.gui.busy)
        self.assertLessEqual(self.gui.preview_image.width(), 450)
        self.assertEqual(self.gui.gallery_rows["0"]["count"], 1)

    def test_service_disconnect_marks_unknown_without_freezing(self):
        self.session.close()
        self.gui.refresh()
        self.pump(lambda: not self.gui.busy)
        self.assertFalse(self.gui.online)
        self.assertIsNone(self.gui.latest)
        self.assertIn("未知", self.gui.account_status.get())
        self.assertEqual(str(self.gui.start_button["state"]), "disabled")

    def test_close_during_request_does_not_wait_or_retain_gui_callback(self):
        from unittest.mock import Mock
        entered, release = threading.Event(), threading.Event()
        callback = Mock()
        def request():
            entered.set()
            release.wait(3)
            return {"complete": True}
        self.gui.submit(request, callback)
        self.assertTrue(entered.wait(1))
        try:
            started = time.monotonic()
            self.gui.close()
            self.assertLess(time.monotonic() - started, .5)
            self.assertIsNone(self.gui.pending_callback)
            self.assertIsNone(self.gui.banner)
            callback.assert_not_called()
        finally:
            release.set()
            self.gui.executor.shutdown(wait=True)
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
