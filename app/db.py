from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    media_type TEXT NOT NULL DEFAULT 'text/plain',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    page INTEGER,
                    section TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    route TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    events TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "documents", "filename", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "documents", "media_type", "TEXT NOT NULL DEFAULT 'text/plain'")
            self._ensure_column(conn, "chunks", "page", "INTEGER")
            self._ensure_column(conn, "chunks", "section", "TEXT")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_document(
        self,
        title: str,
        source: str,
        content: str,
        filename: str = "",
        media_type: str = "text/plain",
    ) -> str:
        document_id = f"doc_{uuid4().hex[:12]}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO documents(id, title, source, content, filename, media_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (document_id, title, source, content, filename, media_type, utc_now()),
            )
        return document_id

    def add_chunks(self, document_id: str, chunks: Iterable[tuple[int, str, list[float], int | None, str | None]]) -> int:
        rows = [
            (f"chk_{uuid4().hex[:12]}", document_id, position, content, json.dumps(embedding), page, section, utc_now())
            for position, content, embedding, page, section in chunks
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO chunks(id, document_id, position, content, embedding, page, section, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def list_chunks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.document_id, c.position, c.content, c.embedding, c.page, c.section,
                       d.title, d.source, d.filename, d.media_type
                FROM chunks c JOIN documents d ON d.id = c.document_id
                ORDER BY c.document_id, c.position
                """
            ).fetchall()
        return [
            {
                **dict(row),
                "embedding": json.loads(row["embedding"]),
            }
            for row in rows
        ]

    def chunk_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT d.id, d.title, d.source, d.filename, d.media_type, d.created_at, COUNT(c.id) AS chunks
                FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
                GROUP BY d.id ORDER BY d.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, document_id: str) -> bool:
        with self.connect() as conn:
            result = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return result.rowcount > 0

    def run_summary(self) -> dict[str, float | int]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, AVG(latency_ms) AS average_latency_ms, AVG(verified) AS verified_rate FROM runs"
            ).fetchone()
        return {
            "runs": int(row["runs"] or 0),
            "average_latency_ms": round(float(row["average_latency_ms"] or 0), 2),
            "verified_rate": round(float(row["verified_rate"] or 0), 4),
        }

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, utc_now()),
            )

    def get_messages(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at FROM (
                    SELECT id, role, content, created_at FROM messages
                    WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_run(self, run: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(run_id, session_id, query, route, answer, verified, latency_ms, events, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["session_id"],
                    run["query"],
                    run["route"],
                    run["answer"],
                    int(run["verified"]),
                    run["latency_ms"],
                    json.dumps(run["events"], ensure_ascii=False),
                    utc_now(),
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["verified"] = bool(result["verified"])
        result["events"] = json.loads(result["events"])
        return result
