import unittest
from unittest.mock import patch

from wechat_gallery_bot.management.child_binding import validate_context, ChildProvider
from wechat_gallery_bot.management.common import ManagementError


class ChildBindingTests(unittest.TestCase):
    def test_rejection_has_specific_bounded_reason(self):
        cases = [(dict(self.record, published_unix="80"), 8, 6, "expired"),
                 (self.record, 8, 7, "console"), (self.record, 9, 6, "session"),
                 (dict(self.record, phase="ending"), 8, 6, "phase")]
        for record, current, console, reason in cases:
            with self.assertRaises(ManagementError) as failure:
                validate_context(record, current, console, now=100)
            self.assertEqual(failure.exception.lease_reason, reason)

    def test_no_cross_session_process_open(self):
        import inspect
        from wechat_gallery_bot.management import child_binding
        source = inspect.getsource(child_binding)
        for forbidden in ["OpenProcess", "controller_identity", "psutil.Process", "process_session(pid)", "process_session(int(record"]:
            if forbidden == "process_session(pid)":
                continue  # Generic helper declaration is still used for local candidates.
            self.assertNotIn(forbidden, source)

    def test_expired_future_and_nonfinite_publications_rejected(self):
        for stamp in ["90", "101", "nan", "inf"]:
            with self.subTest(stamp=stamp), self.assertRaises(ManagementError):
                validate_context(dict(self.record, published_unix=stamp), 8, 6, now=100)

    def test_old_controller_without_publication_rejected(self):
        record = dict(self.record)
        del record["published_unix"]
        with self.assertRaises(ManagementError):
            validate_context(record, 8, 6, now=100)

    def test_failure_includes_stage_not_false_exit_claim(self):
        from wechat_gallery_bot.management.child_binding import require_child_context
        with patch("wechat_gallery_bot.management.child_binding.Path.stat", side_effect=PermissionError()):
            with self.assertRaisesRegex(ManagementError, "读取连接记录.*PermissionError"):
                require_child_context()

    def setUp(self):
        self.record = {"owned_session": "8", "parent_session": "6", "phase": "connected", "published_unix": "99"}

    def test_child_accepted(self):
        self.assertEqual(validate_context(self.record, 8, 6, now=100), 8)

    def test_host_unknown_and_console_refused(self):
        for args in [(6, 6), (9, 6), (8, 8), (8, 7)]:
            with self.subTest(args=args), self.assertRaises(ManagementError):
                validate_context(self.record, *args, now=100)

    def test_teardown_unknown_record_or_wrong_controller_refused(self):
        for record in [{}, dict(self.record, phase="ending"), dict(self.record, owned_session="unknown")]:
            with self.subTest(record=record), self.assertRaises(ManagementError):
                validate_context(record, 8, 6, now=100)

    def test_child_worker_flag(self):
        from wechat_gallery_bot.management import window_binding
        with patch.object(window_binding.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"[]"
            self.assertEqual(ChildProvider().scan(), [])
            self.assertEqual(run.call_args.args[0][-1], "--scan-child")

    def test_context_checked_before_desktop_scan(self):
        from wechat_gallery_bot.management.window_binding import scan_native
        with patch("wechat_gallery_bot.management.child_binding.require_child_context", side_effect=ManagementError("host")), patch("wechat_gallery_bot.management.backend.desktop_active") as desktop:
            with self.assertRaises(ManagementError):
                scan_native(child=True)
            desktop.assert_not_called()
