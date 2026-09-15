"""Child-session-only PrintWindow + local OpenCV/OCR comparison worker.

No screen-wide fallback, focus changes, clicks, disk screenshots or OCR network API.
Visual text is a candidate observation, never dispatched as a bot command.
"""
import base64
import ctypes
import hashlib
import io
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .common import ManagementError


def analyze(image, engine=None):
    import cv2
    import numpy as np
    pixels = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    if min(gray.shape) < 20 or gray.std() < 2:
        raise ManagementError("截图为空或近乎纯色；不会转用桌面截图。")
    if engine is None:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    result, _ = engine(cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    lines = []
    for box, text, score in (result or [])[:200]:
        lines.append({"text": str(text)[:1000], "confidence": round(float(score), 3),
                      "box": [[round(float(x)), round(float(y))] for x, y in box]})
    # Preserve geometry: equal text at different positions is not deduplicated.
    return {"lines": lines, "fingerprint": hashlib.sha256(pixels.tobytes()).hexdigest(),
            "size": list(image.size)}


def capture(hwnd, roi):
    import win32gui
    import win32ui
    from PIL import Image
    user32 = ctypes.windll.user32
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    user32.PrintWindow.restype = ctypes.c_int
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right-left, bottom-top
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise ManagementError("窗口截图尺寸超限。")
    x1,y1,x2,y2 = roi
    if not (left <= x1 < x2 <= right and top <= y1 < y2 <= bottom):
        raise ManagementError("聊天区域不在绑定窗口内。")
    dc = win32gui.GetWindowDC(hwnd)
    source = memory = bitmap = old = None
    try:
        source = win32ui.CreateDCFromHandle(dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        old = memory.SelectObject(bitmap)
        if not user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2):
            raise ManagementError("绑定窗口不能提供截图。")
        image = Image.frombuffer("RGB", (width,height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        return image.crop((x1-left,y1-top,x2-left,y2-top)).copy()
    finally:
        if old is not None and memory is not None:
            memory.SelectObject(old)
        if bitmap is not None:
            win32gui.DeleteObject(bitmap.GetHandle())
        if memory is not None:
            memory.DeleteDC()
        if source is not None:
            source.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def native(window, group):
    # DPI context is worker-local; do not alter host/global display settings.
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    import uiautomation as uia
    from .group_listener import snapshot_native, locate_controls
    from .draft_trial import belongs_to_root
    before = snapshot_native(window, group)  # validates child session, window and exact group
    with uia.UIAutomationInitializerInThread():
        root = uia.ControlFromHandle(window.hwnd)
        title, _, listing = locate_controls(root)
        if title.Name != group or not belongs_to_root(root, listing):
            raise ManagementError("群归属变化，放弃截图。")
        rect = listing.BoundingRectangle
        roi = (rect.left, rect.top, rect.right, rect.bottom)
        image = capture(window.hwnd, roi)
    after = snapshot_native(window, group)
    if before["view"] != after["view"] or before["messages"] != after["messages"]:
        raise ManagementError("截图期间列表变化，等待下一帧。")
    result = analyze(image)
    result["uia"] = [r["text"] for r in after["messages"] if r["kind"] == "text"][-30:]
    result["unknown"] = sum(r["kind"] == "unknown" for r in after["messages"])
    image.thumbnail((720, 340))
    output = io.BytesIO()
    image.save(output, format="PNG")
    result["preview"] = base64.b64encode(output.getvalue()).decode("ascii")
    return result


def read_vision(window, group):
    try:
        run = subprocess.run([str(Path(sys.executable).with_name("python.exe")), "-m", __name__],
            input=json.dumps({"window": asdict(window), "group": group}).encode(),
            capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ManagementError("截图/OCR 超时；已停止本次工作进程，不切前台。") from None
    if run.returncode:
        # Don't surface dependency tracebacks, file paths or captured chat data.
        raise ManagementError("本帧无法验证：请确认分身连接、配置群已打开；也可能是黑屏、列表变化或 OCR 不兼容。")
    return json.loads(run.stdout)


if __name__ == "__main__":
    from .window_binding import Window
    try:
        payload = json.loads(sys.stdin.buffer.read(16384))
        result = native(Window(**payload["window"]), payload["group"])
        sys.stdout.buffer.write(json.dumps(result).encode())
    except Exception:
        sys.exit(1)
