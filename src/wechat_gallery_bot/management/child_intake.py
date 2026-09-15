"""User-started, isolated no-send intake worker and bounded GUI supervisor."""
import json
from contextlib import ExitStack
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .common import ManagementError


class IntakeProcess:
    def __init__(self):
        self.process = None
        self.window = None
        self.message = "自动接收入库未启动；不会发送。"
        self.stopping_at = None
        self.started_at = None
        self.ready = False

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    def start(self, window, root, settings):
        if self.active:
            raise ManagementError("请先停止上一轮自动接收。")
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        payload = {"window": asdict(window), "root": str(root), "settings": settings}
        process = subprocess.Popen([str(executable), "-m", __name__, "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        self.process, self.window = process, window
        self.stopping_at, self.started_at, self.ready = None, time.monotonic(), False
        self.message = "正在检查分身隔离及免费后端兼容性；不发送。"
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
        except Exception:
            process.terminate()
            raise ManagementError("自动接收启动失败；未自动重试。") from None
        def read_status():
            try:
                for line in process.stdout:
                    if process is not self.process:
                        return
                    data = json.loads(line)
                    state = data.get("state")
                    if state == "ready":
                        self.ready = True
                        self.message = "自动接收已启动：收到加图指令后尝试读取原图入库；所有群回复均禁用。"
                    elif state == "handled":
                        self.message = "已交给图库处理 %d 条事件；入库结果请查看图库，不代表发送成功。" % int(data["count"])
                    elif state == "error":
                        self.message = "自动接收失败：隔离检查、窗口或免费库兼容性未通过。未发送；不自动重试。"
                    elif state == "stopped":
                        self.message = "自动接收已停止；未发送。"
            except (ValueError, OSError, KeyError):
                if process is self.process:
                    self.stop()
                    self.message = "自动接收状态通道异常，请停止后重新连接。"
            finally:
                process.stdout.close()
        threading.Thread(target=read_status, daemon=True).start()

    def stop(self):
        if self.active and self.stopping_at is None:
            self.stopping_at = time.monotonic()
            self.message = "正在停止自动接收……"
            # Closing the private pipe also stops the worker when the GUI exits.
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def poll(self, window):
        if self.active:
            if window != self.window or (not self.ready and time.monotonic() - self.started_at > 30):
                self.stop()
            if self.stopping_at is not None and time.monotonic() - self.stopping_at > 5:
                self.process.terminate()  # Only the worker created by this supervisor.
                self.message = "接收程序停止超时，已结束本次工作进程；未关闭微信或分身。"
        elif self.process is not None:
            if self.process.returncode and self.stopping_at is None:
                self.message = "自动接收工作进程异常退出；未自动重试。"
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        return self.message


def worker():
    from .window_binding import Window
    from .child_input import ChildInputGuard
    from ..adapters.child_adapter import ChildWxAutoAdapter
    from ..app import GalleryBot
    from ..config import Config
    from ..storage.sqlite_repository import SQLiteRepository
    from ..storage.process_lock import data_directory_lock
    from ..services.gallery_service import GalleryService
    from ..services.pending_add_service import PersistentPendingAddService
    stop = threading.Event()
    output = sys.stdout
    def emit(state, **fields):
        output.write(json.dumps({"state": state, **fields}) + "\n")
        output.flush()
    repository = None
    leases = ExitStack()
    try:
        data = json.loads(sys.stdin.readline(16384))
        window = Window(**data["window"])
        guard = ChildInputGuard(window, stop)
        guard()  # Before data writes or backend imports.
        root = Path(data["root"]).resolve()
        expected = (Path.home() / ".tianyi-bot/local-workspace/gallery").resolve()
        if root != expected:
            raise ManagementError("自动接收仅使用固定本机图库目录。")
        leases.enter_context(data_directory_lock(root))
        settings = data["settings"]
        config = Config.load(root / "unused.env", {
            "TIANYI_GROUPS": json.dumps(settings["groups"]),
            "TIANYI_PENDING_SECONDS": str(settings["pending_seconds"]),
            "TIANYI_MAX_IMAGE_MB": str(settings["max_image_mb"])})
        def wait_for_parent():
            try:
                sys.stdin.read(1)
            finally:
                stop.set()
        threading.Thread(target=wait_for_parent, daemon=True).start()
        import uiautomation as uia
        with uia.UIAutomationInitializerInThread():
            repository = SQLiteRepository(root / "bot.db")
            adapter = ChildWxAutoAdapter(config.groups, root / "downloads", config.max_image_bytes)
            bot = GalleryBot(adapter, GalleryService(repository, root / "images", config.max_image_bytes),
                             PersistentPendingAddService(repository, config.pending_seconds))
            count = 0
            def handle(event):
                nonlocal count
                guard()
                bot.handle(event)
                count += 1
                emit("handled", count=count)
            adapter.run(handle, window=window, stop_event=stop, on_ready=lambda: emit("ready"))
        emit("stopped")
    except Exception:
        emit("error")
    finally:
        if repository is not None:
            repository.close()
        leases.close()


if __name__ == "__main__" and "--worker" in sys.argv:
    worker()
