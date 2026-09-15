import hashlib
import hmac
import http.client
import json
import re
import ssl

from .common import ManagementError, private_ipv4


class ManagementClient:
    def __init__(self, pairing: dict):
        self.host = private_ipv4(pairing.get("host", ""))
        self.port = int(pairing.get("port", 8765))
        if not 1 <= self.port <= 65535:
            raise ManagementError("端口无效。")
        self.token = pairing.get("token", "")
        self.fingerprint = pairing.get("fingerprint", "").lower()
        if not re.fullmatch("[0-9a-f]{64}", self.fingerprint) or not re.fullmatch("[0-9a-fA-F]{64}", self.token):
            raise ManagementError("连接文件的密钥或证书指纹无效。")

    def request(self, method: str, path: str, data=None):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # The imported certificate fingerprint is the trust anchor, verified
        # BEFORE sending the bearer token. No system CA changes are made.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=8, context=context)
        try:
            connection.connect()
            actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual, self.fingerprint):
                raise ManagementError("虚拟机证书不匹配；已拒绝发送密钥。请重新核对连接文件。")
            headers = {"Authorization": "Bearer " + self.token}
            body = None
            if method == "POST":
                body = json.dumps(data or {}).encode("utf-8")
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise ManagementError("服务响应超过大小限制。")
            if response.status != 200:
                raise ManagementError(json.loads(payload).get("error", "请求失败。"))
            return payload if response.getheader("Content-Type", "").startswith("image/") else json.loads(payload)
        except ManagementError:
            raise
        except Exception as exc:
            raise ManagementError("无法连接虚拟机服务；请检查虚拟机、服务和仅主机网络。操作未自动重试，请刷新真实状态。") from exc
        finally:
            connection.close()
