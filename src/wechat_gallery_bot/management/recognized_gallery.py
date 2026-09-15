"""Bridge recognized text to the existing local gallery, never to WeChat sending."""
from pathlib import Path
from ..services.command_parser import parse_command
from ..services.gallery_service import GalleryService
from ..storage.sqlite_repository import SQLiteRepository
from .common import ManagementError


def candidates(result):
    texts = list(result.get("uia", []))
    texts.extend(r["text"] for r in result.get("lines", []) if r.get("confidence", 0) >= .9)
    found = []
    for text in texts:
        try:
            command = parse_command(text)
        except ValueError:
            continue
        if command.kind in {"add", "lookup"}:
            value = (command.kind, command.keyword)
            if value not in found:
                found.append(value)
    return found[:200]  # deduplicates proposals, NOT actual message events


def execute(root, settings, group, kind, keyword, source=None):
    if group not in settings["groups"]:
        raise ManagementError("该群不在已保存配置中，请先保存配置。")
    repo = SQLiteRepository(Path(root) / "bot.db")
    try:
        gallery = GalleryService(repo, Path(root) / "images", settings["max_image_mb"] * 1024 * 1024)
        if kind == "add":
            if source is None:
                raise ManagementError("缺少原图文件；不能用截图替代。")
            inserted, count = gallery.add(group, keyword, Path(source), "local-operator")
            return {"status": "added" if inserted else "duplicate", "count": count}
        if kind != "lookup":
            raise ManagementError("不支持的图库操作。")
        chosen = gallery.choose(group, keyword)
        return {"status": "found" if chosen else "missing", "image_id": chosen.id if chosen else None}
    finally:
        repo.close()
