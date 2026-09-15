import tempfile
from pathlib import Path

from PIL import Image

from .adapters.fake import FakeAdapter
from .app import GalleryBot
from .models import MessageEvent
from .services.gallery_service import GalleryService
from .storage.sqlite_repository import SQLiteRepository


def run_demo() -> None:
    with tempfile.TemporaryDirectory(prefix="tianyi-demo-") as directory:
        root = Path(directory)
        source = root / "sample.png"
        Image.new("RGB", (16, 16), "orange").save(source)
        adapter = FakeAdapter([
            MessageEvent("演示群", "A", "text", "/加图 帕姆王", event_id="1"),
            MessageEvent("演示群", "B", "image", local_file_path=source, event_id="2"),
            MessageEvent("演示群", "A", "image", local_file_path=source, event_id="3"),
            MessageEvent("演示群", "B", "text", "帕姆王", event_id="4"),
        ])
        repository = SQLiteRepository(root / "bot.db")
        try:
            bot = GalleryBot(adapter, GalleryService(repository, root / "images"))
            bot.run()
            assert len(repository.images("演示群", "帕姆王")) == 1
            assert adapter.downloads == 1
            assert [kind for _, kind, _ in adapter.outgoing] == ["text", "text", "image"]
            for chat, kind, content in adapter.outgoing:
                print(f"{chat} <- {content if kind == 'text' else '[随机图片]'}")
            print("演示通过：只收录 A 的图片；B 成功取图。未连接微信。")
        finally:
            repository.close()
