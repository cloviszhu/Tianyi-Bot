from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MessageEvent:
    chat_key: str
    sender_key: str
    message_type: str
    text: str = ""
    local_file_path: Path | None = None
    is_self: bool = False
    timestamp: float = 0.0  # adapter receipt wall time, not a claimed server timestamp
    sender_name: str | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class GalleryImage:
    id: int
    namespace: str
    keyword: str
    local_path: str
    sha256: str
    uploader: str
    created_at: str
