"""Backend-independent window routing policy; never performs UI actions.

Native topology/pattern verification is still required before a real backend can
use this policy. Passing these checks alone does not certify background behavior.
"""
from dataclasses import dataclass

from .base import AdapterError


@dataclass(frozen=True)
class Surface:
    hwnd: int
    pid: int
    process_started: float
    kind: str
    owner: int
    identity: str  # A provider-issued window incarnation, not just an HWND.
    group: str = ""


class WindowScope:
    def __init__(self, main: Surface, groups):
        if main.kind != "main":
            raise AdapterError("A main window is required")
        self.main = main
        self.groups = frozenset(groups)
        self.invalid = False

    def reject(self):
        self.invalid = True
        raise AdapterError("窗口归属无法证明，当前会话已失效；禁止转向其他窗口。")

    def validate(self, surface: Surface, snapshot):
        if self.invalid:
            self.reject()
        by_handle = {s.hwnd: s for s in snapshot}
        if len(by_handle) != len(snapshot) or by_handle.get(self.main.hwnd) != self.main or by_handle.get(surface.hwnd) != surface:
            self.reject()
        if sum(s.kind == "main" and s.pid == self.main.pid for s in snapshot) != 1:
            self.reject()
        cursor, seen = surface, set()
        while cursor != self.main:
            if cursor.hwnd in seen or (cursor.pid, cursor.process_started) != (self.main.pid, self.main.process_started):
                self.reject()
            seen.add(cursor.hwnd)
            cursor = by_handle.get(cursor.owner)
            if cursor is None:
                self.reject()
        return surface

    def select(self, kind, parent: Surface, snapshot, *, group="", before=()):
        self.validate(parent, snapshot)
        if kind == "group" and (parent != self.main or group not in self.groups):
            self.reject()
        if kind not in {"group", "preview", "menu"}:
            self.reject()
        if kind == "preview" and parent.kind != "group":
            self.reject()
        if kind == "menu" and parent.kind not in {"group", "preview"}:
            self.reject()
        candidates = [s for s in snapshot if s.kind == kind and s.owner == parent.hwnd
                      and s.pid == self.main.pid and s.process_started == self.main.process_started
                      and (kind != "group" or s.group == group)]
        if len(candidates) != 1:
            self.reject()
        selected = self.validate(candidates[0], snapshot)
        # A preview/menu must have appeared in this operation; no stale popup reuse.
        if kind in {"preview", "menu"} and (not before or selected in before):
            self.reject()
        return selected


BACKGROUND_BLOCKER = (
    "真实启动已禁用：尚无通过验证的全后台微信后端。免费库现有文字/图片路径会使用焦点、鼠标或剪贴板。"
    "仅有窗口绑定或UIA接口存在不能证明后台收发可用；不会退回前台操作。"
)


def require_verified_background_backend():
    # Deliberately no settings/env/checkbox override. Replace only after actual
    # implementation + per-version background tests + explicit account consent.
    raise AdapterError(BACKGROUND_BLOCKER)
