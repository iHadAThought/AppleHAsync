"""SQLite sync metadata for content hashes / echo suppression."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class SyncMetaStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_hash (
                kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                uid TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (kind, source_id, uid)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS echo_suppress (
                kind TEXT NOT NULL,
                uid TEXT NOT NULL,
                until_ts REAL NOT NULL,
                PRIMARY KEY (kind, uid)
            )
            """
        )
        self._conn.commit()

    def set_hash(self, kind: str, source_id: str, uid: str, content_hash: str) -> None:
        self._conn.execute(
            """
            INSERT INTO item_hash(kind, source_id, uid, content_hash, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, source_id, uid) DO UPDATE SET
                content_hash=excluded.content_hash,
                updated_at=excluded.updated_at
            """,
            (kind, source_id, uid, content_hash, time.time()),
        )
        self._conn.commit()

    def get_hash(self, kind: str, source_id: str, uid: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM item_hash WHERE kind=? AND source_id=? AND uid=?",
            (kind, source_id, uid),
        ).fetchone()
        return row[0] if row else None

    def mark_echo(self, kind: str, uid: str, seconds: float) -> None:
        self._conn.execute(
            """
            INSERT INTO echo_suppress(kind, uid, until_ts) VALUES (?, ?, ?)
            ON CONFLICT(kind, uid) DO UPDATE SET until_ts=excluded.until_ts
            """,
            (kind, uid, time.time() + seconds),
        )
        self._conn.commit()

    def is_echo_suppressed(self, kind: str, uid: str) -> bool:
        row = self._conn.execute(
            "SELECT until_ts FROM echo_suppress WHERE kind=? AND uid=?",
            (kind, uid),
        ).fetchone()
        if not row:
            return False
        if row[0] < time.time():
            self._conn.execute(
                "DELETE FROM echo_suppress WHERE kind=? AND uid=?", (kind, uid)
            )
            self._conn.commit()
            return False
        return True

    def close(self) -> None:
        self._conn.close()
