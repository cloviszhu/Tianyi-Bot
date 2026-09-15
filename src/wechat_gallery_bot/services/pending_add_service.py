from dataclasses import dataclass
from typing import Callable
import time


@dataclass(frozen=True)
class PendingAdd:
    keyword: str
    expires_at: float


class PendingAddService:
    def __init__(self, timeout: float = 60, clock: Callable[[], float] = time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self._items: dict[tuple[str, str], PendingAdd] = {}

    def start(self, chat: str, sender: str, keyword: str) -> None:
        now = self.clock()
        self._items = {k: v for k, v in self._items.items() if v.expires_at > now}
        self._items[chat, sender] = PendingAdd(keyword, now + self.timeout)

    def inspect(self, chat: str, sender: str) -> tuple[PendingAdd | None, bool]:
        item = self._items.get((chat, sender))
        if item is not None and self.clock() >= item.expires_at:
            del self._items[chat, sender]
            return None, True
        return item, False

    def cancel(self, chat: str, sender: str) -> bool:
        item, _ = self.inspect(chat, sender)
        self._items.pop((chat, sender), None)
        return item is not None
