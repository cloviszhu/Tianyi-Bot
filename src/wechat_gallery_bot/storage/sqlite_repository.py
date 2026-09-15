import sqlite3
import hashlib
import json
from pathlib import Path

from ..models import GalleryImage


class SQLiteRepository:
    """Used behind the application's serialization lock; values are always bound."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY,
                namespace TEXT NOT NULL,
                keyword TEXT NOT NULL,
                local_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                uploader TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(namespace, keyword, sha256)
            );
            CREATE TABLE IF NOT EXISTS last_sent (
                namespace TEXT NOT NULL,
                keyword TEXT NOT NULL,
                image_id INTEGER NOT NULL REFERENCES images(id),
                PRIMARY KEY(namespace, keyword)
            );
            CREATE TABLE IF NOT EXISTS event_processing (
                event_key TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('processing','done','uncertain')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
        """)

    @staticmethod
    def event_key(chat: str, event_id: str) -> str:
        # No message body or raw account/group identifiers in this ledger.
        return hashlib.sha256(json.dumps([chat, event_id], ensure_ascii=False).encode()).hexdigest()

    def claim_event(self, chat: str, event_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO event_processing(event_key,status) VALUES(?,'processing') ON CONFLICT DO NOTHING",
                (self.event_key(chat, event_id),))
        return cursor.rowcount == 1

    def finish_event(self, chat: str, event_id: str, *, uncertain=False) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE event_processing SET status=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE event_key=? AND status='processing'",
                ("uncertain" if uncertain else "done", self.event_key(chat, event_id)))

    def processing_counts(self):
        return dict(self.connection.execute("SELECT status,COUNT(*) FROM event_processing GROUP BY status").fetchall())

    def insert(self, namespace: str, keyword: str, local_path: str, sha256: str, uploader: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO images(namespace,keyword,local_path,sha256,uploader) VALUES(?,?,?,?,?) "
                "ON CONFLICT(namespace,keyword,sha256) DO NOTHING",
                (namespace, keyword, local_path, sha256, uploader),
            )
        return cursor.rowcount == 1

    def images(self, namespace: str, keyword: str) -> list[GalleryImage]:
        rows = self.connection.execute(
            "SELECT * FROM images WHERE namespace=? AND keyword=? ORDER BY id", (namespace, keyword)
        ).fetchall()
        return [GalleryImage(**dict(row)) for row in rows]

    def last_sent(self, namespace: str, keyword: str) -> int | None:
        row = self.connection.execute(
            "SELECT image_id FROM last_sent WHERE namespace=? AND keyword=?", (namespace, keyword)
        ).fetchone()
        return row[0] if row else None

    def mark_sent(self, image: GalleryImage) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO last_sent VALUES(?,?,?) ON CONFLICT(namespace,keyword) "
                "DO UPDATE SET image_id=excluded.image_id",
                (image.namespace, image.keyword, image.id),
            )

    def close(self) -> None:
        self.connection.close()
