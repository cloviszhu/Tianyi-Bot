"""Explicit read-only structural capability probe. Never invokes a pattern."""
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .common import ManagementError
from .window_binding import BindingService, Window, scan_native


def probe_controls(root):
    # Use class-only selectors. Do not inspect Name, Value, message descendants,
    # pattern actions, clipboard, focus or pointer position.
    page = root.GroupControl(ClassName="mmui::ChatMessagePage")
    box = page.CustomControl(ClassName="mmui::XSplitterView")
    edit = box.EditControl(ClassName="mmui::ChatInputField")
    found = bool(edit.Exists(0))
    available = bool(edit.GetPropertyValue(30043)) if found else False
    return {"input_found": found, "value_pattern_advertised": available,
            "background_text_verified": False, "background_image_verified": False,
            "background_receive_verified": False, "real_start_allowed": False}


def native_probe(window):
    BindingService()._validate(window, scan_native())
    import uiautomation as uia
    with uia.UIAutomationInitializerInThread():
        root = uia.ControlFromHandle(window.hwnd)
        if root.ProcessId != window.pid or root.ClassName != "mmui::MainWindow":
            raise ManagementError("绑定窗口已失效。")
        result = probe_controls(root)
    BindingService()._validate(window, scan_native())
    return result


def probe(window):
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    result = subprocess.run([str(executable), "-m", __name__],
                            input=json.dumps(asdict(window)).encode("utf-8"), capture_output=True,
                            timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ManagementError("结构检查未完成；没有调用收发或前台操作。请重新核对绑定。")
    return json.loads(result.stdout.decode("utf-8"))


if __name__ == "__main__":
    try:
        window = Window(**json.loads(sys.stdin.buffer.read(8192)))
        sys.stdout.buffer.write(json.dumps(native_probe(window)).encode("utf-8"))
    except Exception:
        sys.exit(1)
