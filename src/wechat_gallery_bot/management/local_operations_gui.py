"""Actual local configuration/gallery GUI with explicitly separate offline demo."""
import io
import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from ..adapters.base import AdapterError
from ..adapters.window_scope import BACKGROUND_BLOCKER
from .background_probe import probe
from .common import ManagementError
from .local_workspace import LocalWorkspace
from .draft_trial import run_trial, describe


class LocalOperationsWindow:
    def __init__(self, root, binding_getter, workspace=None, trial_lock=None, child_mode=False):
        self.root, self.binding_getter = root, binding_getter
        self.child_mode = child_mode
        from .child_intake import IntakeProcess
        self.intake = IntakeProcess()
        self.listening = False
        self.listen_generation = 0
        self.workspace = workspace or LocalWorkspace(Path.home() / ".tianyi-bot" / "local-workspace")
        self.closed, self.busy = False, False
        self.trial_active = False
        self.trial_lock = trial_lock or (lambda active: None)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.results = queue.Queue()
        self.timers = set()
        self.rows, self.photo = {}, None
        root.title("天意Bot 0.6.12 · 群监听与图库" if child_mode else "天意Bot · 本机图库与后台能力")
        root.geometry("960x850")
        root.minsize(960, 850)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="本机图库与运行控制", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="分身内允许自动点击、按键和剪贴板操作；主Windows不参与。" if child_mode else "硬约束：机器人全程后台，不占鼠标、不抢焦点、不模拟按键。", foreground="#176b68").pack(anchor="w", pady=8)
        ttk.Label(frame, text="默认不发送；启用收发需本轮明确授权。微信兼容性、隐藏后运行和主机输入隔离尚待实测。" if child_mode else BACKGROUND_BLOCKER, wraplength=900, foreground="#9b6500").pack(anchor="w", pady=8)
        self.note = tk.StringVar(root, "可保存配置、查看图库和运行离线演示。离线演示不会连接绑定的小号。")
        ttk.Label(frame, textvariable=self.note, wraplength=900).pack(anchor="w", pady=8)
        self.binding_note = tk.StringVar(root)
        ttk.Label(frame, textvariable=self.binding_note).pack(anchor="w")
        tabs = ttk.Notebook(frame)
        tabs.pack(fill="both", expand=True, pady=12)
        config, gallery = ttk.Frame(tabs, padding=16), ttk.Frame(tabs, padding=16)
        tabs.add(config, text="配置与验证")
        tabs.add(gallery, text="图库与预览")
        self.vision_active, self.vision_serial = False, 0
        if child_mode:
            vision = ttk.Frame(tabs, padding=12)
            tabs.add(vision, text="视觉识别")
            ttk.Label(vision, text="仅截取绑定微信的聊天区域，离线 OCR；不保存画面、不发送。\n开始后可隐藏分身画面，稍后回来查看帧数和变化；不自动判断后台验证通过。", wraplength=850).pack(anchor="w")
            ttk.Button(vision, text="开始截图＋OCR 对比", command=self.start_vision).pack(anchor="w", pady=6)
            ttk.Button(vision, text="停止视觉识别", command=self.stop_vision).pack(anchor="w")
            self.vision_note = tk.StringVar(root, "尚未运行")
            ttk.Label(vision, textvariable=self.vision_note, wraplength=850).pack(anchor="w", pady=6)
            self.vision_preview = ttk.Label(vision)
            self.vision_preview.pack()
            self.vision_text = tk.Text(vision, height=10, wrap="word", state="disabled")
            self.vision_text.pack(fill="both", expand=True)
            self.recognized_choices = []
            self.recognized_group = None
            self.recognized_picker = ttk.Combobox(vision, state="readonly")
            self.recognized_picker.pack(fill="x", pady=4)
            ttk.Button(vision, text="暂停识别并处理选中指令（取图预览／选择原图入库）", command=self.process_recognized).pack(anchor="w")
            self.gallery_action_note = tk.StringVar(root, "识别结果需人工确认；尚不能自动获取微信原图，不发送。")
            ttk.Label(vision, textvariable=self.gallery_action_note, wraplength=850).pack(anchor="w")
        ttk.Label(config, text="未来监听群：每行一个完整群名（保存不等于授权收发）").pack(anchor="w")
        self.groups = tk.Text(config, height=4)
        self.groups.pack(fill="x", pady=6)
        settings = self.workspace.local.settings
        self.groups.insert("1.0", "\n".join(settings["groups"]))
        bar = ttk.Frame(config)
        bar.pack(fill="x", pady=8)
        self.timeout = tk.StringVar(root, str(settings["pending_seconds"]))
        self.limit = tk.StringVar(root, str(settings["max_image_mb"]))
        ttk.Label(bar, text="超时（秒）").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=600, textvariable=self.timeout, width=6).pack(side="left", padx=8)
        ttk.Label(bar, text="图片上限（MB）").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=100, textvariable=self.limit, width=6).pack(side="left", padx=8)
        self.save_button = ttk.Button(bar, text="保存到本机", command=self.save)
        self.save_button.pack(side="left", padx=12)
        self.listen_note = tk.StringVar(root, "只读监听尚未启动；不读取正文、不发送。")
        if child_mode:
            listen_bar = ttk.Frame(config)
            listen_bar.pack(fill="x", pady=8)
            ttk.Button(listen_bar, text="开始只读群监听", command=self.start_listening).pack(side="left")
            ttk.Button(listen_bar, text="停止监听", command=self.stop_listening).pack(side="left", padx=8)
            ttk.Label(config, textvariable=self.listen_note, wraplength=860).pack(anchor="w")
        self.probe_button = ttk.Button(config, text="只读检查绑定窗口的后台接口（不收发）", command=self.probe)
        self.probe_button.pack(anchor="w", pady=6)
        self.diagnose_button = ttk.Button(config, text="同学群：只读定位失败步骤（不写入）", command=self.diagnose_draft)
        self.diagnose_button.pack(anchor="w", pady=4)
        self.draft_confirmed = tk.BooleanVar(root, False)
        self.draft_check = ttk.Checkbutton(config, variable=self.draft_confirmed,
            text="确认小号当前为“同学群”、输入框无草稿/附件；本次仅后台写入后清除，不发送")
        self.draft_check.pack(anchor="w", pady=4)
        self.draft_button = ttk.Button(config, text="同学群：后台草稿测试（不发送）", command=self.draft_test)
        self.draft_button.pack(anchor="w", pady=4)
        if not child_mode:
            ttk.Label(config, text="测试期间请暂停鼠标/键盘操作以免干扰观测。若超时或焦点变化，可能需人工清除测试草稿。", wraplength=880).pack(anchor="w")
        else:
            for widget in (self.probe_button, self.diagnose_button, self.draft_check, self.draft_button):
                widget.pack_forget()
        self.live_button = ttk.Button(config, text="启动真实机器人：后台能力未验证", state="disabled")
        self.live_button.pack(anchor="w", pady=6)
        if child_mode:
            self.live_button.pack_forget()
            self.send_confirmed = tk.BooleanVar(root, False)
            ttk.Checkbutton(config, text="本轮允许在以上配置群发送文字及图片（下次启动需重新授权）", variable=self.send_confirmed).pack(anchor="w")
            ttk.Button(config, text="启动自动接收入库（分身内操作，不发送）", command=self.start_intake).pack(anchor="w", pady=4)
            ttk.Button(config, text="启动群收发机器人（需本轮授权）", command=lambda: self.start_intake(send=True)).pack(anchor="w", pady=4)
            ttk.Button(config, text="停止自动接收", command=self.intake.stop).pack(anchor="w", pady=4)
            self.intake_note = tk.StringVar(root, self.intake.message)
            ttk.Label(config, textvariable=self.intake_note, wraplength=860).pack(anchor="w")
        ttk.Separator(config).pack(fill="x", pady=12)
        ttk.Label(config, text="离线演示：独立数据目录，与真实图库完全分开").pack(anchor="w")
        actions = ttk.Frame(config)
        actions.pack(fill="x", pady=8)
        self.demo_start = ttk.Button(actions, text="启动离线演示", command=lambda: self.submit(self.workspace.start_demo, lambda _: self.note.set("离线演示已启动；不会操作微信。")))
        self.demo_start.pack(side="left")
        self.scenario_button = ttk.Button(actions, text="演示加图 → 取图", command=lambda: self.submit(self.workspace.scenario, lambda _: self.note.set("已提交三条模拟事件，稍后到图库页选择离线演示并刷新。")))
        self.scenario_button.pack(side="left", padx=10)
        self.stop_button = ttk.Button(actions, text="停止离线演示", command=lambda: self.submit(self.workspace.stop_demo, lambda _: self.note.set("已请求停止离线演示。")))
        self.stop_button.pack(side="left")
        self.demo_note = tk.StringVar(root)
        ttk.Label(config, textvariable=self.demo_note).pack(anchor="w", pady=6)
        ttk.Label(config, text="关闭此GUI会停止自动接收及离线演示；隐藏分身画面不等于关闭GUI。" if child_mode else "关闭此控制窗口会停止离线演示。当前没有后台运行的真实机器人。", wraplength=850).pack(anchor="w")
        self.dataset = ttk.Combobox(gallery, state="readonly", values=["本机图库", "离线演示图库"])
        self.dataset.current(0)
        self.dataset.pack(anchor="w")
        self.dataset.bind("<<ComboboxSelected>>", lambda _: self.refresh_gallery())
        self.gallery_button = ttk.Button(gallery, text="刷新图库", command=self.refresh_gallery)
        self.gallery_button.pack(anchor="w", pady=8)
        body = ttk.Frame(gallery)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("group", "keyword", "count"), show="headings", height=8)
        for key, title in [("group", "群"), ("keyword", "关键词"), ("count", "张数")]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=130 if key != "count" else 50)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_gallery)
        previews = ttk.Frame(body, padding=12)
        previews.pack(side="left", fill="both", expand=True)
        self.image_picker = ttk.Combobox(previews, state="readonly")
        self.image_picker.pack(anchor="w")
        self.image_picker.bind("<<ComboboxSelected>>", self.select_image)
        self.preview = ttk.Label(previews, text="选择关键词查看图片")
        self.preview.pack(fill="both", expand=True)
        self.images = []
        self.schedule(80, self.drain)
        self.schedule(250, self.poll)

    def start_intake(self, *, send=False):
        if not self.child_mode or self.busy or self.intake.active:
            return
        window = self.binding_getter()
        if window is None:
            self.note.set("请先连接分身内唯一微信。")
            return
        self.stop_listening()
        self.stop_vision()
        groups = [s.strip() for s in self.groups.get("1.0", "end").splitlines() if s.strip()]
        if send:
            if not self.send_confirmed.get() or not groups:
                self.note.set("请填写测试群，并明确勾选本轮文字和图片发送授权。")
                return
            if not messagebox.askyesno("确认本轮真实群收发", "机器人将在分身内以下群读取指令、下载原图、发送文字及图片：\n\n" + "\n".join(groups) + "\n\n微信兼容性仍需实测；确认开始？", parent=self.root):
                return
        self.send_confirmed.set(False)
        seconds, size = self.timeout.get(), self.limit.get()
        def start():
            self.workspace.configure(groups, seconds, size)
            self.intake.start(window, self.workspace.local.root, dict(self.workspace.local.settings), send_confirmed=send)
        self.submit(start, lambda _: self.note.set("本轮群收发启动已请求；分身内微信请交给机器人操作。" if send else "自动接收试运行已请求；不会回复群消息。分身内微信请交给机器人操作，主机可正常使用。"))

    def start_vision(self):
        if self.busy or self.vision_active or not self.child_mode or self.intake.active:
            return
        groups = [s.strip() for s in self.groups.get("1.0", "end").splitlines() if s.strip()]
        window = self.binding_getter()
        if len(groups) != 1 or window is None:
            self.vision_note.set("先连接分身微信并填写一个完整群名。")
            return
        self.stop_listening()
        self.vision_active = True
        self.vision_serial += 1
        self.vision_frames, self.vision_changes, self.vision_errors = 0, 0, 0
        self.vision_hash = None
        self.vision_tick(self.vision_serial, window, groups[0])

    def stop_vision(self):
        self.vision_active = False
        self.vision_serial += 1
        self.vision_note.set("视觉识别已停止；未发送。")

    def vision_tick(self, serial, window, group):
        if self.closed or not self.vision_active or serial != self.vision_serial:
            return
        if self.binding_getter() != window:
            self.stop_vision()
            self.vision_note.set("窗口绑定变化，已停止。")
            return
        if self.busy:
            self.schedule(500, lambda: self.vision_tick(serial, window, group))
            return
        from .vision_probe import read_vision
        def read():
            try:
                return read_vision(window, group), None
            except ManagementError as exc:
                return None, str(exc)
        def done(pair):
            if self.closed or not self.vision_active or serial != self.vision_serial:
                return
            if self.binding_getter() != window:
                self.stop_vision()
                return
            result, error = pair
            if error:
                self.vision_errors += 1
                self.vision_note.set(f"本帧失败（连续 {self.vision_errors}/3）：{error}")
                if self.vision_errors >= 3:
                    self.vision_active = False
                    self.vision_note.set("连续三帧失败，已停止。" + error)
                    return
            else:
                import base64
                self.vision_errors = 0
                self.vision_frames += 1
                if self.vision_hash is not None and self.vision_hash != result["fingerprint"]:
                    self.vision_changes += 1
                self.vision_hash = result["fingerprint"]
                from .recognized_gallery import candidates
                selected = self.recognized_picker.get()
                self.recognized_choices = candidates(result)
                self.recognized_group = group
                values = [("加图：" if kind == "add" else "取图：") + word for kind, word in self.recognized_choices]
                self.recognized_picker.configure(values=values)
                self.recognized_picker.set(selected if selected in values else "")
                self.vision_photo = ImageTk.PhotoImage(Image.open(io.BytesIO(base64.b64decode(result["preview"]))))
                self.vision_preview.configure(image=self.vision_photo)
                self.vision_text.configure(state="normal")
                self.vision_text.delete("1.0", "end")
                ocr = "\n".join(f"{r['confidence']:.0%}  {r['text']}" for r in result["lines"])
                self.vision_text.insert("1.0", "OCR 候选（不是消息条数）：\n" + ocr + "\n\nUIA 文字：\n" + "\n".join(result["uia"]))
                self.vision_text.configure(state="disabled")
                self.vision_note.set(f"有效帧 {self.vision_frames} · 画面变化 {self.vision_changes} · OCR 文字块 {len(result['lines'])} · UIA 未识别行 {result['unknown']}。画面不变可能是静止或冻结，不能据此确认后台成功。")
            self.schedule(3000, lambda: self.vision_tick(serial, window, group))
        self.submit(read, done)

    def process_recognized(self):
        if self.intake.active:
            self.gallery_action_note.set("自动接收运行中，请停止后再手动处理识别结果。")
            return
        if self.busy:
            self.gallery_action_note.set("正在完成当前帧，请稍后处理。")
            return
        index = self.recognized_picker.current()
        if index < 0 or index >= len(self.recognized_choices):
            self.gallery_action_note.set("请先从识别结果中选择一条指令。")
            return
        kind, keyword = self.recognized_choices[index]
        group = self.recognized_group
        self.stop_vision()
        source = None
        if kind == "add":
            if not messagebox.askyesno("确认本机入库", f"群：{group}\n关键词：{keyword}\n请选择对应原图。不会自动关联发送者，也不会向群发送消息。", parent=self.root):
                return
            source = filedialog.askopenfilename(parent=self.root, title="选择对应原图（不会删除源文件）", filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.webp *.bmp")])
            if not source:
                return
        from .recognized_gallery import execute
        settings = dict(self.workspace.local.settings)
        def work():
            result = execute(self.workspace.local.root, settings, group, kind, keyword, source)
            if result.get("image_id"):
                result["preview"] = self.workspace.local.thumbnail(result["image_id"])
            return result
        def done(result):
            status = result["status"]
            text = {"added": "原图已加入本机图库", "duplicate": "图片已存在，没有重复入库", "missing": "图库中没有该关键词", "found": "已从本机图库取图，显示在上方"}[status]
            self.gallery_action_note.set(f"{group} / {keyword}：{text}。未发送。")
            if "preview" in result:
                self.vision_photo = ImageTk.PhotoImage(Image.open(io.BytesIO(result["preview"])))
                self.vision_preview.configure(image=self.vision_photo)
        self.submit(work, done)

    def schedule(self, delay, callback):
        def fire():
            self.timers.discard(timer)
            if not self.closed:
                callback()
        timer = self.root.after(delay, fire)
        self.timers.add(timer)

    def submit(self, function, done):
        if self.closed or self.busy:
            return
        self.busy = True
        self.dataset.configure(state="disabled")
        self.image_picker.configure(state="disabled")
        def work():
            try:
                self.results.put((done, function(), None))
            except Exception as exc:
                self.results.put((done, None, str(exc) if isinstance(exc, (ManagementError, AdapterError)) else "操作未完成；没有转用前台自动化。"))
        self.executor.submit(work)

    def drain(self):
        if self.closed:
            return
        try:
            done, result, error = self.results.get_nowait()
            self.busy = False
            if self.trial_active:
                self.trial_active = False
                self.trial_lock(False)
                if error:
                    error = describe({"status": "unknown"})
            if error:
                self.note.set(error)
            else:
                done(result)
        except queue.Empty:
            pass
        self.schedule(80, self.drain)

    def poll(self):
        if self.closed:
            return
        window = self.binding_getter()
        if self.child_mode:
            self.intake_note.set(self.intake.poll(window))
        self.binding_note.set(("已连接分身微信；收发模式见下方运行状态" if self.child_mode else f"当前人工绑定：窗口 {window.hwnd:#x} / 进程 {window.pid}，未授权真实收发") if window else "没有有效绑定；可继续使用本机配置与离线演示。")
        stats = self.workspace.status()
        label = {"stopped": "已停止", "starting": "正在启动", "running": "运行中", "stopping": "正在停止", "error": "异常"}.get(stats["demo"], "未知")
        self.demo_note.set(f"离线状态：{label} · 已处理 {stats['events']} 条模拟事件 · 模拟文字 {stats['text_replies']} / 图片 {stats['image_replies']}" + (" · " + stats["error"] if stats["error"] else ""))
        for button in (self.save_button, self.probe_button, self.gallery_button, self.draft_button, self.draft_check, self.diagnose_button):
            button.configure(state="disabled" if self.busy else "normal")
        self.dataset.configure(state="disabled" if self.busy else "readonly")
        self.image_picker.configure(state="disabled" if self.busy else "readonly")
        idle = stats["demo"] in {"stopped", "error"}
        self.demo_start.configure(state="normal" if idle and not self.busy else "disabled")
        self.scenario_button.configure(state="normal" if stats["demo"] == "running" and not self.busy else "disabled")
        self.stop_button.configure(state="normal" if not idle and not self.busy else "disabled")
        self.schedule(500, self.poll)

    def save(self):
        if self.listening or self.intake.active:
            self.note.set("请先停止监听再修改配置。")
            return
        groups = [s.strip() for s in self.groups.get("1.0", "end").splitlines() if s.strip()]
        seconds, size = self.timeout.get(), self.limit.get()
        self.submit(lambda: self.workspace.configure(groups, seconds, size), lambda _: self.note.set("本机配置已保存；未启动或授权真实收发。"))

    def start_listening(self):
        if self.busy or self.listening or not self.child_mode or self.intake.active:
            return
        if getattr(self, "vision_active", False):
            self.stop_vision()
        groups = [s.strip() for s in self.groups.get("1.0", "end").splitlines() if s.strip()]
        window = self.binding_getter()
        if len(groups) != 1 or not window:
            self.listen_note.set("请保持微信已连接，并在群名框填写一个完整群名。")
            return
        from .message_observation import MessageObservation
        self.observation = MessageObservation()
        self.listen_failures = 0
        self.listen_window, self.listen_group = window, groups[0]
        self.listening = True
        self.listen_generation += 1
        self.listen_note.set("正在读取配置群消息并建立基线；正文仅在内存和本窗口预览，不保存、不发送。")
        self.listen_tick(self.listen_generation)

    def stop_listening(self):
        self.listening = False
        self.listen_generation += 1
        self.listen_note.set("监听已停止；未发送任何消息。")

    def listen_tick(self, generation):
        if self.closed or not self.listening or generation != self.listen_generation:
            return
        if self.binding_getter() != self.listen_window:
            self.stop_listening()
            self.listen_note.set("微信连接失效，监听已停止。")
            return
        if self.busy:
            self.schedule(500, lambda: self.listen_tick(generation))
            return
        from .group_listener import read_snapshot
        window, group = self.listen_window, self.listen_group
        def read():
            try:
                return read_snapshot(window, group), None
            except ManagementError as exc:
                return None, str(exc)
            except Exception:
                return None, "群监听检查失败：请保持配置群打开、分身已连接；该版本结构也可能不兼容。"
        def done(result):
            if not self.listening or generation != self.listen_generation:
                return
            snapshot, error = result
            if self.binding_getter() != window:
                error = "连接已变化。"
            try:
                if error:
                    raise ManagementError(error)
                self.observation.update(snapshot)
            except Exception as exc:
                self.listen_failures += 1
                self.observation.interrupt()
                if self.listen_failures <= 3 and self.binding_getter() == window:
                    self.listen_note.set(f"读取暂停，{self.listen_failures}/3 次自动重试；恢复后重新建立基线，期间可能漏消息。不发送。")
                    self.schedule(2000, lambda: self.listen_tick(generation))
                    return
                self.stop_listening()
                self.listen_note.set(str(exc) if isinstance(exc, ManagementError) else "消息结构变化，监听已停止。")
                return
            self.listen_failures = 0
            obs = self.observation
            self.listen_note.set(f"只读接收 · 连续确认 {obs.count} 条 · 当前未识别行 {obs.unknown} · 缺口 {obs.gaps}（非群消息总数）\n最近确认：{obs.latest or '暂无'}；不发送。")
            self.schedule(1500, lambda: self.listen_tick(generation))
        self.submit(read, done)

    def probe(self):
        window = self.binding_getter()
        if not window:
            self.note.set("请先在绑定窗口中选择并核对小号。")
            return
        def done(result):
            if self.binding_getter() != window:
                self.note.set("检查期间绑定已变化，结果已丢弃。")
                return
            self.note.set(f"只读检查：输入控件{'存在' if result['input_found'] else '未发现'}，声明Value接口：{'是' if result['value_pattern_advertised'] else '否'}。这不证明能后台写入或不抢焦点；图片与收发均未验证，真实启动仍禁用。")
        self.submit(lambda: probe(window), done)

    def repository(self):
        return self.workspace.demo if self.dataset.current() == 1 else self.workspace.local

    def draft_test(self):
        if self.busy:
            return
        window = self.binding_getter()
        if not window or not self.draft_confirmed.get():
            self.note.set("请先绑定小号，并勾选本次同学群草稿测试；不会自动选择窗口或切换聊天。")
            return
        self.draft_confirmed.set(False)
        self.trial_active = True
        self.trial_lock(True)
        self.note.set("正在进行一次后台草稿测试，不发送。请暂勿操作鼠标/键盘；测试至多等待15秒，超时不重试。")
        report_path = self.workspace.root / "last-draft-trial.json"
        def done(result):
            text = describe(result)
            if self.binding_getter() != window:
                text += " 检查期间绑定已变化，此结果不能用于当前绑定。"
            self.note.set(text)
        self.submit(lambda: run_trial(window, confirmed=True, report_path=report_path), done)

    def diagnose_draft(self):
        if self.busy:
            return
        window = self.binding_getter()
        if not window:
            self.note.set("请先绑定小号；只读诊断不会自动选择窗口。")
            return
        self.note.set("正在逐步只读诊断，不写入、不清除、不发送。")
        def done(result):
            if self.binding_getter() != window:
                self.note.set("绑定已变化，本次只读结果不适用于当前窗口。")
                return
            self.note.set(describe(result))
        self.submit(lambda: run_trial(window, confirmed=False, readonly=True,
            report_path=self.workspace.root / "last-draft-diagnostic.json"), done)

    def refresh_gallery(self):
        if self.busy:
            return
        self.photo = None
        self.preview.configure(image="", text="选择关键词查看图片")
        self.images = []
        self.image_picker.set("")
        self.image_picker.configure(values=[])
        repository = self.repository()
        def done(rows):
            self.tree.delete(*self.tree.get_children())
            self.rows = {str(i): row for i, row in enumerate(rows)}
            for key, row in self.rows.items():
                self.tree.insert("", "end", iid=key, values=(row["namespace"], row["keyword"], row["count"]))
        self.submit(repository.galleries, done)

    def select_gallery(self, _=None):
        selected = self.tree.selection()
        if not selected or self.busy:
            return
        row, repository = self.rows[selected[0]], self.repository()
        def done(rows):
            self.images = [row["id"] for row in rows]
            self.image_picker.configure(values=[f"图片 #{i}" for i in self.images])
            self.image_picker.set("")
            self.photo = None
            self.preview.configure(image="", text="请选择图片")
            if self.images:
                self.image_picker.current(0)
                self.select_image()
        self.submit(lambda: repository.gallery_images(row["namespace"], row["keyword"]), done)

    def select_image(self, _=None):
        index = self.image_picker.current()
        if index < 0 or self.busy:
            return
        repository, image_id = self.repository(), self.images[index]
        def done(payload):
            with Image.open(io.BytesIO(payload)) as image:
                image.thumbnail((360, 260))
                self.photo = ImageTk.PhotoImage(image.copy(), master=self.root)
            self.preview.configure(image=self.photo, text="")
        self.submit(lambda: repository.thumbnail(image_id), done)

    def close(self):
        if self.closed:
            return
        if self.intake.active:
            self.intake.stop()
            self.intake.poll(self.binding_getter())
            self.schedule(100, self.close)
            return
        if self.listening:
            self.stop_listening()
        if self.busy:
            self.note.set("等待当前只读检查或本地操作结束后关闭……")
            self.schedule(100, self.close)
            return
        if not self.workspace.close():
            self.note.set("离线演示仍在停止，暂不释放数据锁；请稍后关闭。")
            return
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        for timer in self.timers:
            self.root.after_cancel(timer)
        self.timers.clear()
        self.root.destroy()
