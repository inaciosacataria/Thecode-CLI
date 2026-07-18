from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from nexus.config.models import Settings
from nexus.security.paths import resolve_project_path
from nexus.sessions.database import SessionDatabase
from nexus.sessions.models import FileChange, Session


def content_hash(content: str | bytes) -> str:
    raw = content.encode() if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


class SessionManager:
    def __init__(self, database: SessionDatabase, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def create(self, branch: str = "") -> Session:
        session = Session(id=uuid.uuid4().hex[:12], project_dir=str(self.settings.project_root), branch=branch, provider=self.settings.llm.provider, model=self.settings.llm.model)
        self.database.save_session(session)
        return session

    def record_change(
        self,
        session_id: str,
        path: Path,
        previous: str | None,
        current: str | bytes,
        previous_bytes: bytes | None = None,
        operation: str = "edit",
        source_path: str | None = None,
        destination_previous_bytes: bytes | None = None,
    ) -> int:
        previous_hash_value = previous_bytes if previous_bytes is not None else previous
        return self.database.add_change(
            FileChange(
                session_id=session_id, path=str(path), previous_content=previous,
                previous_bytes=previous_bytes,
                previous_hash=content_hash(previous_hash_value) if previous_hash_value is not None else None,
                new_hash=content_hash(current),
                created=previous is None and previous_bytes is None,
                operation=operation, source_path=source_path,
                destination_previous_bytes=destination_previous_bytes,
            )
        )

    def undo(self, session_id: str) -> str:
        change = self.database.last_change(session_id)
        if not change or change.id is None:
            raise ValueError("No agent change is available to undo")
        path = resolve_project_path(self.settings.project_root, change.path)
        current = path.read_bytes() if path.exists() else b""
        if content_hash(current) != change.new_hash:
            raise ValueError("File changed after the agent operation; refusing to overwrite it")
        if change.operation == "move" and change.source_path:
            source = resolve_project_path(self.settings.project_root, change.source_path)
            if source.exists():
                raise ValueError("Move source exists again; refusing to overwrite it")
            source.parent.mkdir(parents=True, exist_ok=True)
            path.replace(source)
            if change.destination_previous_bytes is not None:
                path.write_bytes(change.destination_previous_bytes)
        elif change.created:
            path.unlink()
        else:
            if change.previous_bytes is not None:
                path.write_bytes(change.previous_bytes)
            else:
                path.write_text(change.previous_content or "", encoding="utf-8")
        self.database.mark_undone(change.id)
        return f"Undid change to {path.relative_to(self.settings.project_root)}"
