"""Read-only, user-started current-group message reading; no input or sending."""
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from .common import ManagementError, atomic_json
from .window_binding import Window, BindingService, scan_native


STAGES = {"request": "读取监听请求", "binding": "微信窗口复核", "locate": "定位群标题和消息区",
          "group": "核对群名与群聊标识", "messages": "读取消息标识", "recheck": "结束前复核", "timeout": "控件查询超时"}
stage = "request"
diagnostics = {}
REASONS = {"searching": "搜索中", "limit": "搜索达到上限", "candidates": "候选缺失或重复", "list_missing": "消息区没有列表", "located": "已定位"}
CLASSES = {"mmui::MainWindow", "mmui::ChatMessagePage", "mmui::ChatInfoView", "mmui::MessageView", "mmui::XSplitterView", "mmui::ChatInputField"}


def walk_structure(root, prune):
    """Keep a shared node budget, but measure depth relative to each search scope."""
    pending = [(root, 0, -1)]
    while pending:
        control, depth, parent = pending.pop(0)
        if len(diagnostics["nodes"]) >= 500 or depth > 24:
            diagnostics["reason"] = "limit"
            raise ManagementError("结构搜索超出范围")
        cls = control.ClassName
        node_index = len(diagnostics["nodes"])
        diagnostics["nodes"].append({"parent": parent, "depth": depth, "class": cls if cls in CLASSES else "other"})
        yield control
        if cls in prune:
            continue
        children = control.GetChildren()
        if len(children) + len(pending) + len(diagnostics["nodes"]) > 500:
            diagnostics["reason"] = "limit"
            raise ManagementError("结构搜索超出范围")
        pending.extend((child, depth + 1, node_index) for child in children)


def locate_controls(root):
    """Find one page first; never traverse message bodies or the input editor."""
    global diagnostics
    diagnostics = {"reason": "searching", "counts": [0, 0, 0], "nodes": []}
    opaque = {"mmui::MessageView", "mmui::ChatInputField"}
    pages = [c for c in walk_structure(root, opaque | {"mmui::ChatMessagePage"})
             if c.ClassName == "mmui::ChatMessagePage"]
    if len(pages) != 1:
        diagnostics["reason"] = "candidates"
        raise ManagementError("聊天页面缺失或重复")
    matches = {"title": [], "count": [], "view": []}
    for control in walk_structure(pages[0], opaque):
        aid = control.AutomationId or ""
        for key, suffix in [("title", "current_chat_name_label"), ("count", "current_chat_count_label")]:
            if aid == suffix or aid.endswith("." + suffix):
                matches[key].append(control)
        diagnostics["counts"] = [len(matches[k]) for k in ("title", "count", "view")]
        if control.ClassName == "mmui::MessageView":
            matches["view"].append(control)
            diagnostics["counts"][2] = len(matches["view"])
    counts = "/".join(str(len(matches[k])) for k in ("title", "count", "view"))
    if any(len(value) != 1 for value in matches.values()):
        diagnostics["reason"] = "candidates"
        raise ManagementError("结构候选数（标题/群标识/消息区）=" + counts)
    view = matches["view"][0]
    listing = view.ListControl(searchDepth=6)
    if not listing.Exists(0):
        diagnostics["reason"] = "list_missing"
        raise ManagementError("消息区内未找到列表")
    diagnostics["reason"] = "located"
    return matches["title"][0], matches["count"][0], listing


def failure(exc):
    return {"stage": stage if stage in STAGES else "request",
            "diagnostics": diagnostics,
            "kind": type(exc).__name__ if type(exc).__name__ in {"ManagementError", "COMError", "PermissionError", "TimeoutExpired", "AttributeError"} else "OtherError"}


def describe_failure(error):
    detail = error.get("diagnostics", {})
    counts = detail.get("counts")
    suffix = ("；" + REASONS.get(detail.get("reason"), "结构未知") + "，标题/群标识/消息区=" + "/".join(map(str, counts))) if counts else ""
    return "监听停止：" + STAGES.get(error.get("stage"), "未知步骤") + "（" + error.get("kind", "OtherError") + "）" + suffix + "。未发送。"


def sanitize_diagnostics(value):
    if not isinstance(value, dict):
        return {}
    counts = value.get("counts")
    if not isinstance(counts, list) or len(counts) != 3 or any(type(n) is not int or not 0 <= n <= 500 for n in counts):
        return {}
    nodes = []
    for node in value.get("nodes", [])[:500]:
        if not isinstance(node, dict):
            continue
        parent, depth = node.get("parent"), node.get("depth")
        if type(parent) is int and -1 <= parent < 500 and type(depth) is int and 0 <= depth <= 24:
            nodes.append({"parent": parent, "depth": depth, "class": node.get("class") if node.get("class") in CLASSES else "other"})
    return {"reason": value.get("reason") if value.get("reason") in REASONS else "searching", "counts": counts, "nodes": nodes}


def record_failure(error):
    path = Path.home() / ".tianyi-bot/local-workspace/last-group-listener.json"
    try:
        atomic_json(path, {"version": "0.6.8", "status": "failed", **error})
        return " 已保存结构诊断。"
    except Exception:
        return " 结构诊断保存失败。"


class Observation:
    def __init__(self):
        self.view = None
        self.seen = set()
        self.count = 0

    def update(self, snapshot):
        view = tuple(snapshot["view"])
        ids = {tuple(i) for i in snapshot["items"]}
        if not view or len(ids) > 1000:
            raise ManagementError("消息列表结构无效，监听已停止。")
        if self.view is None:
            self.view, self.seen = view, ids
            return 0
        if self.view != view:
            raise ManagementError("消息列表已重建，请重新开始监听。")
        new = ids - self.seen
        if len(self.seen | ids) > 10000:
            raise ManagementError("本轮监听达到上限，请重新开始。")
        self.seen |= ids
        self.count += len(new)
        return len(new)


def snapshot_native(window, group):
    global stage
    import uiautomation as uia
    from .draft_trial import belongs_to_root
    if not isinstance(group, str) or not group.strip() or len(group) > 200:
        raise ManagementError("请填写一个完整群名。")
    stage = "binding"
    BindingService()._validate(window, scan_native(child=True))
    with uia.UIAutomationInitializerInThread():
        root = uia.ControlFromHandle(window.hwnd)
        stage = "locate"
        title, count, listing = locate_controls(root)

        def verify_group():
            if (not title.Exists(0) or not count.Exists(0) or not listing.Exists(0)
                    or any(not belongs_to_root(root, c) for c in (title, count, listing))
                    or title.Name != group):
                raise ManagementError("请手动打开配置的群；群标题或消息列表不匹配，监听已停止。")

        stage = "group"
        verify_group()
        stage = "messages"
        children = listing.GetChildren()
        if len(children) > 1000:
            raise ManagementError("消息列表过大，监听已停止。")
        items = []
        messages = []
        from .message_observation import parse_row
        for item in children:
            if item.ControlTypeName == "ListItemControl":
                if not belongs_to_root(root, item):
                    raise ManagementError("消息归属变化，监听已停止。")
                identity = list(item.GetRuntimeId())
                if not identity:
                    raise ManagementError("消息标识不可用。")
                items.append(identity)
                messages.append(parse_row(item))
        stage = "recheck"
        verify_group()
        BindingService()._validate(window, scan_native(child=True))
        return {"view": list(listing.GetRuntimeId()), "items": items, "messages": messages}


def read_snapshot(window, group):
    executable = Path(sys.executable).with_name("python.exe")
    try:
        result = subprocess.run([str(executable), "-m", __name__],
            input=json.dumps({"window": asdict(window), "group": group}).encode("utf-8"),
            capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        error = {"stage": "timeout", "kind": "TimeoutExpired"}
        raise ManagementError(describe_failure(error) + record_failure(error)) from None
    if result.returncode:
        try:
            error = json.loads(result.stdout.decode("utf-8"))["error"]
            if error["stage"] not in STAGES or error["kind"] not in {"ManagementError", "COMError", "PermissionError", "TimeoutExpired", "AttributeError", "OtherError"}:
                raise ValueError()
            error = {"stage": error["stage"], "kind": error["kind"], "diagnostics": sanitize_diagnostics(error.get("diagnostics"))}
        except Exception:
            error = {"stage": "request", "kind": "OtherError"}
        raise ManagementError(describe_failure(error) + record_failure(error))
    return json.loads(result.stdout.decode("utf-8"))


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.buffer.read(16384))
        result = snapshot_native(Window(**payload["window"]), payload["group"])
        sys.stdout.buffer.write(json.dumps(result).encode("utf-8"))
    except Exception as exc:
        sys.stdout.buffer.write(json.dumps({"error": failure(exc)}).encode("utf-8"))
        sys.exit(1)  # Never output chat text, account identifiers or exception contents.
