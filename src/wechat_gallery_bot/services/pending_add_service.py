from dataclasses import dataclass
from typing import Callable
import time
import math

from .command_parser import normalize_keyword


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


class PersistentPendingAddService:
    """Wall-clock expiry survives process restart; identities stored as hashes.

    Use only within the bot's single-owner data directory and serialization lock.
    A backwards clock jump invalidates a pending request instead of extending it.
    """
    def __init__(self, repository, timeout=60, clock=time.time):
        if not math.isfinite(timeout) or not 1 <= timeout <= 600:
            raise ValueError("Invalid pending timeout")
        self.repository, self.timeout, self.clock = repository, timeout, clock
        with repository.connection:
            repository.connection.execute("CREATE TABLE IF NOT EXISTS pending_add (identity TEXT PRIMARY KEY, keyword TEXT NOT NULL, started REAL NOT NULL, expires REAL NOT NULL)")

    def _now(self):
        now = self.clock()
        if not math.isfinite(now):
            raise ValueError("Invalid clock")
        return now

    def _key(self, chat, sender):
        if not chat or not sender:
            raise ValueError("Pending identity required")
        return self.repository.event_key(chat, sender)

    def start(self, chat, sender, keyword):
        key, keyword, now = self._key(chat, sender), normalize_keyword(keyword), self._now()
        db = self.repository.connection
        with db:
            db.execute("DELETE FROM pending_add WHERE expires<=? OR started>?", (now, now))
            db.execute("INSERT INTO pending_add VALUES(?,?,?,?) ON CONFLICT(identity) DO UPDATE SET keyword=excluded.keyword,started=excluded.started,expires=excluded.expires", (key, keyword, now, now+self.timeout))

    def inspect(self, chat, sender):
        key, now = self._key(chat, sender), self._now()
        db = self.repository.connection
        with db:
            row = db.execute("SELECT keyword,started,expires FROM pending_add WHERE identity=?", (key,)).fetchone()
            if row is None:
                return None, False
            if not row[1] <= now < row[2]:
                db.execute("DELETE FROM pending_add WHERE identity=?", (key,))
                return None, True
            return PendingAdd(row[0], row[2]), False

    def cancel(self, chat, sender):
        item, _ = self.inspect(chat, sender)
        with self.repository.connection:
            self.repository.connection.execute("DELETE FROM pending_add WHERE identity=?", (self._key(chat,sender),))
        return item is not None
