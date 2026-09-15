"""Local stage-one manager: selection and binding only, never starts a bot."""
import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

from .window_binding import BindingService


class LocalBindingWindow:
    def __init__(self, root, service=None, child_mode=False):
        self.root = root
        self.child_mode = child_mode
        self.service = service or BindingService()
        self.closed = False
        self.busy = False
        self.counting = False
        self.bound = None
        self.generation = 0
        self.rows = []
        self.operations = None
        self.trial_active = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.results = queue.Queue()
        root.title("天意Bot 0.6.3 · 分身微信管理" if child_mode else "天意Bot · 本机窗口绑定")
        root.geometry("880x560")
        root.minsize(880, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)
        panel = ttk.Frame(root, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="微信连接与图库" if child_mode else "先选对小号，再接入机器人", font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
        ttk.Label(panel, text=("自动连接分身内唯一微信窗口，不操作主机微信" if child_mode else "本机模式 · 第一步：只识别窗口，不读取聊天、不监听、不发送"), foreground="#176b68").pack(anchor="w", pady=(8, 20))
        bar = ttk.Frame(panel)
        bar.pack(fill="x")
        self.scan_button = ttk.Button(bar, text="重新连接微信" if child_mode else "1. 识别微信窗口", command=self.refresh)
        self.scan_button.pack(side="left")
        self.pick_button = ttk.Button(bar, text="2. 5秒后识别当前窗口", command=self.pick_later)
        self.pick_button.pack(side="left", padx=12)
        self.windows = ttk.Combobox(panel, state="readonly")
        self.windows.pack(fill="x", pady=16)
        self.windows.bind("<<ComboboxSelected>>", self.selection_changed)
        self.confirmed = tk.BooleanVar(root, False)
        self.confirm_box = ttk.Checkbutton(panel, variable=self.confirmed, text="我已亲自查看所选窗口，确认这是机器人小号，不是主号")
        self.confirm_box.pack(anchor="w")
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=16)
        self.bind_button = ttk.Button(actions, text="3. 确认绑定（仅识别）", command=self.bind)
        self.bind_button.pack(side="left")
        self.unbind_button = ttk.Button(actions, text="解除绑定", command=self.unbind)
        self.unbind_button.pack(side="left", padx=12)
        ttk.Button(actions, text="图库与配置" if child_mode else "4. 图库与后台能力", command=self.open_operations).pack(side="left")
        if child_mode:
            for widget in (self.pick_button, self.windows, self.confirm_box, self.bind_button, self.unbind_button):
                widget.pack_forget()
        if not child_mode:
            ttk.Button(actions, text="空白分身验证", command=self.open_session_trial).pack(side="left", padx=8)
        self.status = tk.StringVar(root, "尚未识别。请先打开微信小号，再点击“识别微信窗口”。")
        ttk.Label(panel, textvariable=self.status, wraplength=790, foreground="#176b68").pack(anchor="w", pady=12)
        ttk.Separator(panel).pack(fill="x", pady=12)
        ttk.Label(panel, text=("分身内只开一个微信即可，无需选择或确认大小号。\n若出现多个微信窗口，先停止连接，请关闭多余窗口后重新连接。\n连接仅表示窗口可用；真实监听和收发尚未接通。" if child_mode else "如何分清窗口：识别后点击“5秒后识别当前窗口”，由你切到小号；倒计时结束后返回此界面核对。\n"
            "不会主动切换焦点、移动鼠标或使用剪贴板。窗口名称不能证明账号身份。\n"
            "关闭、最小化、锁屏或异常被下一次检查发现后取消绑定；休眠后请重新核对。\n"
            "如果在同一个窗口内换号，请主动解除绑定并重新核对；当前不能可靠检测这种换号。\n"
            "机器人必须全后台、不抢焦点、不占鼠标；后台收发尚未验证，真实启动暂禁用。"),
            wraplength=790, justify="left").pack(anchor="w")
        root.after(80, self.drain)
        root.after(2000, self.poll)

    def open_session_trial(self):
        from .child_session import launch_trial
        from tkinter import messagebox
        try:
            launch_trial()
        except Exception:
            messagebox.showerror("分身验证", "无法构建或打开验证入口；未启用子会话。")

    def controls(self):
        state = "disabled" if self.busy or self.counting or self.trial_active else "normal"
        for widget in (self.scan_button, self.pick_button, self.bind_button, self.unbind_button, self.confirm_box):
            widget.configure(state=state)
        self.windows.configure(state="disabled" if self.busy or self.counting or self.trial_active else "readonly")

    def submit(self, operation, done):
        if self.busy or self.closed:
            return
        self.busy = True
        self.controls()
        def work():
            try:
                self.results.put((done, operation(), None))
            except Exception:
                self.service.unbind()
                self.results.put((done, None, "识别或核验未通过，绑定已取消。请展开微信、解锁桌面并重新识别；同一进程多个主窗口不支持绑定。"))
        self.executor.submit(work)

    def drain(self):
        if self.closed:
            return
        try:
            done, value, error = self.results.get_nowait()
            self.busy = False
            if error:
                self.bound = None
                self.confirmed.set(False)
                self.status.set(error)
            else:
                done(value)
            self.controls()
        except queue.Empty:
            pass
        self.root.after(80, self.drain)

    def refresh(self):
        self.bound = None
        self.confirmed.set(False)
        self.rows = []
        self.windows.set("")
        self.windows.configure(values=[])
        self.status.set("正在只读识别窗口结构……")
        def done(value):
            self.generation, self.rows = value
            if self.child_mode:
                if len(self.rows) != 1:
                    self.status.set("请在分身内登录微信，然后点“重新连接微信”。" if not self.rows else "发现多个微信窗口，未连接；请关闭多余窗口后重新连接。")
                    return
                generation, key = self.generation, self.rows[0].key
                self.submit(lambda: self.service.bind(generation, key, True), self.show_bound)
                return
            self.windows.configure(values=[f"窗口 {i+1}  ·  HWND {w.hwnd:#x}  ·  PID {w.pid}" + ("  ·  已最小化" if w.minimized else "") for i, w in enumerate(self.rows)])
            self.status.set(f"找到 {len(self.rows)} 个主窗口，尚未选择。请手动核对；不会默认选择第一个。" if self.rows else "没有找到支持识别的微信主窗口。请确认已登录并展开窗口；新版本结构也可能不受支持。")
        self.submit(self.service.refresh, done)

    def selection_changed(self, event=None):
        self.confirmed.set(False)
        self.bound = None
        self.submit(self.service.unbind, lambda _: self.status.set("已更换选择。请再次人工核对并勾选确认。"))

    def pick_later(self):
        if not self.rows:
            self.status.set("请先识别微信窗口。")
            return
        self.counting = True
        self.confirmed.set(False)
        self.bound = None
        self.submit(self.service.unbind, lambda _: None)
        def tick(left):
            if self.closed:
                return
            self.status.set(f"请现在切到小号主窗口，{left} 秒后只读识别当前窗口。")
            if left:
                self.root.after(1000, lambda: tick(left - 1))
                return
            try:
                import win32gui
                self.select_foreground(win32gui.GetForegroundWindow())
            except Exception:
                self.status.set("无法识别当前窗口，请在列表手动选择。")
            self.counting = False
            self.controls()
        self.controls()
        tick(5)

    def select_foreground(self, hwnd):
        matches = [i for i, w in enumerate(self.rows) if w.hwnd == hwnd]
        self.windows.set("")
        self.confirmed.set(False)
        if len(matches) == 1:
            self.windows.current(matches[0])
            self.status.set("已选中你刚才置于前台的窗口。请返回这里核对并勾选确认；尚未绑定。")
        else:
            self.status.set("当前窗口不在候选列表中，没有选择任何窗口。请重新识别。")

    def bind(self):
        index = self.windows.current()
        if index < 0 or not self.confirmed.get():
            self.status.set("请先选择窗口，并勾选人工核对小号。")
            return
        generation, key = self.generation, self.rows[index].key
        self.submit(lambda: self.service.bind(generation, key, True), self.show_bound)

    def show_bound(self, window):
        self.bound = window
        if self.child_mode:
            self.status.set("已连接分身微信。未监听、未发送。")
            return
        self.status.set(f"已绑定窗口 {window.hwnd:#x} / 进程 {window.pid}（人工核对）。仅识别，未监听、未发送。")

    def unbind(self):
        self.bound = None
        self.confirmed.set(False)
        self.submit(self.service.unbind, lambda _: self.status.set("已解除绑定。未启动任何机器人。"))

    def poll(self):
        if self.closed:
            return
        if self.bound and not self.busy and not self.counting and not self.trial_active:
            self.submit(self.service.check, lambda value: self.show_bound(value) if value else None)
        self.root.after(2000, self.poll)

    def close(self):
        if self.operations and not self.operations.closed:
            self.operations.close()
            if not self.operations.closed:
                self.root.after(200, self.close)
                return
        self.closed = True
        self.bound = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def open_operations(self):
        if self.operations and not self.operations.closed:
            self.operations.root.lift()  # Only the user-requested management GUI.
            return
        from .local_operations_gui import LocalOperationsWindow
        child = tk.Toplevel(self.root)
        try:
            self.operations = LocalOperationsWindow(child, lambda: self.bound, trial_lock=self.lock_for_trial, child_mode=self.child_mode)
        except Exception:
            child.destroy()
            self.status.set("本机图库控制窗口无法打开，可能已有另一个管理器占用数据目录。")

    def lock_for_trial(self, active):
        self.trial_active = active
        self.controls()


def main():
    root = tk.Tk()
    LocalBindingWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
