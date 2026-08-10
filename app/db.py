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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '新建对话',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    route TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    events TEXT NOT NULL,
                    errors TEXT NOT NULL DEFAULT '[]',
                    citations TEXT NOT NULL DEFAULT '[]',
                    thinking_mode TEXT NOT NULL DEFAULT 'disabled',
                    retrieval_query TEXT NOT NULL DEFAULT '',
                    rewrite_reason TEXT NOT NULL DEFAULT 'not_applicable',
                    verify_reason TEXT NOT NULL DEFAULT 'not_applicable',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_session_created ON runs(session_id, created_at);
                """
            )
            self._ensure_column(conn, "documents", "filename", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "documents", "media_type", "TEXT NOT NULL DEFAULT 'text/plain'")
            self._ensure_column(conn, "chunks", "page", "INTEGER")
            self._ensure_column(conn, "chunks", "section", "TEXT")
            self._ensure_column(conn, "runs", "errors", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "runs", "citations", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "runs", "thinking_mode", "TEXT NOT NULL DEFAULT 'disabled'")
            self._ensure_column(conn, "runs", "retrieval_query", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "runs", "rewrite_reason", "TEXT NOT NULL DEFAULT 'not_applicable'")
            self._ensure_column(conn, "runs", "verify_reason", "TEXT NOT NULL DEFAULT 'not_applicable'")
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at)
                SELECT session_id, '历史会话', MIN(created_at), MAX(created_at)
                FROM runs GROUP BY session_id
                """
            )
            conn.execute(
                """
                UPDATE sessions
                SET title = COALESCE(
                    (
                        SELECT CASE
                            WHEN LENGTH(TRIM(r.query)) > 32 THEN SUBSTR(TRIM(r.query), 1, 32) || '…'
                            ELSE TRIM(r.query)
                        END
                        FROM runs r WHERE r.session_id = sessions.id
                        ORDER BY r.created_at ASC LIMIT 1
                    ),
                    title
                )
                WHERE title = '历史会话'
                """
            )

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

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """Look up one chunk for citation verification without re-running retrieval."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.position, c.content, c.page, c.section,
                       d.title, d.source, d.filename, d.media_type
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.id = ?
                """,
                (chunk_id,),
            ).fetchone()
        return dict(row) if row else None

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

    def document_catalog(self, max_sections_per_document: int = 8) -> list[dict[str, Any]]:
        """Return compact, non-content metadata for automatic source routing.

        The planner sees titles, filenames and structural headings—not the
        document body—so it can choose a provenance boundary without answering
        the user's question from untrusted document text.
        """
        documents = self.list_documents()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT document_id, section, MIN(position) AS first_position
                FROM chunks
                WHERE section IS NOT NULL AND section != ''
                GROUP BY document_id, section
                ORDER BY document_id, first_position
                """
            ).fetchall()
        sections_by_document: dict[str, list[str]] = {}
        for row in rows:
            sections = sections_by_document.setdefault(row["document_id"], [])
            if len(sections) < max_sections_per_document:
                sections.append(row["section"])
        return [
            {
                "id": document["id"],
                "title": document["title"],
                "filename": document["filename"],
                "source": document["source"],
                "media_type": document["media_type"],
                "sections": sections_by_document.get(document["id"], []),
            }
            for document in documents
        ]

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

    def create_session(self) -> dict[str, str]:
        session_id = f"ses_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, "新建对话", now, now),
            )
        return {"id": session_id, "title": "新建对话", "created_at": now, "updated_at": now, "runs": 0}

    def ensure_session(self, session_id: str, initial_title: str = "新建对话") -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, initial_title, now, now),
            )

    def set_session_title(self, session_id: str, title: str) -> None:
        """Update a title explicitly generated for an existing conversation."""
        compact = " ".join(title.split()).strip()
        if not compact:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (compact[:48], utc_now(), session_id),
            )

    def session_run_count(self, session_id: str) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM runs WHERE session_id = ?", (session_id,)).fetchone()[0])

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, COUNT(r.run_id) AS runs
                FROM sessions s LEFT JOIN runs r ON r.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session_turns(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
            ).fetchall()
        return [self._decode_run(dict(row)) for row in rows]

    def get_recent_verified_turns(self, session_id: str, limit: int = 3) -> list[dict[str, str]]:
        """Return trusted short-term conversational context in chronological order.

        A previous assistant answer is useful for resolving pronouns, but an
        unverified answer must not influence a later retrieval query.  Runs
        are the authoritative place where the answer and its verification
        result are stored together, unlike the generic messages table.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT query, answer, created_at FROM (
                    SELECT query, answer, created_at
                    FROM runs
                    WHERE session_id = ? AND verified = 1
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_run(self, run: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (run["session_id"], "新建对话", now, now),
            )
            conn.execute(
                """
                UPDATE sessions
                SET title = CASE WHEN title IN ('', '新建对话', '历史会话') THEN ? ELSE title END,
                    updated_at = ?
                WHERE id = ?
                """,
                (self._session_title(run["query"]), now, run["session_id"]),
            )
            conn.execute(
                """
                INSERT INTO runs(run_id, session_id, query, retrieval_query, rewrite_reason, verify_reason, route, answer, verified, latency_ms, events, errors, citations, thinking_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["session_id"],
                    run["query"],
                    run.get("retrieval_query", run["query"]),
                    run.get("rewrite_reason", "not_applicable"),
                    run.get("verify_reason", "not_applicable"),
                    run["route"],
                    run["answer"],
                    int(run["verified"]),
                    run["latency_ms"],
                    json.dumps(run["events"], ensure_ascii=False),
                    json.dumps(run.get("errors", []), ensure_ascii=False),
                    json.dumps(run.get("citations", []), ensure_ascii=False),
                    run.get("thinking_mode", "disabled"),
                    now,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return self._decode_run(dict(row))

    @staticmethod
    def _session_title(query: str) -> str:
        compact = " ".join(query.split())
        return (compact[:32] + "…") if len(compact) > 32 else (compact or "新建对话")

    @staticmethod
    def _decode_run(result: dict[str, Any]) -> dict[str, Any]:
        result["verified"] = bool(result["verified"])
        result["events"] = json.loads(result["events"])
        result["errors"] = json.loads(result["errors"])
        result["citations"] = json.loads(result.get("citations") or "[]")
        return result
