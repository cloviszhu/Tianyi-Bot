from pathlib import Path
from typing import Callable, Iterable

from ..models import MessageEvent
from .base import AdapterError


class FakeAdapter:
    def __init__(self, events: Iterable[MessageEvent] = ()):
        self.events = list(events)
        self.outgoing: list[tuple[str, str, str]] = []
        self.downloads = 0

    def run(self, handler: Callable[[MessageEvent], None]) -> None:
        for event in self.events:
            handler(event)

    def download_image(self, event: MessageEvent) -> Path:
        self.downloads += 1
        if event.local_file_path is None:
            raise AdapterError("Image unavailable")
        return Path(event.local_file_path)

    def send_text(self, chat_key: str, text: str) -> None:
        self.outgoing.append((chat_key, "text", text))

    def send_image(self, chat_key: str, path: Path) -> None:
        if not path.is_file():
            raise AdapterError("Image unavailable")
        self.outgoing.append((chat_key, "image", str(path)))
