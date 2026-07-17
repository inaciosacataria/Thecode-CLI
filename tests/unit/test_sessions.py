from pathlib import Path
import sqlite3

import pytest

from nexus.config.models import Settings
import nexus.sessions.database as session_database
from nexus.sessions.database import SessionDatabase
from nexus.sessions.manager import SessionManager
from nexus.sessions.models import StoredMessage


def test_session_persistence(tmp_path: Path) -> None:
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create("main")
    assert db.get_session(session.id) == session


def test_session_messages_are_loaded_in_order(tmp_path: Path) -> None:
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()
    db.add_message(StoredMessage(session_id=session.id, role="user", content="delete it"))
    db.add_message(StoredMessage(session_id=session.id, role="assistant", content="confirm?"))

    messages = db.list_messages(session.id)

    assert [(message.role, message.content) for message in messages] == [
        ("user", "delete it"),
        ("assistant", "confirm?"),
    ]


def test_rollback_and_conflict_detection(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("before", encoding="utf-8")
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()
    path.write_text("after", encoding="utf-8")
    manager.record_change(session.id, path, "before", "after")
    assert "Undid" in manager.undo(session.id)
    assert path.read_text(encoding="utf-8") == "before"

    path.write_text("agent", encoding="utf-8")
    manager.record_change(session.id, path, "before", "agent")
    path.write_text("user", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        manager.undo(session.id)


def test_undo_restores_deleted_file(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    path.write_text("hello", encoding="utf-8")
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()

    path.unlink()
    manager.record_change(session.id, path, "hello", "")

    assert "Undid" in manager.undo(session.id)
    assert path.read_text(encoding="utf-8") == "hello"


def test_session_database_falls_back_when_default_path_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_home = tmp_path / "home"
    fallback_home = tmp_path / "temp"
    expected_primary = primary_home / ".nexus" / "sessions.db"
    expected_fallback = fallback_home / "thecode" / "sessions.db"
    real_connect = sqlite3.connect

    monkeypatch.setattr(session_database.Path, "home", lambda: primary_home)
    monkeypatch.setattr(session_database.tempfile, "gettempdir", lambda: str(fallback_home))

    def fake_connect(path: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if Path(str(path)) == expected_primary:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(session_database.sqlite3, "connect", fake_connect)

    db = SessionDatabase()

    assert db.path == expected_fallback
    assert db.connection.execute("SELECT 1").fetchone()[0] == 1
