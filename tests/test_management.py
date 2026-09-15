import hashlib
import http.client
import json
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wechat_gallery_bot.management.backend import LiveBackend, SimulatedBackend
from wechat_gallery_bot.management.client import ManagementClient
from wechat_gallery_bot.management.common import ManagementError, private_ipv4
from wechat_gallery_bot.management.controller import GuestController
from wechat_gallery_bot.management.session import ServiceSession
from wechat_gallery_bot.services.gallery_service import GalleryService
from wechat_gallery_bot.storage.sqlite_repository import SQLiteRepository


def wait_until(predicate, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for state")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backend = SimulatedBackend()
        self.controller = GuestController(self.root, self.backend)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.controller.close)

    def bind(self):
        state = self.controller.reconnect()
        return self.controller.bind({"epoch": state["epoch"], "candidate_id": state["candidates"][0]["id"], "label": "小号A", "confirmed": True})

    def start(self):
        self.controller.configure({"groups": ["测试群"], "pending_seconds": 60, "max_image_mb": 20})
        state = self.bind()
        return self.controller.start({"binding_id": state["binding"]["id"], "config_revision": state["config_revision"], "confirmed": True})

    def test_start_requires_binding_and_confirmation(self):
        with self.assertRaises(ManagementError):
            self.controller.start({})
        state = self.controller.reconnect()
        with self.assertRaises(ManagementError):
            self.controller.bind({"epoch": state["epoch"], "candidate_id": state["candidates"][0]["id"], "label": "x"})

    def test_stale_window_list_rejected(self):
        state = self.controller.reconnect()
        self.controller.reconnect()
        with self.assertRaises(ManagementError):
            self.controller.bind({"epoch": state["epoch"], "candidate_id": state["candidates"][0]["id"], "label": "x", "confirmed": True})

    def test_changed_config_requires_new_start_confirmation(self):
        state = self.bind()
        self.controller.configure({"groups": ["changed"], "pending_seconds": 60, "max_image_mb": 20})
        with self.assertRaises(ManagementError):
            self.controller.start({"binding_id": state["binding"]["id"], "config_revision": state["config_revision"], "confirmed": True})

    def test_start_stop_and_reconnect(self):
        self.start()
        wait_until(lambda: self.controller.status()["state"] == "running")
        with self.assertRaises(ManagementError):
            self.controller.reconnect()
        with self.assertRaises(ManagementError):
            self.controller.configure({"groups": ["g"], "pending_seconds": 60, "max_image_mb": 20})
        self.controller.stop()
        wait_until(lambda: self.controller.status()["state"] == "stopped")
        self.assertIsNone(self.controller.reconnect()["binding"])

    def test_lost_desktop_stops_and_invalidates_binding(self):
        self.start()
        wait_until(lambda: self.controller.status()["state"] == "running")
        self.backend.available = False
        wait_until(lambda: self.controller.status()["state"] in {"stopped", "error"})
        self.assertIsNone(self.controller.status()["binding"])
        self.assertEqual(self.controller.status()["connection"], "lost")

    def test_process_change_invalidates_binding(self):
        self.bind()
        self.backend.identity = "other-process"
        self.assertIsNone(self.controller.status()["binding"])

    def test_configuration_persists_but_binding_does_not(self):
        self.controller.configure({"groups": ["中文群"], "pending_seconds": 30, "max_image_mb": 5})
        self.bind()
        other = GuestController(self.root, self.backend)
        self.assertEqual(other.status()["settings"]["groups"], ["中文群"])
        self.assertIsNone(other.status()["binding"])

    def test_no_arbitrary_data_path_in_api_config(self):
        with self.assertRaises(ManagementError):
            self.controller.configure({"groups": [], "pending_seconds": 60, "max_image_mb": 20, "data_dir": "C:/"})

    def test_live_backend_rejects_host_before_ui_import(self):
        with patch("wechat_gallery_bot.management.backend.in_virtualbox_guest", return_value=False):
            with self.assertRaises(ManagementError):
                LiveBackend()

    def test_closed_controller_rejects_new_start(self):
        self.controller.close()
        with self.assertRaises(ManagementError):
            self.controller.reconnect()

    def test_long_pause_requires_reconfirmation(self):
        self.bind()
        self.controller._last_guard_at = time.time() - 60
        with self.assertRaisesRegex(ManagementError, "中断过久"):
            self.controller._guard(self.controller.binding)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.session = ServiceSession(self.root, "127.0.0.1", 0, "127.0.0.1", simulated=True)
        self.client = ManagementClient(self.session.pairing)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.session.close)

    def test_tls_status_and_reconnect_after_gui_disconnect(self):
        self.client.request("POST", "/config", {"groups": ["模拟群"], "pending_seconds": 60, "max_image_mb": 20})
        state = self.client.request("POST", "/reconnect")
        state = self.client.request("POST", "/bind", {"epoch": state["epoch"], "candidate_id": state["candidates"][0]["id"], "label": "demo", "confirmed": True})
        self.client.request("POST", "/start", {"binding_id": state["binding"]["id"], "config_revision": state["config_revision"], "confirmed": True})
        self.client = None
        wait_until(lambda: self.session.controller.status()["state"] == "running")
        another = ManagementClient(self.session.pairing)
        self.assertEqual(another.request("GET", "/status")["state"], "running")
        another.request("POST", "/stop")
        wait_until(lambda: self.session.controller.status()["state"] == "stopped")

    def test_wrong_token_rejected(self):
        pairing = dict(self.session.pairing, token="0" * 64)
        with self.assertRaisesRegex(ManagementError, "认证失败"):
            ManagementClient(pairing).request("GET", "/status")

    def test_certificate_mismatch_before_request(self):
        pairing = dict(self.session.pairing, fingerprint="0" * 64)
        with self.assertRaisesRegex(ManagementError, "证书不匹配"), patch.object(self.session.controller, "status") as status:
            ManagementClient(pairing).request("GET", "/status")
        status.assert_not_called()

    def test_source_ip_rejected(self):
        self.session.server.allowed_host = "127.0.0.2"
        with self.assertRaises(ManagementError):
            self.client.request("GET", "/status")

    def test_public_and_wildcard_addresses_rejected(self):
        for value in ["0.0.0.0", "8.8.8.8", "255.255.255.255", "example.com", "169.254.1.1"]:
            with self.subTest(value=value), self.assertRaises(ManagementError):
                private_ipv4(value)

    def test_gallery_listing_and_bounded_thumbnail(self):
        source = self.root / "sample.png"
        Image.new("RGB", (900, 600), "green").save(source)
        repository = SQLiteRepository(self.root / "bot.db")
        try:
            GalleryService(repository, self.root / "images").add("模拟群", "小图", source, "A")
        finally:
            repository.close()
        row = self.client.request("GET", "/galleries")["items"][0]
        self.assertEqual(row["count"], 1)
        payload = self.client.request("GET", f"/thumbnail/{row['preview_id']}")
        import io
        with Image.open(io.BytesIO(payload)) as image:
            self.assertLessEqual(image.width, 640)
            self.assertLessEqual(image.height, 480)
        with self.assertRaises(ManagementError):
            self.client.request("GET", "/thumbnail/../../server-key.pem")

    def test_metadata_path_escape_rejected(self):
        repository = SQLiteRepository(self.root / "bot.db")
        try:
            repository.insert("g", "k", "../management/server-key.pem", "hash", "A")
        finally:
            repository.close()
        with self.assertRaisesRegex(ManagementError, "路径无效"):
            self.client.request("GET", "/thumbnail/1")

    def test_state_response_does_not_expose_secrets(self):
        payload = json.dumps(self.client.request("GET", "/status"))
        self.assertNotIn(self.session.pairing["token"], payload)
        self.assertNotIn("server-key", payload)

    def test_malformed_json_and_unknown_routes(self):
        with self.assertRaises(ManagementError):
            self.client.request("POST", "/shell", {"command": "test"})
        with self.assertRaises(ManagementError):
            self.client.request("POST", "/config", {"groups": "wrong"})


if __name__ == "__main__":
    unittest.main()
