import argparse
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .common import ManagementError, app_home, atomic_json
from .session import ServiceSession


class GuestWindow:
    def __init__(self, root, simulated=False):
        self.root, self.simulated = root, simulated
        self.session = None
        self.busy = False
        self.results = queue.Queue()
        self.home = app_home() / ("simulation" if simulated else "guest")
        self.preferences_path = self.home / "service-settings.json"
        root.title("天意Bot · " + ("离线模拟服务" if simulated else "虚拟机服务"))
        root.geometry("650x440")
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="离线模拟服务（不连接微信）" if simulated else "虚拟机内的天意Bot服务", font=("Microsoft YaHei UI", 17, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
        self.host = tk.StringVar(value="127.0.0.1" if simulated else "192.168.56.2")
        self.allowed = tk.StringVar(value="127.0.0.1" if simulated else "192.168.56.1")
        self.port = tk.StringVar(value="8765")
        self.directory = tk.StringVar(value=str(self.home / "data"))
        if self.preferences_path.is_file():
            try:
                values = json.loads(self.preferences_path.read_text(encoding="utf-8"))
                for key, variable in (("host", self.host), ("allowed", self.allowed), ("port", self.port), ("directory", self.directory)):
                    variable.set(values[key])
            except Exception:
                pass
        for row, (label, variable) in enumerate((("监听地址（仅主机网卡）", self.host), ("允许的主机IP", self.allowed), ("管理端口", self.port), ("虚拟机内数据目录", self.directory)), 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(frame, textvariable=variable, width=42).grid(row=row, column=1, sticky="ew", pady=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="w", pady=14)
        self.start_button = ttk.Button(buttons, text="启动管理服务", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止服务", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.export_button = ttk.Button(buttons, text="导出主机连接文件", command=self.export, state="disabled")
        self.export_button.pack(side="left")
        self.state = tk.StringVar(value="尚未启动。服务启动本身不连接微信；连接和收发由主机GUI明确操作。")
        ttk.Label(frame, textvariable=self.state, wraplength=585).grid(row=6, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(frame, text="保持此程序与虚拟机登录会话运行。可最小化此窗口；不要锁屏、注销或保存虚拟机状态。\n连接文件包含管理密钥，请只传给你自己的主机。", wraplength=585).grid(row=7, column=0, columnspan=2, sticky="w", pady=10)
        if simulated:
            ttk.Button(frame, text="打开主机GUI连接此模拟服务", command=self.open_manager).grid(row=8, column=0, columnspan=2, sticky="w")
        frame.columnconfigure(1, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def start(self):
        if self.busy or self.session:
            return
        try:
            host, allowed, port, directory = self.host.get().strip(), self.allowed.get().strip(), int(self.port.get()), Path(self.directory.get()).resolve()
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            messagebox.showerror("设置无效", "请填写正确的端口和数据目录。")
            return
        self.busy = True
        self.start_button.configure(state="disabled")
        self.state.set("正在启动管理服务…")
        def work():
            try:
                session = ServiceSession(directory, host, port, allowed, self.simulated)
                try:
                    atomic_json(self.preferences_path, {"host": host, "allowed": allowed, "port": port, "directory": str(directory)})
                except OSError:
                    session.close()
                    raise
                self.results.put(("started", session))
            except Exception as exc:
                self.results.put(("error", str(exc) if isinstance(exc, ManagementError) else "启动失败：请检查端口占用、网卡地址、数据权限及management依赖。"))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.busy or not self.session:
            return
        self.busy = True
        self.stop_button.configure(state="disabled")
        self.state.set("正在停止机器人和管理服务…")
        session = self.session
        def work():
            try:
                session.close()
                self.results.put(("stopped", None))
            except Exception as exc:
                self.results.put(("error", str(exc) if isinstance(exc, ManagementError) else "停止未完成，请重试。"))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            event, value = self.results.get_nowait()
            self.busy = False
            if event == "started":
                self.session = value
                self.state.set("管理服务在线 · " + ("模拟模式" if self.simulated else "等待主机连接；尚未操作微信"))
            elif event == "stopped":
                self.session = None
                self.state.set("服务已停止。")
            else:
                self.state.set(value)
            self.start_button.configure(state="disabled" if self.session else "normal")
            self.stop_button.configure(state="normal" if self.session else "disabled")
            self.export_button.configure(state="normal" if self.session else "disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def export(self):
        if not self.session:
            return
        path = filedialog.asksaveasfilename(title="导出敏感连接文件，仅传给自己的主机", defaultextension=".json", initialfile="tianyi-connection.json")
        if path:
            atomic_json(Path(path), self.session.pairing)
            self.state.set("连接文件已导出。请通过受控文件传递方式移至主机，使用后清理临时副本。")

    def open_manager(self):
        if not self.session:
            messagebox.showinfo("先启动服务", "请先启动模拟管理服务。")
            return
        from .client import ManagementClient
        from .gui import ManagerWindow
        window = tk.Toplevel(self.root)
        ManagerWindow(window, client=ManagementClient(self.session.pairing))

    def close(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等待当前操作结束。")
            return
        if self.session:
            if messagebox.askyesno("服务仍在运行", "关闭虚拟机服务窗口会停止机器人。\n选择“否”保留运行；仅关闭主机GUI不会停止。\n现在停止服务吗？"):
                self.stop()
            return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    root = tk.Tk()
    GuestWindow(root, args.simulate)
    root.mainloop()


if __name__ == "__main__":
    main()
