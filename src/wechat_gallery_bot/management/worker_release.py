"""Side-by-side local worker packages; activation affects only the next worker.

Hashes detect corruption, not publisher authenticity. Stage only reviewed local
project wheels. No network installer, extraction, shell, or dependency mutation.
"""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from .common import ManagementError, atomic_json

BOOTSTRAP = "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=['worker','--worker'];runpy.run_module('wechat_gallery_bot.management.child_intake',run_name='__main__')"


class WorkerReleases:
    def __init__(self, root=None):
        self.root = root or Path.home() / ".tianyi-bot" / "worker-releases"
        self.pointer = self.root / "active.json"

    @staticmethod
    def check_blob(blob):
        if not blob or len(blob) > 8 * 1024 * 1024:
            raise ManagementError("后台版本包为空或过大。")
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                entries = archive.infolist()
                names = [i.filename for i in entries]
                required = {"wechat_gallery_bot/__init__.py", "wechat_gallery_bot/management/child_intake.py"}
                if (not required.issubset(names) or len(entries) > 1000 or len(set(names)) != len(names)
                        or sum(i.file_size for i in entries) > 64 * 1024 * 1024):
                    raise ValueError()
                for item in entries:
                    parts = PurePosixPath(item.filename).parts
                    if (not parts or ".." in parts or "\\" in item.filename or ":" in item.filename
                            or item.filename.startswith("/")
                            or not (parts[0] == "wechat_gallery_bot" or re.fullmatch(r"wechat_gallery_bot-[A-Za-z0-9_.]+\.dist-info", parts[0]))
                            or (item.external_attr >> 16) & 0o170000 == 0o120000):
                        raise ValueError()
                if archive.testzip() is not None:
                    raise ValueError()
        except (zipfile.BadZipFile, ValueError, RuntimeError, NotImplementedError):
            raise ManagementError("后台版本包结构或校验失败。") from None
        return hashlib.sha256(blob).hexdigest()

    def path(self, digest):
        if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
            raise ManagementError("后台版本标识无效。")
        path = self.root / (digest + ".whl")
        if path.resolve().parent != self.root.resolve() or path.is_symlink():
            raise ManagementError("后台版本路径无效。")
        return path

    def stage(self, source):
        import os
        import tempfile
        with Path(source).open("rb") as stream:
            blob = stream.read(8 * 1024 * 1024 + 1)
        digest = self.check_blob(blob)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(digest)
        if target.exists():
            self.verify(digest)
            return digest
        fd, temporary = tempfile.mkstemp(prefix=".stage-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return digest

    def verify(self, digest):
        path = self.path(digest)
        try:
            with path.open("rb") as stream:
                blob = stream.read(8 * 1024 * 1024 + 1)
            if self.check_blob(blob) != digest:
                raise ManagementError("后台版本包已变化，拒绝启动。")
        except OSError:
            raise ManagementError("后台版本包缺失或不可读。") from None
        return path

    def state(self):
        if not self.pointer.exists():
            return {"current": None, "previous": None}
        try:
            with self.pointer.open("rb") as stream:
                raw = stream.read(1025)
            if len(raw) > 1024:
                raise ValueError()
            value = json.loads(raw)
            if set(value) != {"current", "previous"}:
                raise ValueError()
            for digest in value.values():
                if digest is not None:
                    self.path(digest)
            return value
        except (OSError, ValueError, TypeError):
            raise ManagementError("后台版本选择记录无效，不回退旧程序。") from None

    def activate(self, digest):
        self.verify(digest)
        current = self.state()["current"]
        if current != digest:
            atomic_json(self.pointer, {"current": digest, "previous": current})

    def rollback(self):
        state = self.state()
        if state["previous"] is not None:
            self.verify(state["previous"])
        atomic_json(self.pointer, {"current": state["previous"], "previous": state["current"]})

    def command(self, executable):
        digest = self.state()["current"]
        if digest is None:
            return [str(executable), "-m", "wechat_gallery_bot.management.child_intake", "--worker"], "installed"
        return [str(executable), "-I", "-c", BOOTSTRAP, str(self.verify(digest))], digest


def main():
    import argparse
    parser = argparse.ArgumentParser(description="本地后台版本管理；不启动机器人")
    parser.add_argument("action", choices=("stage", "activate", "rollback", "status"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    releases = WorkerReleases()
    try:
        if args.action == "stage":
            if not args.value:
                parser.error("stage 需要本地项目 wheel")
            print(releases.stage(args.value))
        elif args.action == "activate":
            releases.activate(args.value)
        elif args.action == "rollback":
            releases.rollback()
        else:
            print(json.dumps(releases.state()))
    except (ManagementError, OSError) as exc:
        parser.exit(1, (str(exc) if isinstance(exc, ManagementError) else "本地版本文件不可读写。") + "\n")


if __name__ == "__main__":
    main()
