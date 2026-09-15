import tempfile
import unittest
from pathlib import Path
from PIL import Image
from wechat_gallery_bot.services.pending_add_service import PersistentPendingAddService
from wechat_gallery_bot.storage.sqlite_repository import SQLiteRepository
from wechat_gallery_bot.services.gallery_service import GalleryService
from wechat_gallery_bot.app import GalleryBot
from wechat_gallery_bot.adapters.fake import FakeAdapter
from wechat_gallery_bot.models import MessageEvent


class PendingRecoveryTests(unittest.TestCase):
    def test_restart_and_sender_isolation_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.png'
            Image.new('RGB',(10,10),'red').save(source)
            adapter = FakeAdapter()
            repo = SQLiteRepository(root/'bot.db')
            GalleryBot(adapter,GalleryService(repo,root/'images')).handle(MessageEvent('g','a','text',text='/加图 猫',event_id='1'))
            repo.close()
            repo = SQLiteRepository(root/'bot.db')
            bot = GalleryBot(adapter,GalleryService(repo,root/'images'))
            bot.handle(MessageEvent('g','b','image',local_file_path=source,event_id='2'))
            self.assertEqual(adapter.downloads,0)
            bot.handle(MessageEvent('g','a','image',local_file_path=source,event_id='3'))
            bot.handle(MessageEvent('g','b','text',text='猫',event_id='4'))
            self.assertEqual(adapter.outgoing[-1][1],'image')
            self.assertEqual(len(repo.images('g','猫')),1)
            self.assertEqual(bot.pending.inspect('g','a'),(None,False))
            repo.close()

    def test_expiry_cancel_replace_and_clock_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteRepository(Path(directory)/'bot.db')
            now = [100.0]
            pending = PersistentPendingAddService(repo,10,lambda:now[0])
            pending.start('g','a','猫')
            pending.start('g','a','狗')
            self.assertEqual(pending.inspect('g','a')[0].keyword,'狗')
            self.assertIsNone(pending.inspect('other','a')[0])
            now[0] = 110
            self.assertEqual(pending.inspect('g','a'),(None,True))
            pending.start('g','a','猫')
            now[0] = 109
            self.assertEqual(pending.inspect('g','a'),(None,True))
            pending.start('g','a','猫')
            self.assertTrue(pending.cancel('g','a'))
            self.assertFalse(pending.cancel('g','a'))
            repo.close()
