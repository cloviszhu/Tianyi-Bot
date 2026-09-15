import http.client
import unittest
from unittest.mock import Mock

from wechat_gallery_bot.management.child_control import ChildControl


class ChildControlTests(unittest.TestCase):
    def setUp(self):
        self.control = ChildControl()
        self.addCleanup(self.cleanup)
        self.dispatch = Mock(return_value=True)
        self.pump()

    def cleanup(self):
        self.control.close()
        self.control.thread.join(3)
        self.assertFalse(self.control.thread.is_alive())

    def pump(self, validate=lambda: None):
        self.control.pump(validate, self.dispatch, lambda: {"ready": False})

    def request(self, method, path, *, token=None, body=None, extra=None):
        import json
        client = http.client.HTTPConnection("127.0.0.1", self.control.port, timeout=2)
        headers = {"Authorization": "Bearer " + (self.control.token if token is None else token)}
        headers.update(extra or {})
        try:
            client.request(method, path, body=body, headers=headers)
            result = client.getresponse()
            return result.status, json.loads(result.read())
        finally:
            client.close()

    def test_authenticated_loopback_status_no_token_disclosure(self):
        self.assertEqual(self.control.server.server_address[0], "127.0.0.1")
        status, data = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["fresh"])
        self.assertNotIn(self.control.token, str(data))
        self.assertEqual(self.request("GET", "/status", token="bad")[0], 403)
        self.assertEqual(self.request("GET", "/status", extra={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/status", extra={"Host": "example.com"})[0], 403)

    def test_command_runs_only_when_owner_pumps_and_never_sends(self):
        self.assertEqual(self.request("POST", "/start_receive")[0], 202)
        self.dispatch.assert_not_called()
        self.assertEqual(self.request("POST", "/stop")[0], 409)
        self.pump()
        self.dispatch.assert_called_once_with("start_receive")
        self.assertEqual(self.request("GET", "/status")[1]["command"]["state"], "requested")
        for path in ("/send", "/exec", "/update", "/start_receive?send=true", "//stop"):
            self.assertNotEqual(self.request("POST", path)[0], 202)

    def test_body_forbidden(self):
        self.assertEqual(self.request("POST", "/start_receive", body='{"send":true}')[0], 400)
        self.dispatch.assert_not_called()

    def test_context_loss_rejects_pending_command(self):
        self.request("POST", "/start_receive")
        self.pump(Mock(side_effect=RuntimeError("private detail")))
        self.dispatch.assert_not_called()
        self.assertEqual(self.request("GET", "/status")[1]["command"]["state"], "rejected")
        self.assertEqual(self.request("POST", "/start_receive")[0], 409)

    def test_stale_gui_and_delayed_command_rejected(self):
        self.control.updated -= 4
        self.assertEqual(self.request("POST", "/reconnect")[0], 409)
        self.pump()
        self.request("POST", "/reconnect")
        ticket, action, created = self.control.pending.get_nowait()
        self.control.pending.put_nowait((ticket, action, created - 4))
        self.pump()
        self.dispatch.assert_not_called()

    def test_client_uses_fixed_loopback_and_local_discovery(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from wechat_gallery_bot.management.child_control import local_request
        from wechat_gallery_bot.management.common import ManagementError
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.home", return_value=Path(directory)):
            discovery = Path(directory) / ".tianyi-bot" / "child-control.json"
            discovery.parent.mkdir()
            discovery.write_text(json.dumps({"protocol": 1, "port": self.control.port, "token": self.control.token}))
            self.assertTrue(local_request()["fresh"])
            self.assertEqual(local_request("stop")["state"], "pending")
            with self.assertRaises(ManagementError):
                local_request("send")
            discovery.write_text(json.dumps({"protocol": 1, "port": 80, "token": self.control.token}))
            with self.assertRaises(ManagementError):
                local_request()


class AttachTests(unittest.TestCase):
    def test_child_gui_dispatch_never_grants_send(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from wechat_gallery_bot.management.child_control import attach_control
        intake = SimpleNamespace(active=False, ready=False, terminal=False, message="stopped", stop=Mock())
        ops = SimpleNamespace(closed=False, busy=False, intake=intake,
                              groups=SimpleNamespace(get=lambda *a: "group"))
        def start(**kwargs):
            self.assertEqual(kwargs, {"send": False})
            ops.busy = True
        ops.start_intake = Mock(side_effect=start)
        root = SimpleNamespace(after=Mock())
        window = SimpleNamespace(root=root, closed=False, bound=object(), busy=False,
                                 operations=ops, refresh=Mock())
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.home", return_value=Path(directory)), patch(
                "wechat_gallery_bot.management.child_binding.require_child_context", return_value=14):
            attach_control(window)
            control = window.child_control
            try:
                control.pending.put_nowait(("ticket", "start_receive", __import__("time").monotonic()))
                root.after.call_args.args[1]()
                ops.start_intake.assert_called_once_with(send=False)
                self.assertEqual(control.command["state"], "requested")
            finally:
                window.closed = True
                control.close()
                control.thread.join(3)
