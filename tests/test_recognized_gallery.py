import tempfile
import unittest
from pathlib import Path
from PIL import Image
from wechat_gallery_bot.management.recognized_gallery import candidates, execute
from wechat_gallery_bot.management.common import ManagementError


class RecognizedGalleryTests(unittest.TestCase):
    def test_candidates_reuse_parser_without_fuzzy_command_execution(self):
        data = {"uia": ["/加图 猫", "猫"], "lines": [{"text":"/加图 猫","confidence":.99}, {"text":"狗","confidence":.5}]}
        self.assertEqual(candidates(data), [("add","猫"),("lookup","猫")])

    def test_original_image_roundtrip_dedupe_and_group_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.png"
            Image.new("RGB", (16,16), "red").save(source)
            settings = {"groups":["A","B"], "max_image_mb":1}
            self.assertEqual(execute(root,settings,"A","add","猫",source)["status"],"added")
            self.assertEqual(execute(root,settings,"A","add","猫",source)["status"],"duplicate")
            self.assertEqual(execute(root,settings,"A","lookup","猫")["status"],"found")
            self.assertEqual(execute(root,settings,"B","lookup","猫")["status"],"missing")
            self.assertTrue(source.exists())
            with self.assertRaises(ManagementError):
                execute(root,settings,"C","add","猫",source)
            with self.assertRaises(ManagementError):
                execute(root,settings,"A","add","猫")
