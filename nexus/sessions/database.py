from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from nexus.sessions.models import FileChange, Session, StoredMessage

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
PRAGMA user_version = 1;
"""


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
            connection.executescript(SCHEMA)
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
            connection.executescript(SCHEMA)
            return connection

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
        self.connection.execute("INSERT INTO messages(session_id,role,content,metadata) VALUES (?,?,?,?)", (message.session_id, message.role, message.content, json.dumps(message.metadata)))
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
        cursor = self.connection.execute("INSERT INTO file_changes(session_id,path,previous_content,previous_hash,new_hash,created,undone) VALUES (?,?,?,?,?,?,?)", (change.session_id, change.path, change.previous_content, change.previous_hash, change.new_hash, change.created, change.undone))
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
