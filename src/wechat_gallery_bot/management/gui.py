import io
import json
import queue
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlencode

from PIL import Image, ImageTk

from .client import ManagementClient
from .common import ManagementError, app_home, atomic_json
from .vm import VirtualBoxConsole

STATES = {"stopped": "已停止", "starting": "正在启动", "running": "运行中", "stopping": "正在停止", "error": "运行异常"}
CONNECTIONS = {"not_connected": "未连接微信", "no_window": "未发现微信窗口", "window_detected": "已发现窗口，待人工核对小号", "manually_confirmed": "小号已人工确认", "lost": "连接已失效"}


class ManagerWindow:
    def __init__(self, root: tk.Tk, client=None, profile_path=None):
        self.root, self.client = root, client
        self.profile_path = profile_path or app_home() / "host-connection.json"
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="manager-http")
        self.results = queue.Queue()
        self.busy = False
        self.closed = False
        self.latest = None
        self.online = False
        self.loaded_epoch = None
        self.preview_image = None
        self.gallery_rows = {}
        self.image_ids = []
        root.title("天意Bot · 虚拟机管理")
        root.geometry("1080x820")
        root.minsize(920, 680)
        root.configure(background="#f3f5f8")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f8")
        style.configure("TLabel", background="#f3f5f8", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=6)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("State.TLabel", font=("Microsoft YaHei UI", 12, "bold"), foreground="#205d87")
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="天意Bot", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="主机管理 · 微信与图库在虚拟机中运行").pack(anchor="w", pady=(2, 12))
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="导入连接文件", command=self.import_pairing).pack(side="left")
        ttk.Button(toolbar, text="连接 / 刷新状态", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(toolbar, text="打开虚拟机控制台入口", command=self.open_console).pack(side="left")
        self.endpoint = tk.StringVar(value="尚未连接虚拟机")
        ttk.Label(toolbar, textvariable=self.endpoint).pack(side="right")
        self.banner = tk.StringVar(value="服务离线 · 状态未知")
        ttk.Label(frame, textvariable=self.banner, style="State.TLabel").pack(anchor="w", pady=(14, 4))
        self.account_status = tk.StringVar(value="绑定账号：尚未确认")
        ttk.Label(frame, textvariable=self.account_status).pack(anchor="w")
        self.note = tk.StringVar(value="关闭此主机GUI不会停止虚拟机中的机器人。首次连接请导入虚拟机导出的连接文件。")
        ttk.Label(frame, textvariable=self.note, wraplength=1000).pack(anchor="w", pady=(5, 12))
        tabs = ttk.Notebook(frame)
        tabs.pack(fill="both", expand=True)
        control_shell, gallery_tab = ttk.Frame(tabs), ttk.Frame(tabs, padding=16)
        tabs.add(control_shell, text="  连接与运行  ")
        tabs.add(gallery_tab, text="  图库浏览  ")
        canvas = tk.Canvas(control_shell, background="#f3f5f8", highlightthickness=0)
        scrollbar = ttk.Scrollbar(control_shell, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        control_tab = ttk.Frame(canvas, padding=16)
        content = canvas.create_window((0, 0), window=control_tab, anchor="nw")
        control_tab.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
        ttk.Label(control_tab, text="1  选择虚拟机内窗口，并在控制台人工核对登录的小号").grid(row=0, column=0, columnspan=3, sticky="w")
        self.windows = ttk.Combobox(control_tab, state="readonly", width=54)
        self.windows.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8)
        self.reconnect_button = ttk.Button(control_tab, text="重新连接微信", command=lambda: self.action("/reconnect", {}))
        self.reconnect_button.grid(row=1, column=2, padx=8)
        ttk.Label(control_tab, text="小号备注（人工填写）").grid(row=2, column=0, sticky="w")
        self.account_label = tk.StringVar()
        ttk.Entry(control_tab, textvariable=self.account_label, width=32).grid(row=2, column=1, sticky="ew")
        self.confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(control_tab, text="已在控制台核对：这是小号，当前不会切换账号", variable=self.confirmed).grid(row=3, column=0, columnspan=2, sticky="w", pady=8)
        self.bind_button = ttk.Button(control_tab, text="确认绑定所选窗口", command=self.bind)
        self.bind_button.grid(row=3, column=2, padx=8)
        ttk.Label(control_tab, text="免费库无法读取可靠账号ID；窗口标题不是账号名。换号前请停止，换号后重新绑定。", foreground="#9b6500").grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 14))
        ttk.Label(control_tab, text="2  监听群（每行一个完整群名）").grid(row=5, column=0, columnspan=3, sticky="w")
        self.groups = tk.Text(control_tab, height=5, width=60, font=("Microsoft YaHei UI", 10), relief="solid", borderwidth=1)
        self.groups.grid(row=6, column=0, columnspan=3, sticky="ew", pady=6)
        limits = ttk.Frame(control_tab)
        limits.grid(row=7, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(limits, text="加图有效期（秒）").pack(side="left")
        self.timeout = tk.StringVar(value="60")
        ttk.Spinbox(limits, from_=1, to=600, textvariable=self.timeout, width=7).pack(side="left", padx=(8, 22))
        ttk.Label(limits, text="图片上限（MB）").pack(side="left")
        self.limit = tk.StringVar(value="20")
        ttk.Spinbox(limits, from_=1, to=100, textvariable=self.limit, width=7).pack(side="left", padx=8)
        actions = ttk.Frame(control_tab)
        actions.grid(row=8, column=0, columnspan=3, sticky="w", pady=12)
        self.save_button = ttk.Button(actions, text="保存配置到虚拟机", command=self.save_settings)
        self.save_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="启动机器人", command=self.start_bot)
        self.start_button.pack(side="left", padx=10)
        self.stop_button = ttk.Button(actions, text="停止机器人", command=lambda: self.action("/stop", {}))
        self.stop_button.pack(side="left")
        control_tab.columnconfigure(1, weight=1)
        ttk.Label(control_tab, text="登录扫码/弹窗：打开上方控制台入口 → 选择虚拟机 → 显示。").grid(row=9, column=0, columnspan=3, sticky="w")
        ttk.Label(control_tab, text="后台运行要求：虚拟机未锁屏、未暂停；主机不休眠。RDP会话不支持。").grid(row=10, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Button(gallery_tab, text="刷新图库", command=self.refresh_galleries).pack(anchor="w")
        body = ttk.Frame(gallery_tab)
        body.pack(fill="both", expand=True, pady=10)
        self.gallery_tree = ttk.Treeview(body, columns=("group", "keyword", "count"), show="headings", height=13)
        for name, title, width in [("group", "群", 140), ("keyword", "关键词", 160), ("count", "张数", 60)]:
            self.gallery_tree.heading(name, text=title)
            self.gallery_tree.column(name, width=width)
        self.gallery_tree.pack(side="left", fill="both", expand=True)
        self.gallery_tree.bind("<<TreeviewSelect>>", self.select_gallery)
        preview = ttk.Frame(body, padding=(16, 0))
        preview.pack(side="left", fill="both", expand=True)
        self.image_picker = ttk.Combobox(preview, state="readonly", width=32)
        self.image_picker.pack(anchor="w")
        self.image_picker.bind("<<ComboboxSelected>>", self.select_image)
        self.image_label = ttk.Label(preview, text="选择关键词查看图片预览", anchor="center")
        self.image_label.pack(fill="both", expand=True, pady=12)
        self._buttons()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(80, self._drain)
        root.after(2000, self._poll)
        if self.client is None and self.profile_path.is_file():
            try:
                self.set_client(json.loads(self.profile_path.read_text(encoding="utf-8")))
            except Exception:
                self.note.set("已保存连接不可用，请重新导入连接文件。")
        if self.client:
            self.endpoint.set(f"{self.client.host}:{self.client.port} · 加密连接")
            root.after(100, self.refresh)

    def set_client(self, pairing):
        self.client = ManagementClient(pairing)
        self.endpoint.set(f"{self.client.host}:{self.client.port} · 加密连接")
        self.loaded_epoch = None

    def import_pairing(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="选择虚拟机导出的连接文件", filetypes=[("连接文件", "*.json")])
        if not path:
            return
        try:
            pairing = json.loads(Path(path).read_text(encoding="utf-8"))
            self.set_client(pairing)
            atomic_json(self.profile_path, pairing)
            self.refresh()
        except Exception:
            messagebox.showerror("连接文件无效", "请使用虚拟机服务窗口导出的JSON连接文件。")

    def submit(self, function, callback):
        if self.busy or self.closed:
            return
        self.busy = True
        self._buttons()
        def work():
            try:
                self.results.put((callback, function(), None))
            except Exception as exc:
                self.results.put((callback, None, str(exc) if isinstance(exc, ManagementError) else "操作失败，请刷新状态后重试。"))
        self.executor.submit(work)

    def _drain(self):
        if self.closed:
            return
        try:
            callback, result, error = self.results.get_nowait()
            self.busy = False
            if error:
                self.online = False
                self.latest = None
                self.banner.set("连接/操作失败 · 运行状态待刷新")
                self.account_status.set("绑定账号：状态未知")
                self.note.set(error)
            else:
                callback(result)
            self._buttons()
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _poll(self):
        if self.closed:
            return
        if self.client and not self.busy:
            self.refresh()
        self.root.after(2500, self._poll)

    def _buttons(self):
        idle = self.latest and self.latest["state"] in {"stopped", "error"}
        allowed = self.online and not self.busy
        for button in (self.reconnect_button, self.save_button, self.bind_button):
            button.configure(state="normal" if allowed and idle else "disabled")
        self.start_button.configure(state="normal" if allowed and idle and self.latest.get("binding") else "disabled")
        self.stop_button.configure(state="normal" if allowed and not idle else "disabled")

    def refresh(self):
        if self.client:
            self.submit(lambda: self.client.request("GET", "/status"), self.show_status)
        else:
            self.note.set("请先导入虚拟机服务窗口导出的连接文件。")

    def show_status(self, status):
        self.latest, self.online = status, True
        prefix = "离线模拟 · " if status["simulated"] else "虚拟机服务在线 · "
        self.banner.set(prefix + STATES.get(status["state"], status["state"]) + " · " + CONNECTIONS.get(status["connection"], status["connection"]))
        binding = status.get("binding")
        self.account_status.set("绑定账号（人工备注）：" + (binding["label"] if binding else "尚未确认"))
        self.note.set(status["error"] or "状态已刷新。关闭主机GUI不停止机器人；虚拟机锁屏、窗口丢失时需重新连接与确认。")
        candidates = status["candidates"]
        values = [f"{c['title']}  ·  进程 {c['pid']}" for c in candidates]
        if tuple(self.windows["values"]) != tuple(values):
            self.windows.configure(values=values)
            self.windows.set("")
            self.confirmed.set(False)
        if status["epoch"] != self.loaded_epoch:
            self.groups.delete("1.0", "end")
            self.groups.insert("1.0", "\n".join(status["settings"]["groups"]))
            self.timeout.set(str(status["settings"]["pending_seconds"]))
            self.limit.set(str(status["settings"]["max_image_mb"]))
            self.loaded_epoch = status["epoch"]
        self._buttons()

    def action(self, path, data):
        if self.client:
            self.submit(lambda: self.client.request("POST", path, data), self.show_status)

    def bind(self):
        index = self.windows.current()
        if not self.latest or index < 0 or not self.confirmed.get():
            messagebox.showinfo("先核对小号", "请选择微信窗口，并勾选已在虚拟机控制台核对小号。")
            return
        self.action("/bind", {"candidate_id": self.latest["candidates"][index]["id"], "epoch": self.latest["epoch"],
                              "label": self.account_label.get(), "confirmed": True})

    def save_settings(self):
        self.action("/config", {"groups": [line.strip() for line in self.groups.get("1.0", "end").splitlines() if line.strip()],
                                "pending_seconds": self.timeout.get(), "max_image_mb": self.limit.get()})

    def start_bot(self):
        if not self.latest or not self.latest.get("binding"):
            return
        binding, settings = self.latest["binding"], self.latest["settings"]
        text = f"小号（人工核对）：{binding['label']}\n群：{'、'.join(settings['groups'])}\n\n启动会在这些群中自动收发。请确认窗口仍是小号，且已保存配置。"
        if self.latest["simulated"]:
            text = "这是模拟服务，不会操作真实微信。\n\n" + text
        if messagebox.askyesno("确认本次启动", text):
            self.action("/start", {"binding_id": binding["id"], "config_revision": self.latest["config_revision"], "confirmed": True})

    def refresh_galleries(self):
        if self.client:
            self.submit(lambda: self.client.request("GET", "/galleries"), self.show_galleries)

    def show_galleries(self, data):
        for row in self.gallery_tree.get_children():
            self.gallery_tree.delete(row)
        self.gallery_rows.clear()
        for index, item in enumerate(data["items"]):
            key = str(index)
            self.gallery_rows[key] = item
            self.gallery_tree.insert("", "end", iid=key, values=(item["namespace"], item["keyword"], item["count"]))

    def select_gallery(self, _=None):
        selected = self.gallery_tree.selection()
        if not selected or not self.client or self.busy:
            return
        item = self.gallery_rows[selected[0]]
        query = urlencode({"namespace": item["namespace"], "keyword": item["keyword"]})
        self.submit(lambda: self.client.request("GET", "/images?" + query), self.show_images)

    def show_images(self, data):
        self.image_ids = [item["id"] for item in data["items"]]
        self.image_picker.configure(values=[f"图片 #{item}" for item in self.image_ids])
        if self.image_ids:
            self.image_picker.current(0)
            self.select_image()

    def select_image(self, _=None):
        index = self.image_picker.current()
        if self.client and index >= 0:
            image_id = self.image_ids[index]
            self.submit(lambda: self.client.request("GET", f"/thumbnail/{image_id}"), self.show_image)

    def show_image(self, payload):
        with Image.open(io.BytesIO(payload)) as image:
            image.thumbnail((450, 320))
            self.preview_image = ImageTk.PhotoImage(image.copy(), master=self.root)
        self.image_label.configure(image=self.preview_image, text="")

    def open_console(self):
        try:
            VirtualBoxConsole().open()
            self.note.set("在VirtualBox中选择天意Bot虚拟机并点击“显示”。扫码/弹窗处理完选择“分离”，不要关机或保存状态。")
        except ManagementError as exc:
            messagebox.showinfo("虚拟机控制台", str(exc))

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()  # Deliberately no POST /stop.


def main():
    import sys
    if "--remote" not in sys.argv:
        from .local_gui import main as local_main
        return local_main()
    root = tk.Tk()
    ManagerWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
