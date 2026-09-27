from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .models import DocumentState, Preferences


class Storage:
    """Transactional recovery and bounded per-document snapshots in SQLite."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.assets = directory / "attachments"
        self.assets.mkdir(exist_ok=True)
        self.connection = sqlite3.connect(directory / "editor.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY, document_id TEXT NOT NULL,
                created REAL NOT NULL, value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS versions_document ON versions(document_id, created);
        """)

    def put(self, key: str, value: dict) -> None:
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def get(self, key: str) -> dict | None:
        row = self.connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def autosave(self, document: DocumentState) -> None:
        self.put("active", asdict(document))

    def load_draft(self) -> DocumentState | None:
        data = self.get("active")
        return DocumentState.from_dict(data) if data else None

    def save_preferences(self, prefs: Preferences) -> None:
        self.put("preferences", asdict(prefs))

    def preferences(self) -> Preferences:
        return Preferences.from_dict(self.get("preferences") or {})

    def snapshot(self, document: DocumentState) -> None:
        if not document.markdown.strip():
            return
        row = self.connection.execute("SELECT value FROM versions WHERE document_id=? ORDER BY id DESC LIMIT 1", (document.id,)).fetchone()
        if row and json.loads(row[0])["markdown"] == document.markdown:
            return
        with self.connection:
            self.connection.execute("INSERT INTO versions(document_id, created, value) VALUES (?, ?, ?)", (document.id, time.time(), json.dumps(asdict(document))))
            self.connection.execute("DELETE FROM versions WHERE document_id=? AND id NOT IN (SELECT id FROM versions WHERE document_id=? ORDER BY id DESC LIMIT 30)", (document.id, document.id))

    def versions(self, document_id: str) -> list[tuple[int, float, DocumentState]]:
        return [(row[0], row[1], DocumentState.from_dict(json.loads(row[2]))) for row in self.connection.execute("SELECT id, created, value FROM versions WHERE document_id=? ORDER BY id DESC", (document_id,))]

    def remember_file(self, path: str) -> None:
        if path:
            recent = self.get("recent") or {"paths": []}
            self.put("recent", {"paths": ([path] + [p for p in recent["paths"] if p != path])[:10]})

    def close(self) -> None:
        self.connection.close()
