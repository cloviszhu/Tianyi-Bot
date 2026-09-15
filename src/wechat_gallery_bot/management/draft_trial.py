"""User-triggered, one-shot background draft experiment. Never sends.

The agent tests this module with injected doubles only. Native execution is an
explicit application button, not an alternate agent desktop-control interface.
"""
import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .common import ManagementError, atomic_json
from .window_binding import BindingService, Window, scan_native

AUTHORIZED_GROUP = "同学群"

STAGES = {"unknown": "未分类步骤", "binding": "绑定窗口/进程复核", "main": "主窗口身份",
          "page": "聊天页面定位", "header": "群标题区域定位", "group_name": "同学群名称核对",
          "group_type": "群聊标识检查", "input": "输入框定位", "ownership": "输入框父级归属",
          "enabled": "输入框启用状态", "value_available": "Value接口声明", "incarnation": "控件实例复核",
          "value_pattern": "Value接口获取", "read_only": "Value只读标志", "attachments": "草稿附属控件检查",
          "draft_read": "草稿读取", "value_write": "Value写入", "environment": "输入环境检查"}


def failure_details(driver, exc):
    stage = getattr(driver, "stage", "unknown")
    kind = type(exc).__name__
    result = {"stage": stage if stage in STAGES else "unknown",
              "error_kind": kind if kind in {"AttributeError", "COMError", "OSError", "TimeoutError", "ManagementError", "RuntimeError", "TypeError"} else "OtherError"}
    hresult = getattr(exc, "hresult", None)
    if type(hresult) is int and -(2**31) <= hresult < 2**32:
        result["hresult"] = hresult
    return result  # Never serialize str(exc), traceback, names or draft values.


def belongs_to_root(root, control, max_depth=64):
    """Validate UIA ancestry, not on-screen rectangle overlap."""
    root_id, pid = tuple(root.GetRuntimeId()), root.ProcessId
    if not root_id:
        return False
    visited = set()
    for _ in range(max_depth):
        if control is None or control.ProcessId != pid:
            return False
        identity = tuple(control.GetRuntimeId())
        if identity == root_id:
            return True
        if not identity or identity in visited:
            return False
        visited.add(identity)
        control = control.GetParentControl()
    return False


def diagnose_driver(driver):
    result = {"status": "not_run", "reason": "precondition", "write_attempted": False,
              "write_observed": False, "clear_attempted": False, "cleared": False,
              "real_start_allowed": False, "mode": "read_only"}
    try:
        driver.verify()
        if driver.read() != "":
            result["reason"] = "existing_draft"
        else:
            result.update(status="diagnostic_ready", reason="read_only_preflight_passed")
    except Exception as exc:
        result["reason"] = "operation_failed"
        result["failure"] = failure_details(driver, exc)
        if result["failure"]["stage"] == "header" and hasattr(driver, "header_structure"):
            try:
                result["structure"] = driver.header_structure()
            except Exception:
                result["structure"] = {"status": "unavailable"}
    return result


def structural_outline(root, start, max_nodes=160, max_depth=8):
    """Bounded, class-allowlisted metadata only: never Name/Value/AutomationId."""
    known = {"mmui::ChatMessagePage", "mmui::ChatInfoView", "mmui::XSplitterView",
             "mmui::ChatInputField", "mmui::MainWindow"}
    pending, seen, nodes = [(start, -1, 0)], set(), []
    truncated = False
    while pending and len(nodes) < max_nodes:
        control, parent, depth = pending.pop(0)
        if not belongs_to_root(root, control):
            raise ManagementError("结构控件归属不符。")
        identity = tuple(control.GetRuntimeId())
        if identity in seen:
            raise ManagementError("结构控件重复。")
        seen.add(identity)
        cls = control.ClassName
        kind = control.ControlType
        index = len(nodes)
        nodes.append({"parent": parent, "depth": depth,
                      "class": cls if cls in known else "other",
                      "type": kind if type(kind) is int and 50000 <= kind <= 50040 else None})
        if depth >= max_depth:
            truncated = True
            continue
        children = control.GetChildren()
        capacity = max_nodes - len(nodes) - len(pending)
        if len(children) > capacity:
            truncated = True
        pending.extend((child, index, depth + 1) for child in children[:max(0, capacity)])
    return {"status": "metadata_only", "nodes": nodes, "truncated": truncated or bool(pending)}


def experiment(driver, monitor):
    result = {"status": "not_run", "write_attempted": False, "write_observed": False,
              "clear_attempted": False, "cleared": False, "real_start_allowed": False,
              "environment_changed": False, "reason": "precondition"}
    marker = "TIANYI_DRAFT_TEST_" + uuid.uuid4().hex[:12]
    try:
        driver.verify()
        if driver.read() != "":
            result["reason"] = "existing_draft"
            return result
        monitor.check()
        driver.verify()
        if driver.read() != "":
            result["reason"] = "draft_changed"
            return result
        monitor.check()
        result["write_attempted"] = True
        driver.write(marker)
        monitor.check()
        driver.verify()
        if driver.read() != marker:
            result["reason"] = "write_not_observed"
            return result
        result["write_observed"] = True
        result["reason"] = "cleanup_pending"
    except Exception as exc:
        result["reason"] = "operation_failed"
        result["failure"] = failure_details(driver, exc)
    finally:
        if result["write_attempted"]:
            # A failure may have occurred after SetValue took effect. Clear only
            # our exact marker, in the same verified target, with no input change.
            try:
                monitor.check()
                driver.verify()
                current = driver.read()
                if current == marker:
                    monitor.check()
                    result["clear_attempted"] = True
                    driver.write("")
                    monitor.check()
                    driver.verify()
                    result["cleared"] = driver.read() == ""
                elif current == "":
                    result["cleared"] = True
                else:
                    result["reason"] = "draft_changed_no_cleanup"
            except Exception as exc:
                result["reason"] = "cleanup_unconfirmed"
                result["cleanup_failure"] = failure_details(driver, exc)
        try:
            monitor.check()
        except Exception:
            result["environment_changed"] = True
        if result["write_attempted"]:
            result["status"] = "observed_pass" if (result["write_observed"] and result["cleared"] and not result["environment_changed"] and result["reason"] == "cleanup_pending") else "failed"
            if not result["cleared"]:
                result["status"] = "uncertain_cleanup"
        if result["status"] == "observed_pass":
            result["reason"] = "sampled_only"
    return result


class EnvironmentMonitor:
    """Read-only 10ms sampling; this is not proof of absence of brief changes."""
    def __init__(self, sample):
        self.sample = sample
        self.baseline = sample()
        self.changed = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.wait(.01):
            try:
                if self.sample() != self.baseline:
                    self.changed.set()
            except Exception:
                self.changed.set()

    def check(self):
        if self.sample() != self.baseline:
            self.changed.set()
        if self.changed.is_set():
            raise ManagementError("检测到前台窗口、鼠标位置或输入状态变化。")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)


def environment_sample():
    import win32api
    import win32gui
    return (win32gui.GetForegroundWindow(), win32gui.GetCursorPos(), win32api.GetLastInputInfo())


class NativeDraftDriver:
    def __init__(self, window, uia=None):
        if uia is None:
            import uiautomation as uia
        self.uia, self.window = uia, window
        self.stage = "binding"
        self.edit_identity = None
        self.edit = None
        self.pattern = None

    def header_structure(self):
        BindingService()._validate(self.window, scan_native())
        root = self.uia.ControlFromHandle(self.window.hwnd)
        if root.ProcessId != self.window.pid or root.ClassName != "mmui::MainWindow":
            raise ManagementError("绑定窗口身份已变化。")
        return structural_outline(root, root)

    def verify(self):
        self.stage = "binding"
        BindingService()._validate(self.window, scan_native())
        self.stage = "main"
        root = self.uia.ControlFromHandle(self.window.hwnd)
        if root.ProcessId != self.window.pid or root.ClassName != "mmui::MainWindow":
            raise ManagementError("绑定窗口身份已变化。")
        self.stage = "page"
        page = root.GroupControl(ClassName="mmui::ChatMessagePage")
        if not page.Exists(0):
            raise ManagementError("未找到聊天页面。")
        self.stage = "header"
        info = page.GroupControl(ClassName="mmui::ChatInfoView")
        if not info.Exists(0):
            raise ManagementError("未找到群标题区域。")
        prefix = "top_content_h_view.top_spacing_v_view.top_left_info_v_view.big_title_line_h_view."
        title = info.TextControl(AutomationId=prefix + "current_chat_name_label")
        count = info.TextControl(AutomationId=prefix + "current_chat_count_label")
        self.stage = "group_name"
        if not title.Exists(0) or title.Name != AUTHORIZED_GROUP:
            raise ManagementError("当前聊天不是已授权的同学群，或无法证明它是群聊。")
        self.stage = "group_type"
        if not count.Exists(0):
            raise ManagementError("未找到群聊标识。")
        self.stage = "input"
        edit = page.CustomControl(ClassName="mmui::XSplitterView").EditControl(ClassName="mmui::ChatInputField")
        if not edit.Exists(0):
            raise ManagementError("未找到输入框。")
        self.stage = "ownership"
        if edit.ProcessId != self.window.pid or not belongs_to_root(root, edit):
            raise ManagementError("输入控件不属于绑定窗口。")
        self.stage = "enabled"
        if not edit.IsEnabled:
            raise ManagementError("输入框被禁用。")
        self.stage = "value_available"
        if not edit.GetPropertyValue(30043):
            raise ManagementError("输入控件未提供可用Value接口。")
        self.stage = "incarnation"
        identity = tuple(edit.GetRuntimeId())
        if self.edit_identity is not None and identity != self.edit_identity:
            raise ManagementError("输入控件已重建。")
        self.stage = "value_pattern"
        pattern = edit.GetValuePattern()
        self.stage = "read_only"
        if pattern.IsReadOnly:
            raise ManagementError("输入控件为只读。")
        # Do not overwrite unrecognized rich draft content/attachments.
        self.stage = "attachments"
        if self.edit_identity is None and edit.GetChildren():
            raise ManagementError("输入框含附属控件，无法确认没有现有草稿或附件。")
        self.edit_identity, self.edit, self.pattern = identity, edit, pattern

    def read(self):
        self.stage = "draft_read"
        return self.pattern.Value  # In memory only; never put draft text in results/logs.

    def write(self, value):
        self.stage = "value_write"
        # Direct pattern call only. No focus, clicks, keystrokes, clipboard, Invoke
        # or fallback to the upstream library's edit/send methods.
        if not self.pattern.SetValue(value, waitTime=0):
            raise ManagementError("Value写入失败。")


def native_experiment(window, confirmed, group, *, readonly=False):
    if (confirmed is not True and not readonly) or group != AUTHORIZED_GROUP:
        raise ManagementError("授权仅限同学群草稿测试。")
    if readonly:
        import uiautomation as uia
        with uia.UIAutomationInitializerInThread():
            return diagnose_driver(NativeDraftDriver(window))
    import win32gui
    import win32process
    # A foreground WeChat window cannot establish background behavior.
    foreground = win32gui.GetForegroundWindow()
    if not foreground or win32process.GetWindowThreadProcessId(foreground)[1] == window.pid:
        raise ManagementError("请让管理器或其他应用位于前台后再测；程序不会自行切换焦点。")
    import uiautomation as uia
    monitor = EnvironmentMonitor(environment_sample)
    try:
        with uia.UIAutomationInitializerInThread():
            return experiment(NativeDraftDriver(window), monitor)
    finally:
        monitor.close()


def run_trial(window, *, confirmed, report_path=None, readonly=False):
    if confirmed is not True and not readonly:
        raise ManagementError("请确认本次只写入后清除，不发送。")
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    payload = {"window": asdict(window), "confirmed": True, "group": AUTHORIZED_GROUP}
    if readonly:
        payload.update(confirmed=False, readonly=True)
    try:
        completed = subprocess.run([str(executable), "-m", __name__], input=json.dumps(payload).encode("utf-8"),
                                   capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            raise ValueError("worker failed")
        result = json.loads(completed.stdout.decode("utf-8"))
    except Exception:
        # A timed-out COM request may still finish in the provider. Never auto retry.
        result = {"status": "unknown", "reason": "worker_timeout_or_failure", "real_start_allowed": False,
                  "cleared": False, "write_attempted": None, "write_observed": False, "clear_attempted": None,
                  "environment_changed": None}
    result["observed_at"] = time.time()
    result["mode"] = "read_only" if readonly else "draft_trial"
    if report_path is not None:
        try:
            atomic_json(report_path, result)
        except OSError:
            result["report_saved"] = False
    return result


def describe(result):
    if result["status"] == "diagnostic_ready":
        return "只读诊断通过：群聊、输入框归属、Value读取及空草稿校验均通过；未写入。可选择执行一次已授权草稿测试；真实机器人仍禁用。"
    if result.get("mode") == "read_only" and result["status"] == "unknown":
        return "只读诊断超时或未完成，未调用写入或清除；未自动重试。"
    if result["status"] == "observed_pass":
        return "本轮写入及清除已核对；10ms采样未观察到焦点/鼠标/输入变化。仅本轮观察，不证明收发或图片后台可用；真实启动仍禁用。"
    if result["status"] in {"unknown", "uncertain_cleanup"}:
        return "测试结果或清理状态不确定。请人工检查小号同学群草稿；如有TIANYI_DRAFT_TEST_开头文字，请手动清除。不要发送，也不要直接重试。真实启动仍禁用。"
    if result["reason"] in {"existing_draft", "draft_changed"}:
        return "发现现有草稿或输入变化，未写入、未清除原内容。"
    if result.get("failure"):
        failure = result["failure"]
        stage = STAGES.get(failure.get("stage"), STAGES["unknown"])
        code = failure.get("error_kind", "OtherError")
        effect = "未尝试写入、未清除。" if result.get("write_attempted") is False else "已尝试写入；请查看清理状态。"
        if result.get("structure", {}).get("status") == "metadata_only":
            return f"失败步骤：{stage}。已记录不含聊天文字的控件结构，用于版本适配；未写入、未发送。请反馈此结果，无需重复草稿测试。"
        return f"失败步骤：{stage}（{code}）。{effect}原因已保存到报告；未执行发送，真实启动仍禁用。"
    return "测试未通过或前提不满足；未执行发送。请确认小号当前为同学群、输入框无草稿，测试期间不要操作输入设备。真实启动仍禁用。"


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.buffer.read(8192))
        result = native_experiment(Window(**payload["window"]), payload.get("confirmed"), payload.get("group"), readonly=payload.get("readonly") is True)
        sys.stdout.buffer.write(json.dumps(result).encode("utf-8"))
    except Exception:
        # Do not serialize group names, account identifiers, draft text, raw errors.
        sys.exit(1)
