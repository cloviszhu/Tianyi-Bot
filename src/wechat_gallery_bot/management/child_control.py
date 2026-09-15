"""Loopback-only, per-launch control mailbox. Never executes HTTP work on Tk.

Same-user local management, not isolation from malicious processes of that user.
No remote-send, shell, path, configuration, or arbitrary code endpoints.
"""
import hmac
import json
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class ChildControl:
    COMMANDS = frozenset({"reconnect", "start_receive", "stop"})

    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.pending = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.snapshot = {"available": False}
        self.updated = 0
        self.command = None
        self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Do not log tokens, URLs or account data.

            def setup(self):
                super().setup()
                self.connection.settimeout(1)

            def reply(self, status, value):
                payload = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def authorized(self):
                expected = f"127.0.0.1:{owner.port}"
                supplied = self.headers.get("Authorization", "")
                return (self.client_address[0] == "127.0.0.1"
                        and self.headers.get("Host") == expected
                        and not self.headers.get("Origin")
                        and hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + owner.token).encode("ascii")))

            def do_GET(self):
                if not self.authorized():
                    return self.reply(403, {"error": "forbidden"})
                if self.path != "/status":
                    return self.reply(404, {"error": "unknown_route"})
                with owner.lock:
                    result = dict(owner.snapshot)
                    result["fresh"] = time.monotonic() - owner.updated < 3
                    result["command"] = owner.command
                self.reply(200, result)

            def do_POST(self):
                if not self.authorized():
                    return self.reply(403, {"error": "forbidden"})
                if self.headers.get("Content-Length", "0") != "0" or self.headers.get("Transfer-Encoding"):
                    return self.reply(400, {"error": "body_not_allowed"})
                action = self.path.removeprefix("/")
                raw_path = self.requestline.split()[1]
                if raw_path != "/" + action or action not in owner.COMMANDS:
                    return self.reply(404, {"error": "unknown_route"})
                with owner.lock:
                    if owner.closed or time.monotonic() - owner.updated >= 3 or (action != "stop" and not owner.snapshot.get("available")):
                        return self.reply(409, {"error": "unavailable"})
                    if owner.command and owner.command["state"] == "pending":
                        return self.reply(409, {"error": "busy"})
                    ticket = secrets.token_hex(8)
                    owner.command = {"id": ticket, "action": action, "state": "pending"}
                    owner.pending.put_nowait((ticket, action, time.monotonic()))
                self.reply(202, {"id": ticket, "state": "pending"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
        self.thread.start()

    def pump(self, validate, dispatch, snapshot):
        """Called exclusively by the child GUI thread; check context each time."""
        try:
            validate()
            available = True
        except Exception:
            available = False
        try:
            ticket, action, created = self.pending.get_nowait()
        except queue.Empty:
            pass
        else:
            state = "rejected"
            if (available or action == "stop") and time.monotonic() - created <= 3:
                try:
                    # Dispatch result means requested, NOT worker-ready or sent.
                    state = "requested" if dispatch(action) else "rejected"
                except Exception:
                    state = "failed"
            with self.lock:
                self.command = {"id": ticket, "action": action, "state": state}
        with self.lock:
            self.snapshot = {**snapshot(), "available": available}
            if not available:
                self.snapshot["ready"] = False
                self.snapshot["worker_summary"] = self.snapshot.get("summary", "")
                self.snapshot["summary"] = "分身控制器连接检查未通过，不能启动微信操作；可停止现有工作进程。"
            self.updated = time.monotonic()

    def close(self):
        with self.lock:
            self.closed = True
            self.snapshot = {"available": False}
        # Joining happens off Tk: a malformed local client must not freeze close.
        def finish():
            self.server.shutdown()
            self.server.server_close()
        threading.Thread(target=finish, daemon=True).start()


def attach_control(window):
    """Install once in the user-started child GUI, never in the host GUI."""
    import os
    from pathlib import Path
    from .child_binding import require_child_context
    from .common import atomic_json
    session = require_child_context(input_enabled=True)
    control = ChildControl()
    window.child_control = control
    try:
        atomic_json(Path.home() / ".tianyi-bot" / "child-control.json",
                    {"port": control.port, "token": control.token, "pid": os.getpid(),
                     "session": session, "protocol": 1})
    except Exception:
        control.close()
        raise

    def snapshot():
        ops = window.operations
        intake = ops.intake if ops and not ops.closed else None
        return {"bound": window.bound is not None, "busy": window.busy or bool(ops and ops.busy),
                "active": bool(intake and intake.active), "ready": bool(intake and intake.active and intake.ready),
                "terminal": bool(intake and intake.terminal),
                "release": getattr(intake, "release", "installed"),
                "summary": intake.message if intake else "自动接收尚未启动"}

    def dispatch(action):
        ops = window.operations
        if action == "stop":
            if ops and not ops.closed:
                ops.intake.stop()
            return True
        if window.busy or (ops and not ops.closed and (ops.busy or ops.intake.active)):
            return False
        if action == "reconnect":
            window.refresh()
            return True
        if action == "start_receive":
            if window.bound is None:
                return False
            if not ops or ops.closed:
                window.open_operations()
                ops = window.operations
            if not ops or ops.closed or not ops.groups.get("1.0", "end").strip():
                return False
            ops.start_intake(send=False)
            return bool(ops.busy or ops.intake.active)
        return False

    def tick():
        if window.closed:
            return
        control.pump(lambda: require_child_context(input_enabled=True), dispatch, snapshot)
        if not control.snapshot.get("available"):
            ops = window.operations
            if ops and not ops.closed and ops.intake.active:
                ops.intake.stop()
            status = getattr(window, "status", None)
            if status is not None:
                status.set("分身控制器连接检查未通过；机器人已请求停止。微信和分身不会被关闭。")
        window.root.after(250, tick)
    tick()


def local_request(action="status"):
    """Host-side client: fixed discovery file, direct loopback, no proxy/URL input."""
    import http.client
    from pathlib import Path
    from .common import ManagementError
    if action not in ChildControl.COMMANDS | {"status"}:
        raise ManagementError("不支持的分身管理操作。")
    try:
        path = Path.home() / ".tianyi-bot" / "child-control.json"
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        pairing = json.loads(raw)
        port, token = pairing["port"], pairing["token"]
        if (pairing.get("protocol") != 1 or type(port) is not int or not 1024 <= port <= 65535
                or not isinstance(token, str) or len(token) != 43
                or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in token)):
            raise ValueError()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET" if action == "status" else "POST", "/" + action,
                               headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            payload = response.read(16385)
            if response.status not in (200, 202) or len(payload) > 16384:
                raise ValueError()
            return json.loads(payload)
        finally:
            connection.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        raise ManagementError("分身管理通道不可用、忙碌或凭据已过期；未自动重试。") from None


if __name__ == "__main__":
    import argparse
    from .common import ManagementError
    parser = argparse.ArgumentParser(description="本机分身管理：不支持群发送或任意命令")
    parser.add_argument("action", choices=sorted(ChildControl.COMMANDS | {"status"}))
    try:
        print(json.dumps(local_request(parser.parse_args().action), ensure_ascii=False))
    except ManagementError as exc:
        parser.exit(1, str(exc) + "\n")
