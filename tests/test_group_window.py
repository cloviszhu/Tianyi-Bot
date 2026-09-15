import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from wechat_gallery_bot.adapters.group_window import open_exact_session
from wechat_gallery_bot.adapters.base import AdapterError


class GroupWindowTests(unittest.TestCase):
    def test_scoped_lookup_ignores_native_qt_version_and_other_processes(self):
        from wechat_gallery_bot.adapters.group_window import find_scoped_group
        selected = SimpleNamespace(pid=7, nickname="group")
        constructor = Mock(return_value=selected)
        constructor._ui_cls_name = "mmui::FramelessMainWindow"
        control = Mock(return_value=SimpleNamespace(ProcessId=7, ClassName=constructor._ui_cls_name, Name="group"))
        def enumerate_windows(callback, arg):
            callback(10, arg)
            callback(11, arg)
        modules = {
            "win32gui": SimpleNamespace(EnumWindows=enumerate_windows),
            "win32process": SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 7 if h == 11 else 8)),
            "wxauto4": SimpleNamespace(uia=SimpleNamespace(ControlFromHandle=control)),
            "wxauto4.ui.main": SimpleNamespace(WeChatSubWnd=constructor),
        }
        api = SimpleNamespace(pid=7)
        with patch.dict("sys.modules", modules):
            self.assertIs(find_scoped_group(api, "group", Mock()), selected)
        control.assert_called_once_with(11)
        constructor.assert_called_once_with(11, api)

    def row(self, name):
        return SimpleNamespace(name=name, control=SimpleNamespace(Name=name+"\npreview"), double_click=Mock())

    def session(self, rows):
        return SimpleNamespace(switch_chat=Mock(return_value="group"), get_session=Mock(return_value=rows), session_list=object())

    def test_target_need_not_be_first_and_prefix_not_selected(self):
        wrong, right = self.row("group-other"), self.row("group")
        self.assertEqual(open_exact_session(self.session([wrong, right]), "group", Mock(), lambda *a: True), "group")
        wrong.double_click.assert_not_called()
        right.double_click.assert_called_once()

    def test_ambiguous_rows_rejected(self):
        rows = [self.row("group"), self.row("group")]
        with self.assertRaises(AdapterError):
            open_exact_session(self.session(rows), "group", Mock(), lambda *a: True)
        for row in rows:
            row.double_click.assert_not_called()

    def test_empty_list_times_out_without_index_error_or_busy_loop(self):
        clock = Mock(side_effect=[0, 0, 1, 6])
        pause = Mock()
        with self.assertRaises(AdapterError):
            open_exact_session(self.session([]), "group", Mock(), lambda *a: True, clock=clock, sleep=pause)
        self.assertEqual(pause.call_count, 2)

    def test_changed_live_row_rejected(self):
        row = self.row("group")
        row.control.Name = "other"
        with self.assertRaises(AdapterError):
            open_exact_session(self.session([row]), "group", Mock(), lambda *a: True)
        row.double_click.assert_not_called()

    def test_guard_loss_prevents_click(self):
        row = self.row("group")
        with self.assertRaises(AdapterError):
            open_exact_session(self.session([row]), "group", Mock(side_effect=[None,None,AdapterError("stop")]), lambda *a: True)
        row.double_click.assert_not_called()

    def test_hidden_row_is_not_clicked(self):
        row = self.row("group")
        with self.assertRaises(AdapterError):
            open_exact_session(self.session([row]), "group", Mock(), lambda *a: False,
                               clock=Mock(side_effect=[0, 0, 6]), sleep=Mock())
        row.double_click.assert_not_called()

    def test_search_alias_is_not_accepted(self):
        session = self.session([])
        session.switch_chat.return_value = "group-other"
        with self.assertRaises(AdapterError):
            open_exact_session(session, "group", Mock(), lambda *a: True)
        session.get_session.assert_not_called()

    def test_visible_group_does_not_use_incompatible_search(self):
        session = self.session([self.row("group")])
        session.switch_chat.side_effect = AssertionError("search must not run")
        self.assertEqual(open_exact_session(session, "group", Mock(), lambda *a: True, prefer_visible=True), "group")
        session.switch_chat.assert_not_called()

    def test_main_window_path_uses_session_hook_then_scoped_lookup(self):
        from wechat_gallery_bot.adapters.group_window import open_group_window
        target = object()
        api = SimpleNamespace(get_sub_wnd=Mock(side_effect=[None, target]), _show=Mock())
        opener = Mock()
        self.assertIs(open_group_window(api, "group", Mock(), opener), target)
        opener.assert_called_once_with("group")

    def test_main_window_existing_window_needs_no_input(self):
        from wechat_gallery_bot.adapters.group_window import open_group_window
        target, opener = object(), Mock()
        self.assertIs(open_group_window(SimpleNamespace(get_sub_wnd=Mock(return_value=target)), "group", Mock(), opener), target)
        opener.assert_not_called()

    def test_child_hook_is_per_client_and_preserves_upstream_response(self):
        from wechat_gallery_bot.adapters.child_adapter import ChildWxAutoAdapter
        adapter = object.__new__(ChildWxAutoAdapter)
        adapter._before_input = Mock()
        row = self.row("group")
        session = self.session([row])
        other = self.session([])
        original = Mock()
        other.open_separate_window = original
        response = Mock(side_effect=lambda **kwargs: kwargs)
        modules = {
            "wxauto4": SimpleNamespace(uia=SimpleNamespace(IsElementInWindow=lambda *a: True)),
            "wxauto4.param": SimpleNamespace(WxResponse=SimpleNamespace(success=response)),
        }
        with patch.dict("sys.modules", modules):
            adapter._prepare_client(SimpleNamespace(_api=SimpleNamespace(_session_api=session)))
            self.assertEqual(session.open_separate_window("group"), {"data": {"nickname": "group"}})
        self.assertIs(other.open_separate_window, original)
        row.double_click.assert_called_once()
        self.assertEqual(adapter._before_input.call_count, 3)
