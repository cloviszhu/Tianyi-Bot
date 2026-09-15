import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wechat_gallery_bot.__main__ import main
from wechat_gallery_bot.adapters.base import AdapterError
from wechat_gallery_bot.adapters.wxauto_adapter import COMMIT, REPOSITORY, WxAutoAdapter, _configure_backend, check_backend
from wechat_gallery_bot.config import Config
from wechat_gallery_bot.storage.process_lock import data_directory_lock


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = WxAutoAdapter(("group",), Path(self.temp.name))
        self.adapter._ready = True
        self.chat = SimpleNamespace(who="group", ChatInfo=lambda: {"chat_name": "group", "chat_type": "group"},
                                    SendMsg=Mock(return_value=True), SendFiles=Mock(return_value=True))

    def message(self, **kwargs):
        fields = dict(attr="friend", type="text", content="/帮助", sender="Alice", id=[1, 2, 3])
        fields.update(kwargs)
        return SimpleNamespace(**fields)

    def test_normalization_and_sends_bound_to_callback_group(self):
        events = []
        def handle(event):
            events.append(event)
            self.adapter.send_text(event.chat_key, "hi")
            self.adapter.send_image(event.chat_key, Path(self.temp.name) / "one.png")
        self.adapter._dispatch(self.message(), self.chat, handle)
        self.assertEqual(events[0].sender_key, "Alice")
        self.assertEqual(events[0].event_id, "[1, 2, 3]")
        self.chat.SendMsg.assert_called_once_with(msg="hi")
        self.chat.SendFiles.assert_called_once()
        self.assertIsNone(self.adapter._active)

    def test_self_and_unknown_sender_skipped(self):
        handle = Mock()
        for fields in [dict(attr="self"), dict(sender="group"), dict(sender="friend"), dict(sender=""), dict(type="file")]:
            self.adapter._dispatch(self.message(**fields), self.chat, handle)
        handle.assert_not_called()

    def test_wrong_or_private_chat_skipped(self):
        handle = Mock()
        self.chat.ChatInfo = lambda: {"chat_name": "group", "chat_type": "friend"}
        self.adapter._dispatch(self.message(), self.chat, handle)
        handle.assert_not_called()
        self.chat.ChatInfo = lambda: {"chat_name": "other", "chat_type": "group"}
        self.adapter._dispatch(self.message(), self.chat, handle)
        handle.assert_not_called()

    def test_chat_changed_before_send_rejected(self):
        def handle(event):
            self.chat.ChatInfo = lambda: {"chat_name": "other", "chat_type": "group"}
            with self.assertRaises(AdapterError):
                self.adapter.send_text(event.chat_key, "hi")
        self.adapter._dispatch(self.message(), self.chat, handle)
        self.chat.SendMsg.assert_not_called()

    def test_no_download_for_unhandled_image(self):
        with patch.object(self.adapter, "_copy_preview") as copy:
            self.adapter._dispatch(self.message(type="image"), self.chat, lambda event: None)
        copy.assert_not_called()

    def test_download_is_lazy_and_callback_scoped(self):
        output = Path(self.temp.name) / "file"
        with patch.object(self.adapter, "_copy_preview", return_value=output) as copy:
            self.adapter._dispatch(self.message(type="image"), self.chat,
                                   lambda event: self.assertEqual(self.adapter.download_image(event), output))
        copy.assert_called_once()

    def test_failure_response_raises(self):
        self.chat.SendMsg.return_value = False
        def handle(event):
            with self.assertRaises(AdapterError):
                self.adapter.send_text(event.chat_key, "hi")
        self.adapter._dispatch(self.message(), self.chat, handle)

    def test_disable_side_effect_and_preserve_callback_order(self):
        module = SimpleNamespace(delete_update_files=Mock())
        params = SimpleNamespace()
        _configure_backend(SimpleNamespace(WxParam=params), module, Path(self.temp.name))
        self.assertIsNone(module.delete_update_files())
        self.assertEqual(params.LISTENER_EXCUTOR_WORKERS, 1)
        self.assertFalse(params.ENABLE_FILE_LOGGER)
        self.assertFalse(params.ENABLE_SENDER_OCR)

    def test_backend_origin_checked_without_import(self):
        dist = Mock(version="40.1.1")
        dist.read_text.return_value = json.dumps({"url": REPOSITORY, "vcs_info": {"commit_id": COMMIT}})
        with patch("sys.platform", "win32"), patch("importlib.metadata.distribution", return_value=dist):
            self.assertEqual(check_backend(), "40.1.1")
            dist.read_text.return_value = "{}"
            with self.assertRaises(AdapterError):
                check_backend()

    def test_preview_copy_uses_real_contract_without_source_deletion(self):
        source = Path(self.temp.name) / "original.png"
        source.write_bytes(b"image bytes; validated later by gallery")
        control = SimpleNamespace(Exists=lambda _: True, SendKeys=Mock())
        preview = SimpleNamespace(control=control, type="image", root=object(), tools={"更多": SimpleNamespace(Click=Mock())})
        menu = SimpleNamespace(select=Mock(return_value=True))
        modules = {
            "wxauto4.ui.component": SimpleNamespace(WeChatImage=Mock(return_value=preview), Menu=Mock(return_value=menu)),
            "wxauto4.utils.lock": SimpleNamespace(LockManager=SimpleNamespace(acquire=nullcontext)),
            "wxauto4.utils.win32": SimpleNamespace(ReadClipboardData=lambda: {"15": [str(source)]}, SetClipboardText=Mock()),
        }
        message = SimpleNamespace(roll_into_view=lambda: True, click=Mock())
        with patch.dict("sys.modules", modules):
            result = self.adapter._copy_preview(message)
        self.assertEqual(result.read_bytes(), source.read_bytes())
        self.assertTrue(source.is_file())
        self.assertNotEqual(result, source)
        menu.select.assert_called_once_with("复制")
        control.SendKeys.assert_called_once_with("{Esc}")

    def test_empty_clipboard_source_is_never_deleted(self):
        source = Path(self.temp.name) / "empty.png"
        source.touch()
        preview = SimpleNamespace(control=SimpleNamespace(Exists=lambda _: True, SendKeys=Mock()), type="image", root=object(), tools={"更多": SimpleNamespace(Click=Mock())})
        modules = {
            "wxauto4.ui.component": SimpleNamespace(WeChatImage=lambda _: preview, Menu=lambda _: SimpleNamespace(select=lambda _: True)),
            "wxauto4.utils.lock": SimpleNamespace(LockManager=SimpleNamespace(acquire=nullcontext)),
            "wxauto4.utils.win32": SimpleNamespace(ReadClipboardData=lambda: {"15": [str(source)]}, SetClipboardText=Mock()),
        }
        with patch.dict("sys.modules", modules), self.assertRaises(AdapterError):
            self.adapter._copy_preview(SimpleNamespace(roll_into_view=lambda: True, click=Mock()))
        self.assertTrue(source.is_file())

    def test_start_stop_with_injected_backend(self):
        callback_holder = {}
        def listen(nickname, callback):
            callback_holder["callback"] = callback
            return self.chat
        wx = SimpleNamespace(AddListenChat=listen, KeepRunning=lambda: callback_holder["callback"](self.message(), self.chat), StopListening=Mock())
        package = SimpleNamespace(WxParam=SimpleNamespace(), WeChat=Mock(return_value=wx))
        module = SimpleNamespace(delete_update_files=Mock())
        handler = Mock()
        with patch("wechat_gallery_bot.adapters.wxauto_adapter.check_backend", return_value="40.1.1"), patch("importlib.import_module", side_effect=lambda name: package if name == "wxauto4" else module):
            self.adapter.run(handler)
        handler.assert_called_once()
        wx.StopListening.assert_called_once_with(remove=False)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        config = Config.load(Path(".nonexistent-env"), {"TIANYI_GROUPS": '["测试群","群二"]'})
        self.assertEqual(config.groups, ("测试群", "群二"))
        self.assertEqual(config.pending_seconds, 60)

    def test_invalid_config(self):
        for values in [{"TIANYI_GROUPS": "group"}, {"TIANYI_GROUPS": '["x","x"]'}, {"TIANYI_PENDING_SECONDS": "nan"}, {"TIANYI_MAX_IMAGE_MB": "0"}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config.load(Path(".nonexistent-env"), values)

    def test_file_utf8_and_relative_data_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('TIANYI_GROUPS=["中文群"]\nTIANYI_DATA_DIR=data\n', encoding="utf-8-sig")
            config = Config.load(path, {})
            self.assertEqual(config.groups, ("中文群",))
            self.assertEqual(config.data_dir, Path(directory).resolve() / "data")

    def test_live_flag_required(self):
        with self.assertRaises(SystemExit), patch("wechat_gallery_bot.config.Config.load", return_value=Config(("g",), Path("data"))):
            main(["run"])

    def test_process_lock_released(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with data_directory_lock(root):
                with self.assertRaises(RuntimeError):
                    with data_directory_lock(root):
                        pass
            with data_directory_lock(root):
                pass
