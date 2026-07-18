from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from nexus.sessions.models import FileChange, Session, StoredMessage
from nexus.sessions.persistence import sanitize_message, sanitize_metadata

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, project_dir TEXT NOT NULL, branch TEXT NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
 role TEXT NOT NULL, content TEXT NOT NULL, metadata TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS file_changes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, path TEXT NOT NULL,
 previous_content TEXT, previous_hash TEXT, new_hash TEXT NOT NULL,
 created INTEGER NOT NULL, undone INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
"""

SCHEMA_VERSION = 4


class SessionDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".nexus" / "sessions.db"
        self.connection = self._connect(self.path)

    def _connect(self, path: Path) -> sqlite3.Connection:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._migrate(connection)
            return connection
        except (OSError, sqlite3.OperationalError):
            if path != self.path:
                raise
            fallback_path = Path(tempfile.gettempdir()) / "thecode" / "sessions.db"
            self.path = fallback_path
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(fallback_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._migrate(connection)
            return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        connection.executescript(SCHEMA)
        if version < 2:
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_changes_session_id ON file_changes(session_id, id);
                DELETE FROM messages
                WHERE lower(trim(content)) IN
                  ('thinking...', 'thinking…', 'ready', 'loaded project', 'response completed');
                """
            )
        if version < 3:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(file_changes)").fetchall()
            }
            if "previous_bytes" not in columns:
                connection.execute("ALTER TABLE file_changes ADD COLUMN previous_bytes BLOB")
        if version < 4:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(file_changes)").fetchall()
            }
            if "operation" not in columns:
                connection.execute("ALTER TABLE file_changes ADD COLUMN operation TEXT NOT NULL DEFAULT 'edit'")
            if "source_path" not in columns:
                connection.execute("ALTER TABLE file_changes ADD COLUMN source_path TEXT")
            if "destination_previous_bytes" not in columns:
                connection.execute("ALTER TABLE file_changes ADD COLUMN destination_previous_bytes BLOB")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()

    def save_session(self, session: Session) -> None:
        values = session.model_dump(mode="json")
        self.connection.execute(
            "INSERT OR REPLACE INTO sessions VALUES (:id,:project_dir,:branch,:provider,:model,:created_at,:updated_at,:status,:summary)", values
        )
        self.connection.commit()

    def list_sessions(self) -> list[Session]:
        rows = self.connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [Session.model_validate(dict(row)) for row in rows]

    def get_session(self, session_id: str) -> Session | None:
        row = self.connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return Session.model_validate(dict(row)) if row else None

    def delete_session(self, session_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def add_message(self, message: StoredMessage) -> None:
        content = sanitize_message(message.role, message.content)
        if content is None:
            return
        metadata = sanitize_metadata(message.metadata)
        self.connection.execute(
            "INSERT INTO messages(session_id,role,content,metadata) VALUES (?,?,?,?)",
            (message.session_id, message.role, content, json.dumps(metadata, ensure_ascii=False)),
        )
        self.connection.commit()

    def update_session_summary(self, session_id: str, summary: str) -> None:
        safe = sanitize_message("assistant", summary)
        if safe is None:
            return
        self.connection.execute(
            "UPDATE sessions SET summary=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (safe[:4000], session_id),
        )
        self.connection.commit()

    def list_messages(self, session_id: str) -> list[StoredMessage]:
        rows = self.connection.execute(
            "SELECT session_id,role,content,metadata FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [
            StoredMessage(
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def add_change(self, change: FileChange) -> int:
        cursor = self.connection.execute(
            "INSERT INTO file_changes(session_id,path,previous_content,previous_bytes,previous_hash,new_hash,created,undone,operation,source_path,destination_previous_bytes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                change.session_id, change.path, change.previous_content, change.previous_bytes,
                change.previous_hash, change.new_hash, change.created, change.undone,
                change.operation, change.source_path, change.destination_previous_bytes,
            ),
        )
        self.connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an identifier for the file change")
        return cursor.lastrowid

    def last_change(self, session_id: str) -> FileChange | None:
        row = self.connection.execute("SELECT * FROM file_changes WHERE session_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        return FileChange.model_validate(dict(row)) if row else None

    def mark_undone(self, change_id: int) -> None:
        self.connection.execute("UPDATE file_changes SET undone=1 WHERE id=?", (change_id,))
        self.connection.commit()
