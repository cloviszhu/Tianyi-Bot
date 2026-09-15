import inspect
import unittest
from PIL import Image, ImageDraw, ImageFont
from wechat_gallery_bot.management.vision_probe import analyze
from wechat_gallery_bot.management.common import ManagementError


class VisionTests(unittest.TestCase):
    def test_blank_frame_rejected(self):
        with self.assertRaises(ManagementError):
            analyze(Image.new("RGB", (300,200), "black"), lambda _: ([],None))

    def test_equal_text_keeps_both_positions(self):
        frame = Image.new("RGB", (300,200), "white")
        ImageDraw.Draw(frame).rectangle((10,10,100,100), fill="black")
        boxes = [[[1,y],[50,y],[50,y+10],[1,y+10]] for y in (10,70)]
        result = analyze(frame, lambda _: ([(b,"1",.95) for b in boxes],None))
        self.assertEqual(len(result["lines"]), 2)
        self.assertNotEqual(result["lines"][0]["box"],result["lines"][1]["box"])
        self.assertEqual(result["size"],[300,200])

    def test_capture_has_no_foreground_or_screen_fallback(self):
        from wechat_gallery_bot.management import vision_probe
        source = inspect.getsource(vision_probe)
        for forbidden in ("SetForegroundWindow", "ImageGrab", "SendKeys", ".Click(", "requests", "write_text"):
            self.assertNotIn(forbidden, source)
        self.assertIn("timeout=25", source)
        self.assertIn("snapshot_native(window, group)",source)


def offline_smoke():
    """Real local OCR on generated text only: not a live WeChat/capture test."""
    import json
    frame = Image.new("RGB", (700,220), "white")
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 36)
    draw.text((25,30), "加图 测试图库", font=font, fill="black")
    draw.text((25,100), "123456", font=font, fill="black")
    result = analyze(frame)
    text = " ".join(r["text"] for r in result["lines"])
    assert "加图" in text and "123456" in text, "local OCR smoke failed"
    print(json.dumps({"test":"generated_chinese_ocr", "passed":True,"blocks":len(result["lines"])}))


if __name__ == "__main__":
    offline_smoke()
