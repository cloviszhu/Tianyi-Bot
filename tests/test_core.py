import random
import sqlite3
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wechat_gallery_bot.adapters.fake import FakeAdapter
from wechat_gallery_bot.adapters.base import AdapterError
from wechat_gallery_bot.app import GalleryBot
from wechat_gallery_bot.models import MessageEvent
from wechat_gallery_bot.services.command_parser import normalize_keyword, parse_command
from wechat_gallery_bot.services.gallery_service import GalleryService, InvalidImage
from wechat_gallery_bot.services.pending_add_service import PendingAddService
from wechat_gallery_bot.storage.sqlite_repository import SQLiteRepository


class ParserTests(unittest.TestCase):
    def test_commands_and_whitespace(self):
        self.assertEqual(parse_command("  /加图   帕姆王  ").keyword, "帕姆王")
        self.assertEqual(parse_command("/加图\t帕姆 王").keyword, "帕姆 王")
        for command, kind in [("/帮助", "help"), ("/取消", "cancel"), ("/加图x", "unknown"), ("", "ignore"), (" 帕姆王 ", "lookup")]:
            self.assertEqual(parse_command(command).kind, kind)
        with self.assertRaises(ValueError):
            parse_command("/加图")

    def test_invalid_keywords(self):
        for value in ["", "../escape", "..", "C:\\secret", "a/b", "a\x00b", "a\nb", "x" * 81, "/帮助", "a|b"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_keyword(value)

    def test_exact_not_casefold_or_substring(self):
        self.assertEqual(parse_command("Ab c").keyword, "Ab c")
        self.assertNotEqual(parse_command("abc").keyword, "Ab c")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        self.second = self.root / "second.png"
        Image.new("RGB", (8, 8), "red").save(self.source)
        Image.new("RGB", (8, 8), "blue").save(self.second)
        self.repo = SQLiteRepository(self.root / "bot.db")
        self.addCleanup(lambda: self.repo.close())
        self.adapter = FakeAdapter()
        self.now = 100.0
        self.gallery = GalleryService(self.repo, self.root / "images", rng=random.Random(7))
        self.pending = PendingAddService(clock=lambda: self.now)
        self.bot = GalleryBot(self.adapter, self.gallery, self.pending, clock=lambda: self.now)

    def text(self, text, sender="A", chat="group", **kwargs):
        self.bot.handle(MessageEvent(chat, sender, "text", text, **kwargs))

    def image(self, sender="A", chat="group", source=None, **kwargs):
        self.bot.handle(MessageEvent(chat, sender, "image", local_file_path=source or self.source, **kwargs))

    def add(self, keyword="帕姆王", **kwargs):
        self.text("/加图 " + keyword, **kwargs)
        self.image(**kwargs)

    def test_requested_scenario(self):
        self.text("/加图 帕姆王")
        self.image(sender="B")
        self.assertEqual(self.adapter.downloads, 0)
        self.image()
        self.text("帕姆王", sender="B")
        self.assertEqual(self.adapter.downloads, 1)
        self.assertEqual(len(self.repo.images("group", "帕姆王")), 1)
        self.assertEqual(self.adapter.outgoing[-1][1], "image")
        self.assertIn("当前共 1 张", self.adapter.outgoing[1][2])

    def test_pending_chat_and_sender_isolation(self):
        self.text("/加图 one", chat="g1")
        self.text("/加图 two", sender="B", chat="g1")
        self.image(chat="g2")
        self.image(sender="B", chat="g1")
        self.image(chat="g1")
        self.assertEqual(len(self.repo.images("g1", "one")), 1)
        self.assertEqual(len(self.repo.images("g1", "two")), 1)
        self.assertEqual(self.repo.images("g2", "one"), [])

    def test_timeout_exact_boundary(self):
        self.text("/加图 one")
        self.now += 60
        self.image()
        self.assertEqual(self.adapter.downloads, 0)
        self.assertIn("超时", self.adapter.outgoing[-1][2])

    def test_before_timeout_and_unrelated_text(self):
        self.text("/加图 one")
        self.text("ordinary unrelated text")
        self.now += 59.999
        self.image()
        self.assertEqual(len(self.repo.images("group", "one")), 1)

    def test_second_add_replaces_only_own_pending(self):
        self.text("/加图 old")
        self.text("/加图 new")
        self.image()
        self.assertEqual(self.repo.images("group", "old"), [])
        self.assertEqual(len(self.repo.images("group", "new")), 1)

    def test_cancel_isolation_and_help(self):
        self.text("/加图 one")
        self.text("/取消", sender="B")
        self.assertIsNotNone(self.pending.inspect("group", "A")[0])
        self.text("/取消")
        self.image()
        self.assertEqual(self.adapter.downloads, 0)
        self.text("/帮助")
        self.assertIn("/加图", self.adapter.outgoing[-1][2])

    def test_duplicate_hash_per_gallery(self):
        self.add()
        self.add()
        self.assertEqual(len(self.repo.images("group", "帕姆王")), 1)
        self.assertIn("已有这张图片", self.adapter.outgoing[-1][2])
        self.add("another")
        self.add(chat="other")
        self.assertEqual(len(list((self.root / "images").iterdir())), 1)
        self.assertEqual(len(self.repo.images("other", "帕姆王")), 1)

    def test_exact_gallery_lookup(self):
        self.add()
        count = len(self.adapter.outgoing)
        for text in ["帕姆", "帕姆王啊", "请发帕姆王"]:
            self.text(text)
        self.text("帕姆王", chat="other")
        self.assertEqual(len(self.adapter.outgoing), count)
        self.text("  帕姆王  ")
        self.assertEqual(self.adapter.outgoing[-1][1], "image")

    def test_no_immediate_repeat_and_random_candidates(self):
        self.add()
        self.text("/加图 帕姆王")
        self.image(source=self.second)
        seen = []
        for _ in range(8):
            self.text("帕姆王")
            seen.append(self.adapter.outgoing[-1][2])
        self.assertEqual(len(set(seen)), 2)
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))

    def test_self_ignored_including_image(self):
        self.text("/加图 one", is_self=True)
        self.assertEqual(self.adapter.outgoing, [])
        self.text("/加图 one")
        self.image(is_self=True)
        self.assertEqual(self.adapter.downloads, 0)

    def test_event_dedup_and_identical_text_new_ids(self):
        self.add()
        self.text("帕姆王", event_id="same")
        count = len(self.adapter.outgoing)
        self.text("帕姆王", event_id="same")
        self.assertEqual(len(self.adapter.outgoing), count)
        self.text("帕姆王", event_id="different")
        self.assertEqual(len(self.adapter.outgoing), count + 1)

    def test_dedup_does_not_extend_or_rearm_pending(self):
        self.text("/加图 one", event_id="cmd")
        self.image(event_id="image")
        self.text("/加图 one", event_id="cmd")
        self.image(event_id="image")
        self.assertIsNone(self.pending.inspect("group", "A")[0])
        self.assertEqual(self.adapter.downloads, 1)

    def test_invalid_bytes_and_failure_retry(self):
        self.text("/加图 one")
        bad = self.root / "not-image.png"
        bad.write_bytes(b"bad image")
        self.image(source=bad)
        self.assertIn("处理失败", self.adapter.outgoing[-1][2])
        self.assertIsNotNone(self.pending.inspect("group", "A")[0])
        self.image()
        self.assertEqual(len(self.repo.images("group", "one")), 1)

    def test_download_failure_keeps_pending(self):
        self.text("/加图 one")
        with patch.object(self.adapter, "download_image", side_effect=AdapterError("failure")):
            self.image()
        self.assertIsNotNone(self.pending.inspect("group", "A")[0])
        self.image()
        self.assertEqual(len(self.repo.images("group", "one")), 1)

    def test_path_traversal_in_keyword_and_metadata(self):
        self.text("/加图 ../escaped")
        self.image()
        self.assertEqual(self.adapter.downloads, 0)
        self.add()
        record = self.repo.images("group", "帕姆王")[0]
        with self.assertRaises(InvalidImage):
            self.gallery.path_for(replace(record, local_path="../source.png"))
        with self.assertRaises(InvalidImage):
            self.gallery.path_for(replace(record, local_path=str(self.source)))

    def test_database_failure_leaves_no_broken_row(self):
        with patch.object(self.repo, "insert", side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                self.gallery.add("group", "one", self.source, "A")
        self.assertEqual(self.repo.images("group", "one"), [])
        self.assertEqual(len(list((self.root / "images").glob(".image-*"))), 0)
        self.gallery.add("group", "one", self.source, "A")
        self.assertTrue(self.gallery.path_for(self.repo.images("group", "one")[0]).is_file())

    def test_atomic_rename_failure_leaves_no_row_or_temporary(self):
        with patch("wechat_gallery_bot.services.gallery_service.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.gallery.add("group", "one", self.source, "A")
        self.assertEqual(self.repo.images("group", "one"), [])
        self.assertEqual(list((self.root / "images").iterdir()), [])

    def test_failed_send_does_not_mark_last_sent(self):
        self.add()
        with patch.object(self.adapter, "send_image", side_effect=AdapterError("no")):
            self.text("帕姆王")
        self.assertIsNone(self.repo.last_sent("group", "帕姆王"))

    def test_persistence_after_reopen(self):
        self.add()
        self.text("帕姆王")
        last = self.repo.last_sent("group", "帕姆王")
        self.repo.close()
        self.repo = SQLiteRepository(self.root / "bot.db")
        gallery = GalleryService(self.repo, self.root / "images")
        record = gallery.choose("group", "帕姆王")
        self.assertIsNotNone(record)
        self.assertTrue(gallery.path_for(record).is_file())
        self.assertEqual(self.repo.last_sent("group", "帕姆王"), last)

    def test_missing_image_friendly_error(self):
        self.add()
        image = self.repo.images("group", "帕姆王")[0]
        self.gallery.path_for(image).unlink()
        self.text("帕姆王")
        self.assertIn("处理失败", self.adapter.outgoing[-1][2])

    def test_size_limit(self):
        self.gallery.max_image_bytes = 1
        with self.assertRaises(InvalidImage):
            self.gallery.add("group", "one", self.source, "A")

    def test_backup_restore_to_new_directory(self):
        self.add()
        self.repo.close()
        restored = self.root / "restored"
        restored.mkdir()
        shutil.copy2(self.root / "bot.db", restored / "bot.db")
        shutil.copytree(self.root / "images", restored / "images")
        self.repo = SQLiteRepository(restored / "bot.db")
        recovered = GalleryService(self.repo, restored / "images")
        record = recovered.choose("group", "帕姆王")
        self.assertTrue(recovered.path_for(record).is_file())
        self.assertEqual(recovered.path_for(record).read_bytes(), self.source.read_bytes())

    def test_concurrent_duplicate_callbacks(self):
        self.text("/加图 one")
        event = MessageEvent("group", "A", "image", local_file_path=self.source, event_id="dup")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.bot.handle, [event] * 12))
        self.assertEqual(self.adapter.downloads, 1)
        self.assertEqual(len(self.repo.images("group", "one")), 1)


if __name__ == "__main__":
    unittest.main()
