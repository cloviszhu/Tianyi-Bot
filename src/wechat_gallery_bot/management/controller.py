import hashlib
import io
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from PIL import Image

from ..adapters.fake import FakeAdapter
from ..adapters.wxauto_adapter import WxAutoAdapter
from ..app import GalleryBot
from ..config import Config
from ..services.gallery_service import GalleryService
from ..services.pending_add_service import PersistentPendingAddService
from ..storage.sqlite_repository import SQLiteRepository
from .common import ManagementError, atomic_json


class GuestController:
    def __init__(self, root: Path, backend):
        self.root, self.backend = root.resolve(), backend
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()
        self._closing = False
        self._last_guard_at = None
        self.state = "stopped"
        self.error = ""
        self.connection = "not_connected"
        self.candidates = []
        self.binding = None
        self.epoch = str(uuid.uuid4())
        self.config_path = self.root / "bot-config.json"
        self.settings = {"groups": [], "pending_seconds": 60, "max_image_mb": 20}
        if self.config_path.exists():
            self.settings = self._validated(json.loads(self.config_path.read_text(encoding="utf-8")))
        repository = SQLiteRepository(self.root / "bot.db")
        repository.close()

    def _validated(self, data):
        if not isinstance(data, dict) or set(data) != {"groups", "pending_seconds", "max_image_mb"}:
            raise ManagementError("配置只接受群列表、超时和图片限制。")
        try:
            parsed = Config.load(self.root / "unused.env", {"TIANYI_GROUPS": json.dumps(data["groups"]),
                                 "TIANYI_PENDING_SECONDS": str(data["pending_seconds"]), "TIANYI_MAX_IMAGE_MB": str(data["max_image_mb"])})
        except (ValueError, TypeError) as exc:
            raise ManagementError("配置无效：群名不可重复，超时1～600秒，图片1～100MB。") from exc
        return {"groups": list(parsed.groups), "pending_seconds": parsed.pending_seconds, "max_image_mb": parsed.max_image_bytes // (1024 * 1024)}

    def _idle(self):
        if self._closing:
            raise ManagementError("服务正在关闭，不能开始新操作。")
        if self._thread and self._thread.is_alive():
            raise ManagementError("请先停止机器人；停止完成后才能修改或重新绑定。")

    def configure(self, data):
        with self._lock:
            self._idle()
            settings = self._validated(data)
            atomic_json(self.config_path, settings)
            self.settings = settings
            return self.status()

    def reconnect(self):
        with self._lock:
            self._idle()
            self.binding = None
            self.candidates = []
            self.connection = "not_connected"
            self.epoch = str(uuid.uuid4())
            try:
                self.candidates = self.backend.discover()
                self.connection = "window_detected" if self.candidates else "no_window"
                self.error = "" if self.candidates else "没有找到已登录微信窗口，请在虚拟机控制台登录小号。"
            except Exception as exc:
                self.error = str(exc) if isinstance(exc, ManagementError) else "微信探测失败，请检查虚拟机桌面与免费后端安装。"
            return self.status()

    def bind(self, data):
        with self._lock:
            self._idle()
            if data.get("epoch") != self.epoch or data.get("confirmed") is not True:
                raise ManagementError("请刷新窗口列表，并明确确认控制台中登录的是小号。")
            candidate = next((c for c in self.candidates if c["id"] == data.get("candidate_id")), None)
            label = data.get("label", "").strip()
            if not candidate or not 1 <= len(label) <= 80:
                raise ManagementError("请选择窗口，并填写人工核对的小号备注。")
            self.backend.validate(candidate)
            self.binding = {"id": str(uuid.uuid4()), "label": label, "candidate": candidate}
            self.connection = "manually_confirmed"
            return self.status()

    def start(self, data):
        with self._lock:
            self._idle()
            if not self.backend.simulated:
                from ..adapters.window_scope import require_verified_background_backend
                require_verified_background_backend()
            if not self.binding or data.get("binding_id") != self.binding["id"] or data.get("confirmed") is not True:
                raise ManagementError("启动需要再次确认当前绑定的小号与监听群。")
            if data.get("config_revision") != self.config_revision():
                raise ManagementError("群配置已变化，请刷新后重新确认。")
            if not self.settings["groups"]:
                raise ManagementError("至少配置一个监听群。")
            self.backend.validate(self.binding["candidate"])
            self._stop = threading.Event()
            self._last_guard_at = time.time()
            self.state, self.error = "starting", ""
            binding, settings = dict(self.binding), dict(self.settings)
            self._thread = threading.Thread(target=self._run, args=(binding, settings), name="tianyi-bot", daemon=True)
            self._thread.start()
            return self.status()

    def _guard(self, binding):
        if self._stop.is_set():
            raise ManagementError("机器人正在停止。")
        with self._lock:
            now = time.time()
            previous = self._last_guard_at
            if previous is not None and (now - previous > 30 or now < previous - 5):
                raise ManagementError("运行检查中断过久或系统时钟跳变；请重新连接并确认小号。")
            self._last_guard_at = now
        self.backend.validate(binding["candidate"])

    def _run(self, binding, settings):
        repository = None
        try:
            repository = SQLiteRepository(self.root / "bot.db")
            adapter = FakeAdapter() if self.backend.simulated else WxAutoAdapter(tuple(settings["groups"]), self.root / "downloads", settings["max_image_mb"] * 1024 * 1024)
            bot = GalleryBot(adapter, GalleryService(repository, self.root / "images", settings["max_image_mb"] * 1024 * 1024), PersistentPendingAddService(repository, settings["pending_seconds"]))
            def ready():
                with self._lock:
                    if not self._stop.is_set():
                        self.state = "running"
            self.backend.run(adapter, bot.handle, self._stop, binding["candidate"], lambda: self._guard(binding), ready)
        except Exception as exc:
            with self._lock:
                if not self._stop.is_set():
                    self.error = str(exc) if isinstance(exc, ManagementError) else "微信运行异常；请检查桌面、窗口和版本后重新连接。"
                    self.binding = None
                    self.connection = "lost"
                    self.state = "error"
        finally:
            if repository:
                repository.close()
            with self._lock:
                if self.state != "error":
                    self.state = "stopped"

    def stop(self):
        with self._lock:
            self._stop.set()
            if self._thread and self._thread.is_alive():
                self.state = "stopping"
            elif self.state != "error":
                self.state = "stopped"
            return self.status()

    def close(self):
        with self._lock:
            self._closing = True
        self.stop()
        if self._thread:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                return False
        return True

    def status(self):
        with self._lock:
            if self.binding:
                try:
                    self.backend.validate(self.binding["candidate"])
                except Exception:
                    self._stop.set()
                    self.binding = None
                    self.connection = "lost"
                    self.error = "桌面或窗口已失效；请解锁后重新连接并确认小号。"
                    if self._thread and self._thread.is_alive():
                        self.state = "stopping"
            return {"service": "online", "simulated": self.backend.simulated, "state": self.state,
                    "connection": self.connection, "error": self.error, "epoch": self.epoch,
                    "binding": {k: v for k, v in self.binding.items() if k != "candidate"} if self.binding else None,
                    "identity_basis": "人工核对，免费库不能读取可靠账号ID",
                    "candidates": [{"id": c["id"], "title": c["title"], "pid": c["pid"]} for c in self.candidates],
                    "settings": dict(self.settings), "config_revision": self.config_revision(), "observed_at": time.time()}

    def config_revision(self):
        return hashlib.sha256(json.dumps(self.settings, sort_keys=True).encode()).hexdigest()

    def _read_db(self):
        connection = sqlite3.connect((self.root / "bot.db").as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def galleries(self):
        connection = self._read_db()
        try:
            return [dict(row) for row in connection.execute("SELECT namespace,keyword,COUNT(*) AS count,MIN(id) AS preview_id FROM images GROUP BY namespace,keyword ORDER BY namespace,keyword LIMIT 500")]
        finally:
            connection.close()

    def gallery_images(self, namespace, keyword):
        connection = self._read_db()
        try:
            return [dict(row) for row in connection.execute("SELECT id,created_at FROM images WHERE namespace=? AND keyword=? ORDER BY id LIMIT 200", (namespace, keyword))]
        finally:
            connection.close()

    def thumbnail(self, image_id: int) -> bytes:
        connection = self._read_db()
        try:
            row = connection.execute("SELECT local_path FROM images WHERE id=?", (image_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ManagementError("图片不存在。")
        root = (self.root / "images").resolve()
        path = (root / row["local_path"]).resolve()
        if not path.is_relative_to(root) or path == root or not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
            raise ManagementError("图片路径无效或文件已缺失。")
        with Image.open(path) as image:
            if image.width * image.height > 25_000_000:
                raise ManagementError("图片过大。")
            image.thumbnail((640, 480))
            output = io.BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=80)
            return output.getvalue()
