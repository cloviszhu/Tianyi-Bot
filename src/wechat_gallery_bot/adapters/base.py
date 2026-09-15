from pathlib import Path
from typing import Callable, Protocol

from ..models import MessageEvent


class AdapterError(RuntimeError):
    pass


class Adapter(Protocol):
    def run(self, handler: Callable[[MessageEvent], None]) -> None: ...
    def download_image(self, event: MessageEvent) -> Path: ...
    def send_text(self, chat_key: str, text: str) -> None: ...
    def send_image(self, chat_key: str, path: Path) -> None: ...
