"""Read-only UI message observations, not a lossless WeChat message transport.

Identity is a UI AutomationId plus payload, never a claim of a server message ID.
Unknown rows, discontinuity and ambiguous identities are visible to the operator.
"""
import hashlib
import json

from .common import ManagementError


def parse_row(control):
    cls, aid = control.ClassName, control.AutomationId or ""
    text = control.Name or ""
    if len(text) > 16384 or len(aid) > 2048:
        raise ManagementError("消息行超过读取限制。")
    kind = "unknown"
    if aid and cls == "mmui::ChatTextItemView":
        kind = "text"
    elif aid and cls == "mmui::ChatBubbleItemView" and text in {"图片", "[图片]"}:
        kind = "image"
    elif aid and cls == "mmui::ChatVoiceItemView":
        kind = "voice"
    # Keep unknown rows as ordering anchors, but never count them as messages.
    key = hashlib.sha256(json.dumps([cls, aid, text], ensure_ascii=False).encode()).hexdigest()
    return {"key": key, "kind": kind, "text": text if kind == "text" else ""}


class MessageObservation:
    def __init__(self):
        self.previous = None
        self.seen = set()
        self.count = 0
        self.gaps = 0
        self.latest = ""
        self.unknown = 0
        self.needs_baseline = False

    def interrupt(self):
        if not self.needs_baseline:
            self.gaps += 1
        self.needs_baseline = True

    def update(self, snapshot):
        rows = snapshot["messages"]
        if len(rows) > 1000:
            raise ManagementError("消息列表过大。")
        keys = [row["key"] for row in rows]
        self.unknown = sum(row["kind"] == "unknown" for row in rows)
        if len(set(keys)) != len(keys):
            self.interrupt()
            self.previous = keys
            return 0
        added = []
        if self.previous is None or self.needs_baseline:
            self.needs_baseline = False
        elif keys != self.previous:
            # Locate the previous tail and require a contiguous matching suffix.
            # Prepending history doesn't count; UI runtime-ID churn is irrelevant.
            if not self.previous or self.previous[-1] not in keys:
                self.gaps += 1
            else:
                end = keys.index(self.previous[-1]) + 1
                overlap = min(end, len(self.previous))
                if keys[end-overlap:end] != self.previous[-overlap:]:
                    self.gaps += 1
                else:
                    added = [r for r in rows[end:] if r["key"] not in self.seen and r["kind"] != "unknown"]
        self.previous = keys
        self.seen.update(keys)
        if len(self.seen) > 10000:
            raise ManagementError("本轮监听达到上限，请重新开始。")
        self.count += len(added)
        if added:
            last = added[-1]
            self.latest = last["text"].replace("\n", " ")[:80] if last["kind"] == "text" else "[" + last["kind"] + "]"
        return len(added)
