import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Callable

from .adapters.base import Adapter, AdapterError
from .models import MessageEvent
from .services.command_parser import parse_command
from .services.gallery_service import GalleryService
from .services.pending_add_service import PendingAddService, PersistentPendingAddService

log = logging.getLogger(__name__)


class GalleryBot:
    def __init__(self, adapter: Adapter, gallery: GalleryService, pending: PendingAddService | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.adapter = adapter
        self.gallery = gallery
        self.pending = pending if pending is not None else PersistentPendingAddService(gallery.repository)
        self.clock = clock
        self._lock = threading.RLock()
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()

    def handle(self, event: MessageEvent) -> None:
        if event.is_self or not event.chat_key or not event.sender_key:
            return
        with self._lock:
            # Claim durably before any reply, download or gallery side effect.
            # A crash can leave 'processing': never automatically replay an
            # externally uncertain operation, even after restarting the process.
            if event.event_id:
                try:
                    if not self.gallery.repository.claim_event(event.chat_key, event.event_id):
                        return
                except sqlite3.Error:
                    log.warning("Event claim failed; no operation performed")
                    return
            self._reply_uncertain = False
            now = self.clock()
            while self._seen and next(iter(self._seen.values())) <= now - 300:
                self._seen.popitem(last=False)
            if event.event_id:
                key = (event.chat_key, event.event_id)
                if key in self._seen:
                    return
                self._seen[key] = now
                if len(self._seen) > 4096:
                    self._seen.popitem(last=False)
            try:
                self._handle(event)
                if event.event_id:
                    self.gallery.repository.finish_event(event.chat_key, event.event_id, uncertain=self._reply_uncertain)
            except (OSError, sqlite3.Error, AdapterError, ValueError) as exc:
                # No raw message, path, sender, or backend exception details in logs.
                log.warning("Message operation failed (%s)", type(exc).__name__)
                if event.event_id:
                    try:
                        self.gallery.repository.finish_event(event.chat_key, event.event_id, uncertain=True)
                    except sqlite3.Error:
                        pass  # the durable claim remains processing; replay blocked
                self._reply(event.chat_key, "处理失败，请稍后重试；加图请在有效期内重新发送图片，或重新发送 /加图 关键词。")

    def _reply(self, chat: str, text: str) -> None:
        try:
            self.adapter.send_text(chat, text)
        except (AdapterError, OSError) as exc:
            self._reply_uncertain = True
            log.warning("Reply failed (%s)", type(exc).__name__)

    def _handle(self, event: MessageEvent) -> None:
        chat, sender = event.chat_key, event.sender_key
        if event.message_type == "image":
            pending, expired = self.pending.inspect(chat, sender)
            if expired:
                self._reply(chat, "加图已超时，请重新发送 /加图 关键词。")
            if pending is None:
                return
            source = self.adapter.download_image(event)
            inserted, count = self.gallery.add(chat, pending.keyword, source, sender)
            self.pending.cancel(chat, sender)
            text = f"已添加「{pending.keyword}」，当前共 {count} 张。" if inserted else f"「{pending.keyword}」已有这张图片，当前共 {count} 张。"
            self._reply(chat, text)
            return
        if event.message_type != "text":
            return
        try:
            command = parse_command(event.text)
        except ValueError as exc:
            self._reply(chat, str(exc))
            return
        if command.kind == "add":
            self.pending.start(chat, sender, command.keyword)
            self._reply(chat, f"请在 {self.pending.timeout:g} 秒内发送一张图片，加入「{command.keyword}」。")
        elif command.kind == "cancel":
            cancelled = self.pending.cancel(chat, sender)
            self._reply(chat, "已取消加图。" if cancelled else "当前没有待添加的图片。")
        elif command.kind == "help":
            self._reply(chat, f"/加图 关键词：在 {self.pending.timeout:g} 秒内发送图片\n直接发送完整关键词：随机取图\n/取消：取消本次加图\n/帮助：查看说明")
        elif command.kind == "unknown":
            self._reply(chat, "暂不支持这个命令，发送 /帮助 查看用法。")
        elif command.kind == "lookup":
            image = self.gallery.choose(chat, command.keyword)
            if image is not None:
                self.adapter.send_image(chat, self.gallery.path_for(image))
                self.gallery.mark_sent(image)

    def run(self) -> None:
        self.adapter.run(self.handle)
