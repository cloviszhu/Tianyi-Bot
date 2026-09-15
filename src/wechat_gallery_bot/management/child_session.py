"""Build and open a standalone, user-operated Windows child-session trial."""
import hashlib
import os
from pathlib import Path
import subprocess

from .common import ManagementError


def build_host(*, require_admin=False):
    source = Path(__file__).with_name("ChildSessionHost.cs")
    manifest = source.with_suffix(".manifest")
    compiler = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        raise ManagementError("缺少系统自带 C# 编译器，未下载或修改系统。")
    digest = hashlib.sha256(source.read_bytes() + (manifest.read_bytes() if require_admin else b"")).hexdigest()[:16]
    folder = Path.home() / ".tianyi-bot" / "session-host" / digest
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "TianyiSessionTrial.exe"
    if not target.exists():
        manifest_flags = ["/win32manifest:" + str(manifest)] if require_admin else []
        result = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", *manifest_flags,
                                 "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                                 "/reference:System.ServiceProcess.dll", "/out:" + str(target), str(source)],
                                capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not target.is_file():
            raise ManagementError("分身验证入口编译失败；系统设置未改变。")
    return target


def launch_trial():
    target = build_host()
    # Opening the panel does not enable sessions, elevate or connect.
    subprocess.Popen([str(target)], creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    launch_trial()
