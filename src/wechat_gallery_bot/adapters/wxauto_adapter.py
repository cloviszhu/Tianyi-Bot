"""FreeWisdom/wxauto-4.0 integration, pinned and isolated from gallery logic."""
import importlib
import importlib.metadata
import contextlib
import io
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from ..models import MessageEvent
from .base import AdapterError

COMMIT = "bd7c5233e79c0a185638a325bf6d30607244dfa8"
REPOSITORY = "https://github.com/FreeWisdom/wxauto-4.0.git"
log = logging.getLogger(__name__)


def check_backend() -> str:
    """Read package metadata only; never import the UI backend here."""
    if sys.platform != "win32":
        raise AdapterError("真实微信后端需要 Windows。")
    try:
        distribution = importlib.metadata.distribution("wxauto4")
        origin = json.loads(distribution.read_text("direct_url.json") or "{}")
    except (importlib.metadata.PackageNotFoundError, ValueError) as exc:
        raise AdapterError('请先安装本项目的 ".[wechat]" 可选依赖。') from exc
    if origin.get("url", "").removesuffix(".git").lower() != REPOSITORY.removesuffix(".git").lower() or origin.get("vcs_info", {}).get("commit_id") != COMMIT:
        raise AdapterError("wxauto4 来源不匹配，请安装 README 指定的 FreeWisdom 固定提交。")
    return distribution.version


def _configure_backend(package, module, download_root: Path) -> None:
    # The pinned fork deletes client update files in __init__ and every poll.
    # Override only that imported helper; never modify the installed source/client.
    module.delete_update_files = lambda: None
    package.WxParam.ENABLE_FILE_LOGGER = False
    package.WxParam.ENABLE_SENDER_OCR = False
    package.WxParam.LISTENER_EXCUTOR_WORKERS = 1
    package.WxParam.DEFAULT_SAVE_PATH = str(download_root)
    logging.getLogger("wxauto4").disabled = True


class WxAutoAdapter:
    def __init__(self, groups: tuple[str, ...], download_root: Path, max_image_bytes: int = 20 * 1024 * 1024):
        if not groups:
            raise AdapterError("请配置需要监听的群名。")
        self.groups = frozenset(groups)
        self.download_root = download_root.resolve()
        self.max_image_bytes = max_image_bytes
        self._lock = threading.RLock()
        self._active: tuple[MessageEvent, object, object] | None = None
        self._downloaded: Path | None = None
        self._ready = False
        self._guard = None

    def _before_input(self):
        if self._guard is not None:
            self._guard()

    def _check_chat(self, chat) -> str:
        info = chat.ChatInfo()
        name = info.get("chat_name")
        if info.get("chat_type") != "group" or name not in self.groups or chat.who != name:
            raise AdapterError("聊天窗口与配置的群聊不匹配。")
        return name

    def _dispatch(self, message, chat, handler: Callable[[MessageEvent], None]) -> None:
        with self._lock:
            if not self._ready:
                return
            try:
                if message.attr != "friend" or message.type not in {"text", "image"}:
                    return
                if self._guard is not None:
                    self._guard()
                name = self._check_chat(chat)
                sender = message.sender
                if not isinstance(sender, str) or not sender.strip() or sender in {name, "friend", "system", "对方", "自己", "我"}:
                    log.warning("Skipped event: sender identity unavailable")
                    return
                event = MessageEvent(
                    chat_key=name, sender_key=sender, sender_name=sender,
                    message_type=message.type, text=message.content if message.type == "text" else "",
                    event_id=str(message.id) if message.id is not None else None,
                    timestamp=time.time(),
                )
                self._active = (event, message, chat)
                handler(event)
            except Exception as exc:
                log.warning("WeChat callback failed (%s)", type(exc).__name__)
            finally:
                self._active = None
                if self._downloaded is not None:
                    try:
                        self._downloaded.unlink(missing_ok=True)
                    except OSError:
                        log.warning("Temporary image cleanup failed")
                    self._downloaded = None

    def _context(self, chat_key: str):
        if self._guard is not None:
            self._guard()
        if self._active is None or self._active[0].chat_key != chat_key:
            raise AdapterError("No active group event")
        chat = self._active[2]
        self._check_chat(chat)
        return chat

    def send_text(self, chat_key: str, text: str) -> None:
        try:
            if not self._context(chat_key).SendMsg(msg=text):
                raise AdapterError("Text send failed")
        except Exception as exc:
            raise AdapterError("Text send failed") from exc

    def send_image(self, chat_key: str, path: Path) -> None:
        try:
            if not self._context(chat_key).SendFiles(filepath=str(path.resolve())):
                raise AdapterError("Image send failed")
        except Exception as exc:
            raise AdapterError("Image send failed") from exc

    def download_image(self, event: MessageEvent) -> Path:
        if self._active is None or self._active[0] is not event:
            raise AdapterError("Image message is no longer active")
        self._context(event.chat_key)
        try:
            self._downloaded = self._copy_preview(self._active[1])
            return self._downloaded
        except Exception as exc:
            raise AdapterError("Image download failed") from exc

    def _copy_preview(self, message) -> Path:
        # Verified against the pinned source's WeChatImage.save. Use its UI copy
        # primitives, but never its source-file deletion branch.
        from wxauto4.ui.component import Menu, WeChatImage
        from wxauto4.utils.lock import LockManager
        from wxauto4.utils.win32 import ReadClipboardData, SetClipboardText

        with LockManager.acquire():
            self._before_input()
            if not message.roll_into_view():
                raise AdapterError("Image is no longer visible")
            self._before_input()
            message.click()
            preview = WeChatImage(message)
            if not preview.control or not preview.control.Exists(0):
                raise AdapterError("Image preview unavailable")
            try:
                if preview.type != "image":
                    raise AdapterError("Not an image preview")
                self._before_input()
                SetClipboardText("")
                self._before_input()
                preview.tools["更多"].Click()
                self._before_input()
                if not Menu(preview.root).select("复制"):
                    raise AdapterError("Copy image failed")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    self._before_input()
                    files = ReadClipboardData().get("15", [])
                    if files:
                        source = Path(files[0])
                        # Bound the copy even if the source changes after stat.
                        with source.open("rb") as stream:
                            content = stream.read(self.max_image_bytes + 1)
                        if not content or len(content) > self.max_image_bytes:
                            raise AdapterError("Image is empty or too large")
                        with tempfile.NamedTemporaryFile(dir=self.download_root, suffix=".image", delete=False) as output:
                            output.write(content)
                            return Path(output.name)
                    time.sleep(0.1)
                raise AdapterError("Copy image timed out")
            finally:
                self._before_input()
                preview.control.SendKeys("{Esc}")

    def run(self, handler: Callable[[MessageEvent], None], *, stop_event=None, hwnd=None, guard=None, on_ready=None) -> None:
        check_backend()
        self.download_root.mkdir(parents=True, exist_ok=True)
        wx = None
        listening_started = False
        self._guard = guard
        try:
            self._before_input()
            with tempfile.TemporaryDirectory(prefix="session-", dir=self.download_root) as session:
                self.download_root = Path(session)
                package = importlib.import_module("wxauto4")
                module = importlib.import_module("wxauto4.wx")
                _configure_backend(package, module, self.download_root)
                if guard is not None:
                    guard()
                # The upstream constructor prints the selected window title.
                with contextlib.redirect_stdout(io.StringIO()):
                    wx = package.WeChat(debug=False, **({"hwnd": hwnd} if hwnd is not None else {}))
                try:
                    for group in sorted(self.groups):
                        if guard is not None:
                            guard()
                        listening_started = True  # AddListenChat starts the thread before finding the chat.
                        chat = wx.AddListenChat(nickname=group, callback=lambda msg, chat: self._dispatch(msg, chat, handler))
                        if not chat or self._check_chat(chat) != group:
                            raise AdapterError("无法监听配置的群聊。")
                    self._ready = True
                    if on_ready is not None:
                        on_ready()
                    if stop_event is None:
                        wx.KeepRunning()
                    else:
                        while not stop_event.wait(0.5):
                            if guard is not None:
                                guard()
                            if not getattr(wx, "_listener_thread", None) or not wx._listener_thread.is_alive() or not set(self.groups).issubset(wx.listen):
                                raise AdapterError("监听线程或群窗口已失效。")
                finally:
                    self._ready = False
                    if listening_started:
                        wx.StopListening(remove=False)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            raise AdapterError("微信连接失败，请核对固定版本、已登录状态和群名；当前客户端兼容性尚未实测。") from exc
