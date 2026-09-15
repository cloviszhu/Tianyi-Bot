import threading
from pathlib import Path

from ..storage.process_lock import data_directory_lock
from .backend import LiveBackend, SimulatedBackend
from .controller import GuestController
from .security import create_identity
from .server import ManagementServer


class ServiceSession:
    def __init__(self, root: Path, host: str, port: int, allowed_host: str, simulated=False):
        self.root = root
        self.lock = data_directory_lock(root)
        self.lock.__enter__()
        self.server = None
        self.controller = None
        self.http_stopped = False
        try:
            self.controller = GuestController(root, SimulatedBackend() if simulated else LiveBackend())
            self.pairing = create_identity(root / "management", host, port)
            self.server = ManagementServer((host, port), self.controller, self.pairing["token"], allowed_host,
                                           root / "management" / "server-cert.pem", root / "management" / "server-key.pem")
            self.pairing["port"] = self.server.server_address[1]
            self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            self.thread.start()
        except Exception:
            if self.server:
                self.server.server_close()
            self.lock.__exit__(None, None, None)
            raise

    def close(self):
        from .common import ManagementError
        if not self.http_stopped:
            self.server.shutdown()
            self.server.server_close()
            self.http_stopped = True
        if not self.controller.close():
            raise ManagementError("机器人仍在退出，数据锁已保留。请等待后再次停止；不要启动第二实例。")
        self.lock.__exit__(None, None, None)
