"""User-started, isolated no-send intake worker and bounded GUI supervisor."""
import json
from contextlib import ExitStack, redirect_stdout
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .common import ManagementError

def code_locations(tb):
    """Bounded package code coordinates only, no locals, source or full paths."""
    locations = []
    while tb is not None and len(locations) < 24:
        module = tb.tb_frame.f_globals.get("__name__", "")
        if isinstance(module, str) and any(module == prefix or module.startswith(prefix + ".")
                for prefix in ("wechat_gallery_bot", "wxauto4", "uiautomation")):
            # Package module names are source identifiers, not window metadata.
            if len(module) <= 160 and all(c.isascii() and (c.isalnum() or c in "._") for c in module):
                locations.append({"module": module, "line": tb.tb_lineno})
        tb = tb.tb_next
    return locations

STARTUP_STAGES = {
    "request": "读取启动参数", "isolation": "分身输入隔离检查",
    "data_path": "核对图库目录", "data_lock": "获取图库运行锁",
    "configuration": "解析群配置", "uia": "初始化UIA",
    "database": "打开图库数据库", "backend_metadata": "检查免费库版本来源",
    "backend_import": "加载免费库依赖", "wechat_constructor": "连接微信控件",
    "group_listener": "打开并监听配置群", "listening": "监听运行",
}


def failure_record(stage, exc):
    # Exception messages/tracebacks may contain chats and paths. Keep only
    # allowlisted type labels, never arbitrary class names or repr/str(exc).
    from .child_input import GUARD_STEPS
    kinds, visited, locations = [], set(), []
    guard_step = None
    lease_reason = None
    window_probe = []
    while exc is not None and id(exc) not in visited and len(kinds) < 5:
        candidate = getattr(exc, "guard_step", None)
        from .child_binding import LEASE_REASONS
        reason = getattr(exc, "lease_reason", None)
        if isinstance(reason, str) and reason in LEASE_REASONS:
            lease_reason = reason
        if isinstance(candidate, str) and candidate in GUARD_STEPS:
            guard_step = candidate
        visited.add(id(exc))
        probe = getattr(exc, "window_probe", None)
        if isinstance(probe, list):
            for item in probe[:24]:
                if isinstance(item, dict) and item.get("class") in {"mmui::MainWindow", "mmui::FramelessMainWindow", "mmui::ChatMessagePage", "other"}:
                    window_probe.append({"class": item["class"], "title_match": item.get("title_match") is True,
                                         "same_pid": item.get("same_pid") is True})
        locations.extend(code_locations(exc.__traceback__))
        name = type(exc).__name__
        kinds.append(name if name in {"ManagementError", "AdapterError", "ValueError",
            "TypeError", "AttributeError", "KeyError", "ImportError", "ModuleNotFoundError",
            "PermissionError", "FileNotFoundError", "OSError", "RuntimeError",
            "OperationalError", "COMError", "TimeoutError"} else "Exception")
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    record = {"stage": stage if stage in STARTUP_STAGES else "request", "kinds": kinds}
    if guard_step is not None:
        record["guard_step"] = guard_step
    if lease_reason is not None:
        record["lease_reason"] = lease_reason
    if locations:
        record["locations"] = locations[-24:]
    if window_probe:
        record["window_probe"] = window_probe[:24]
    return record


def failure_message(record):
    from .child_input import GUARD_STEPS
    detail = GUARD_STEPS.get(record.get("guard_step"))
    return "运行失败：%s；错误类型 %s。未自动重试，具体收发结果需核对。" % (
        STARTUP_STAGES.get(record.get("stage"), "未知步骤") + (" / " + detail if detail else ""),
        " → ".join(record.get("kinds", [])) or "未知")

ISSUE_LABELS = {
    "sender_unavailable": "发送者不可识别，已跳过；连续图片可能因此无法入库",
    "message_metadata": "消息类型读取失败",
    "isolation": "分身隔离检查失败",
    "chat_identity": "群窗口身份检查失败",
    "sender_identity": "发送者读取失败",
    "event_identity": "消息去重标识读取失败",
    "handler": "图库回调失败",
}


def issue_message(code, count):
    return "接收异常累计 %d 次：%s。不能据此认为收发正常。" % (
        count, ISSUE_LABELS.get(code, "未知接收异常"))


class IntakeProcess:
    def __init__(self):
        self.process = None
        self.window = None
        self.message = "自动接收入库未启动；不会发送。"
        self.stopping_at = None
        self.started_at = None
        self.ready = False
        self.checked_at = None
        self.terminal = False
        self.release = "installed"

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    def start(self, window, root, settings, *, send_confirmed=False):
        if self.active:
            raise ManagementError("请先停止上一轮自动接收。")
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        payload = {"window": asdict(window), "root": str(root), "settings": settings,
                   "send_confirmed": send_confirmed is True}
        from .worker_release import WorkerReleases
        command, release = WorkerReleases().command(executable)
        process = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        self.process, self.window = process, window
        self.release = release
        self.stopping_at, self.started_at, self.ready = None, time.monotonic(), False
        self.checked_at, self.terminal = None, False
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
                        self.message = "机器人已启动：本轮已授权配置群收发。" if send_confirmed is True else "自动接收已启动：收到加图指令后尝试读取原图入库；所有群回复均禁用。"
                    elif state == "checked":
                        self.checked_at = time.monotonic()
                    elif state == "handled":
                        self.message = "已交给图库处理 %d 条事件；%s；不代表全部处理成功。" % (int(data["count"]), "本轮允许群收发" if send_confirmed is True else "群发送禁用")
                    elif state == "issue":
                        self.message = issue_message(data.get("code"), int(data["count"]))
                    elif state == "error":
                        self.terminal = True
                        self.message = failure_message(data)
                    elif state == "stopped":
                        self.terminal = True
                        self.message = "机器人已停止；本轮发送授权已结束。" if send_confirmed is True else "自动接收已停止；未发送。"
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
            if self.ready and time.monotonic() - (self.checked_at if self.checked_at is not None else self.started_at) > 12:
                self.stop()
                self.message = "分身运行检查超过12秒未更新，正在停止；不能确认机器人仍正常。"
            if self.stopping_at is not None and time.monotonic() - self.stopping_at > 5:
                self.process.terminate()  # Only the worker created by this supervisor.
                self.message = "接收程序停止超时，已结束本次工作进程；未关闭微信或分身。"
        elif self.process is not None:
            if not self.terminal and self.stopping_at is None:
                self.message = "自动接收工作进程异常退出；未自动重试。"
            self.ready = False
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        return self.message


def worker():
    # Reserve stdout exclusively for the supervisor protocol. wxauto prints
    # account-bearing startup text; discard it via the worker's stderr sink.
    output = sys.stdout
    with redirect_stdout(sys.stderr):
        _worker(output)


def _worker(output):
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
    output_lock = threading.Lock()
    def emit(state, **fields):
        with output_lock:
            output.write(json.dumps({"state": state, **fields}) + "\n")
            output.flush()
    repository = None
    leases = ExitStack()
    stage = "request"
    def set_stage(code):
        nonlocal stage
        stage = code if code in STARTUP_STAGES else "request"
    try:
        data = json.loads(sys.stdin.readline(16384))
        window = Window(**data["window"])
        guard = ChildInputGuard(window, stop)
        set_stage("isolation")
        guard()  # Before data writes or backend imports.
        set_stage("data_path")
        root = Path(data["root"]).resolve()
        expected = (Path.home() / ".tianyi-bot/local-workspace/gallery").resolve()
        if root != expected:
            raise ManagementError("自动接收仅使用固定本机图库目录。")
        set_stage("data_lock")
        leases.enter_context(data_directory_lock(root))
        set_stage("configuration")
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
        set_stage("uia")
        import uiautomation as uia
        with uia.UIAutomationInitializerInThread():
            set_stage("database")
            repository = SQLiteRepository(root / "bot.db")
            adapter = ChildWxAutoAdapter(config.groups, root / "downloads", config.max_image_bytes)
            adapter.on_stage = set_stage
            issue_count = 0
            def issue(code):
                nonlocal issue_count
                issue_count += 1
                emit("issue", code=code if code in ISSUE_LABELS else "unknown", count=issue_count)
            adapter.on_issue = issue
            bot = GalleryBot(adapter, GalleryService(repository, root / "images", config.max_image_bytes),
                             PersistentPendingAddService(repository, config.pending_seconds))
            count = 0
            def handle(event):
                nonlocal count
                guard()
                bot.handle(event)
                count += 1
                emit("handled", count=count)
            last_check = [0.0]
            def checked():
                now = time.monotonic()
                if now - last_check[0] >= 1:
                    last_check[0] = now
                    emit("checked")
            set_stage("isolation")
            adapter.run(handle, window=window, stop_event=stop, on_ready=lambda: emit("ready"), on_checked=checked,
                        send_groups=config.groups if data.get("send_confirmed") is True else ())
        emit("stopped")
    except Exception as exc:
        record = failure_record(stage, exc)
        # Fixed local diagnostic, no chat/account data. The failed worker owns
        # this small report; no main desktop or WeChat interaction is required.
        try:
            report = Path.home() / ".tianyi-bot" / "intake-failure.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({**record, "recorded_at": time.time()}), encoding="utf-8")
        except OSError:
            pass
        emit("error", **record)
    finally:
        if repository is not None:
            repository.close()
        leases.close()


if __name__ == "__main__" and "--worker" in sys.argv:
    worker()
