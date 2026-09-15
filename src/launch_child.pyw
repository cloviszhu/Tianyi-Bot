"""Portable trial entry. Uses the existing Python runtime/dependencies.

The adjacent reviewed wheel is immutable for this GUI's lifetime. Does not
install dependencies, elevate privileges, restart WeChat or start the bot.
"""
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "release.json").read_text(encoding="utf-8"))
    name = manifest["wheel"]
    if (not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name
            or not name.startswith("wechat_gallery_bot-") or not name.endswith("-py3-none-any.whl")):
        raise ValueError("invalid release")
    wheel = root / name
    with wheel.open("rb") as stream:
        blob = stream.read(8 * 1024 * 1024 + 1)
    if len(blob) > 8 * 1024 * 1024 or hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
        raise ValueError("release changed")
    sys.path.insert(0, str(wheel))
    # Process-local only: -m helpers must use this GUI's verified release too.
    # Never modify the user's Windows environment or the installed runtime.
    os.environ["PYTHONPATH"] = str(wheel)
    runpy.run_module("wechat_gallery_bot.management.child_binding", run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("天意Bot", "试运行包缺失、校验失败或入口启动失败。未启动机器人；请保留现有分身与微信。", parent=root)
        root.destroy()
