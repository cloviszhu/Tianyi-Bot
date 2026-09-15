import tempfile
import unittest
from pathlib import Path
from wechat_gallery_bot.storage.sqlite_repository import SQLiteRepository
from wechat_gallery_bot.services.gallery_service import GalleryService
from wechat_gallery_bot.app import GalleryBot
from wechat_gallery_bot.models import MessageEvent
from wechat_gallery_bot.adapters.base import AdapterError


class Replies:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
    def send_text(self, chat, text):
        self.calls += 1
        if self.fail:
            raise AdapterError("uncertain send")


class DurableEventsTests(unittest.TestCase):
    def test_restart_cannot_repeat_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = Replies()
            for _ in range(2):
                repo = SQLiteRepository(root / "bot.db")
                bot = GalleryBot(adapter, GalleryService(repo,root / "images"))
                bot.handle(MessageEvent("group","sender","text",text="/帮助",event_id="id1"))
                repo.close()
            self.assertEqual(adapter.calls,1)

    def test_uncertain_send_is_not_retried_and_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = SQLiteRepository(root / "bot.db")
            adapter = Replies(True)
            bot = GalleryBot(adapter,GalleryService(repo,root / "images"))
            event = MessageEvent("group","sender","text",text="/帮助",event_id="id1")
            bot.handle(event)
            bot.handle(event)
            self.assertEqual(adapter.calls,1)
            self.assertEqual(repo.processing_counts(),{"uncertain":1})
            repo.close()

    def test_atomic_claim_and_group_scope_and_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.db"
            a,b = SQLiteRepository(path),SQLiteRepository(path)
            self.assertTrue(a.claim_event("groupA","same"))
            self.assertFalse(b.claim_event("groupA","same"))
            self.assertTrue(b.claim_event("groupB","same"))
            a.close()
            a = SQLiteRepository(path)
            self.assertFalse(a.claim_event("groupA","same"))
            self.assertEqual(a.processing_counts(),{"processing":2})
            a.close()
            b.close()
