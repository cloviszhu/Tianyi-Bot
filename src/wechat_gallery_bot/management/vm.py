import os
import subprocess
from pathlib import Path

from .common import ManagementError


class VirtualBoxConsole:
    """Only opens the installed manager UI; no implicit VM or system changes."""
    def open(self):
        path = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Oracle" / "VirtualBox" / "VirtualBox.exe"
        if not path.is_file():
            raise ManagementError("尚未安装VirtualBox。请先阅读准备方案并批准系统变更。")
        subprocess.Popen([str(path)], shell=False)
