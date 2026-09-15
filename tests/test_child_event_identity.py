import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from wechat_gallery_bot.adapters.base import AdapterError
from wechat_gallery_bot.adapters.child_adapter import ChildWxAutoAdapter
from wechat_gallery_bot.management.window_binding import Window
from wechat_gallery_bot.storage.sqlite_repository import SQLiteRepository


class ChildEventIdentityTests(unittest.TestCase):
    def test_restart_same_wechat_deduplicates_but_new_process_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = Window(12, 30, 100.0, "C:/Weixin.exe")
            message = SimpleNamespace(id=[42, 1, 9])
            ids = []
            for target in (window, window, Window(12, 30, 200.0, "C:/Weixin.exe")):
                adapter = ChildWxAutoAdapter(("group",), root)
                adapter._set_event_origin(target)
                ids.append(adapter._event_id(message))
            self.assertEqual(ids[0], ids[1])
            self.assertNotEqual(ids[0], ids[2])
            repository = SQLiteRepository(root / "bot.db")
            self.assertTrue(repository.claim_event("group", ids[0]))
            repository.close()
            repository = SQLiteRepository(root / "bot.db")
            try:
                self.assertFalse(repository.claim_event("group", ids[1]))
                self.assertTrue(repository.claim_event("group", ids[2]))
                self.assertTrue(repository.claim_event("other-group", ids[0]))
            finally:
                repository.close()

    def test_repeated_text_with_distinct_controls_is_not_collapsed(self):
        adapter = ChildWxAutoAdapter(("group",), Path("unused"))
        adapter._set_event_origin(Window(1, 2, 3.0, "C:/Weixin.exe"))
        first = adapter._event_id(SimpleNamespace(id=[1, 2], content="1"))
        second = adapter._event_id(SimpleNamespace(id=[1, 3], content="1"))
        self.assertNotEqual(first, second)
        self.assertNotIn("Weixin", first)

    def test_missing_identity_fails_instead_of_untracked_side_effect(self):
        adapter = ChildWxAutoAdapter(("group",), Path("unused"))
        with self.assertRaises(AdapterError):
            adapter._event_id(SimpleNamespace(id=[1]))
        adapter._set_event_origin(Window(1, 2, 3.0, "C:/Weixin.exe"))
        with self.assertRaises(AdapterError):
            adapter._event_id(SimpleNamespace(id=None))
