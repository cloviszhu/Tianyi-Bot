import ipaddress
import json
import os
import tempfile
from pathlib import Path


class ManagementError(RuntimeError):
    """Safe, user-facing error text only."""


def private_ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError) as exc:
        raise ManagementError("地址必须是明确的本机或私有IPv4地址。") from exc
    private = any(address in ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))
    if not private:
        raise ManagementError("拒绝公网、广播及通配监听地址。")
    return str(address)


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def app_home() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TianyiBot"
