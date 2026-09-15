import hmac
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .common import ManagementError, private_ipv4


class ManagementServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, controller, token, allowed_host, cert, key):
        private_ipv4(address[0])
        self.allowed_host = private_ipv4(allowed_host)
        if len(token) < 32:
            raise ManagementError("访问密钥长度不足。")
        self.controller, self.token = controller, token
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(cert, key)

    def process_request(self, request, client_address):
        if client_address[0] != self.allowed_host or not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(8)
            secure = self.context.wrap_socket(request, server_side=True)
            super().process_request_thread(secure, client_address)
        except (OSError, ssl.SSLError):
            request.close()
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        pass  # Do not leak account data or tokens via HTTP tracebacks.


class Handler(BaseHTTPRequestHandler):
    server_version = "TianyiManagement"

    def log_message(self, *args):
        pass

    def _reply(self, value, status=200, content_type="application/json; charset=utf-8"):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        auth = self.headers.get("Authorization", "")
        return hmac.compare_digest(auth.encode("utf-8"), ("Bearer " + self.server.token).encode("utf-8"))

    def _handle(self, post=False):
        if not self._authorized():
            self._reply({"error": "认证失败。"}, 401)
            return
        # Browser cross-origin requests are not part of this desktop protocol.
        if self.headers.get("Origin"):
            self._reply({"error": "拒绝浏览器跨域请求。"}, 403)
            return
        try:
            url = urlsplit(self.path)
            controller = self.server.controller
            if post:
                if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                    raise ManagementError("只接受定长JSON请求。")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ManagementError("请求大小无效。")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ManagementError("请求必须是对象。")
                actions = {"/config": lambda: controller.configure(data), "/reconnect": controller.reconnect,
                           "/bind": lambda: controller.bind(data), "/start": lambda: controller.start(data), "/stop": controller.stop}
                if url.path not in actions or url.query:
                    self._reply({"error": "接口不存在。"}, 404)
                    return
                self._reply(actions[url.path]())
            elif url.path == "/status":
                self._reply(controller.status())
            elif url.path == "/galleries":
                self._reply({"items": controller.galleries()})
            elif url.path == "/images":
                query = parse_qs(url.query)
                self._reply({"items": controller.gallery_images(query.get("namespace", [""])[0], query.get("keyword", [""])[0])})
            elif url.path.startswith("/thumbnail/") and url.path.removeprefix("/thumbnail/").isdigit():
                self._reply(controller.thumbnail(int(url.path.rsplit("/", 1)[1])), content_type="image/jpeg")
            else:
                self._reply({"error": "接口不存在。"}, 404)
        except ManagementError as exc:
            self._reply({"error": str(exc)}, 409)
        except (ValueError, TypeError, KeyError):
            self._reply({"error": "请求格式无效。"}, 400)
        except Exception:
            self._reply({"error": "服务处理失败；请检查虚拟机本地状态。"}, 500)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(post=True)
