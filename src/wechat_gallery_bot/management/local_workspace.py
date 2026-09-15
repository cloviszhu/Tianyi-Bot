"""Local gallery/configuration + explicit offline bot, never a live fallback."""
import queue
import threading
import uuid
from pathlib import Path

from PIL import Image

from ..adapters.window_scope import require_verified_background_backend
from ..models import MessageEvent
from ..storage.process_lock import data_directory_lock
from .backend import SimulatedBackend
from .common import ManagementError
from .controller import GuestController


class DemoBackend(SimulatedBackend):
    def __init__(self):
        super().__init__()
        self.events = queue.Queue(maxsize=32)
        self.completed = 0
        self.replies = 0
        self.images = 0
        self.stats_lock = threading.Lock()

    def run(self, adapter, handler, stop, candidate, guard, ready):
        ready()
        while not stop.is_set():
            guard()
            try:
                event = self.events.get(timeout=.1)
            except queue.Empty:
                continue
            if stop.is_set():
                break
            guard()
            handler(event)
            with self.stats_lock:
                self.completed += 1
                self.replies += sum(item[1] == "text" for item in adapter.outgoing)
                self.images += sum(item[1] == "image" for item in adapter.outgoing)
            adapter.outgoing.clear()

    def clear_pending(self):
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    def statistics(self):
        with self.stats_lock:
            return {"events": self.completed, "text_replies": self.replies, "image_replies": self.images}


class LocalWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = data_directory_lock(self.root)
        self.lock.__enter__()
        self.closed = False
        try:
            self.backend = DemoBackend()
            # Offline images can never enter the eventual live gallery.
            self.demo = GuestController(self.root / "offline-demo", self.backend)
            self.local = GuestController(self.root / "gallery", SimulatedBackend())
        except Exception:
            self.lock.__exit__(None, None, None)
            raise

    def configure(self, groups, seconds, megabytes):
        if self.closed:
            raise ManagementError("窗口已关闭。")
        return self.local.configure({"groups": groups, "pending_seconds": seconds, "max_image_mb": megabytes})

    def start_live(self):
        # Reject BEFORE importing a live library, reading chats, or creating workers.
        require_verified_background_backend()

    def start_demo(self):
        if self.closed:
            raise ManagementError("窗口已关闭。")
        self.demo._idle()
        self.backend.clear_pending()
        settings = self.local.settings
        self.demo.configure({"groups": ["离线演示群"], "pending_seconds": settings["pending_seconds"], "max_image_mb": settings["max_image_mb"]})
        state = self.demo.reconnect()
        state = self.demo.bind({"epoch": state["epoch"], "candidate_id": state["candidates"][0]["id"],
                                "label": "离线模拟，无真实账号", "confirmed": True})
        return self.demo.start({"binding_id": state["binding"]["id"], "config_revision": state["config_revision"], "confirmed": True})

    def scenario(self):
        if self.closed or self.demo.status()["state"] != "running":
            raise ManagementError("请先启动离线演示。")
        if not self.backend.events.empty():
            raise ManagementError("上一组演示尚未处理完。")
        source = self.root / "offline-demo" / "demo-input.png"
        if not source.exists():
            Image.new("RGB", (320, 200), "#218c87").save(source)
        batch = uuid.uuid4().hex
        events = [MessageEvent("离线演示群", "模拟用户", "text", text="/加图 演示图片", event_id=batch + "1"),
                  MessageEvent("离线演示群", "模拟用户", "image", local_file_path=source, event_id=batch + "2"),
                  MessageEvent("离线演示群", "模拟用户", "text", text="演示图片", event_id=batch + "3")]
        for event in events:
            self.backend.events.put_nowait(event)

    def stop_demo(self):
        return self.demo.stop()

    def status(self):
        return {"demo": self.demo.status()["state"], "error": self.demo.error, **self.backend.statistics()}

    def close(self):
        if self.closed:
            return True
        if not self.demo.close():
            return False
        self.local.close()
        self.closed = True
        self.lock.__exit__(None, None, None)
        return True
