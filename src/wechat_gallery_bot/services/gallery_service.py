import hashlib
import io
import os
import random
import tempfile
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..models import GalleryImage
from ..storage.sqlite_repository import SQLiteRepository
from .command_parser import normalize_keyword


class InvalidImage(ValueError):
    pass


class GalleryService:
    def __init__(self, repository: SQLiteRepository, image_root: Path, max_image_bytes: int = 20 * 1024 * 1024,
                 rng: random.Random | None = None, shared: bool = False):
        self.repository = repository
        self.image_root = image_root.resolve()
        self.image_root.mkdir(parents=True, exist_ok=True)
        self.max_image_bytes = max_image_bytes
        self.rng = rng or random.Random()
        self.shared = shared

    def namespace(self, chat: str) -> str:
        return "shared" if self.shared else chat

    def path_for(self, image: GalleryImage) -> Path:
        path = (self.image_root / image.local_path).resolve()
        if not path.is_relative_to(self.image_root) or path == self.image_root:
            raise InvalidImage("Unsafe stored image path")
        return path

    def add(self, chat: str, keyword: str, source: Path, sender: str) -> tuple[bool, int]:
        keyword = normalize_keyword(keyword)
        with Path(source).open("rb") as stream:
            content = stream.read(self.max_image_bytes + 1)
        if not content or len(content) > self.max_image_bytes:
            raise InvalidImage("Image is empty or too large")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.width * image.height > 25_000_000:
                        raise InvalidImage("Image pixel count exceeds 25 million")
                    extension = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp"}.get(image.format)
                    if extension is None:
                        raise InvalidImage("Unsupported image format")
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    image.load()  # verify() alone does not decode JPEG pixel data.
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise InvalidImage("Invalid image data") from exc
        digest = hashlib.sha256(content).hexdigest()
        destination = self.image_root / (digest + extension)
        if destination.is_symlink():
            raise InvalidImage("Unsafe destination")
        # Rewriting identical content also repairs a missing/corrupt existing blob.
        fd, temp_name = tempfile.mkstemp(prefix=".image-", dir=self.image_root)
        temporary = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        namespace = self.namespace(chat)
        inserted = self.repository.insert(namespace, keyword, destination.name, digest, sender)
        return inserted, len(self.repository.images(namespace, keyword))

    def choose(self, chat: str, keyword: str) -> GalleryImage | None:
        keyword = normalize_keyword(keyword)
        namespace = self.namespace(chat)
        records = self.repository.images(namespace, keyword)
        if not records:
            return None
        available = [image for image in records if self.path_for(image).is_file()]
        if not available:
            raise InvalidImage("Stored images are missing")
        last = self.repository.last_sent(namespace, keyword)
        choices = [image for image in available if image.id != last] if len(available) > 1 else available
        return self.rng.choice(choices)

    def mark_sent(self, image: GalleryImage) -> None:
        self.repository.mark_sent(image)
