import sqlite3
from pathlib import Path

import pytest

import nexus.sessions.database as session_database
from nexus.config.models import Settings
from nexus.sessions.database import SessionDatabase
from nexus.sessions.manager import SessionManager
from nexus.sessions.models import StoredMessage
from nexus.sessions.persistence import MAX_TOOL_OUTPUT_CHARS


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


def test_session_database_migrates_v1_without_losing_messages(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(session_database.SCHEMA)
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
        ("s1", str(tmp_path), "main", "openrouter", "free", "2024-01-01", "2024-01-01", "active", ""),
    )
    connection.execute(
        "INSERT INTO messages(session_id,role,content,metadata) VALUES (?,?,?,?)",
        ("s1", "user", "keep this", "{}"),
    )
    connection.execute(
        "INSERT INTO messages(session_id,role,content,metadata) VALUES (?,?,?,?)",
        ("s1", "assistant", "Thinking…", "{}"),
    )
    connection.commit()
    connection.close()

    db = SessionDatabase(path)

    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert [message.content for message in db.list_messages("s1")] == ["keep this"]


def test_persistence_redacts_secrets_skips_visual_state_and_limits_tools(tmp_path: Path) -> None:
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()
    db.add_message(StoredMessage(session_id=session.id, role="assistant", content="Thinking…"))
    db.add_message(
        StoredMessage(
            session_id=session.id,
            role="tool",
            content="OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnopqrstuvwxyz\n" + "x" * 50_000,
            metadata={"token": "ghp_abcdefghijklmnopqrstuvwxyz123456", "kind": "tool_call"},
        )
    )

    messages = db.list_messages(session.id)

    assert len(messages) == 1
    assert len(messages[0].content) <= MAX_TOOL_OUTPUT_CHARS + 50
    assert "abcdefghijklmnopqrstuvwxyz" not in messages[0].content
    assert "abcdefghijklmnopqrstuvwxyz123456" not in str(messages[0].metadata)


def test_session_summary_is_persisted_safely(tmp_path: Path) -> None:
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()
    db.update_session_summary(session.id, "Completed repository review")
    assert db.get_session(session.id).summary == "Completed repository review"  # type: ignore[union-attr]


def test_undo_restores_original_bytes(tmp_path: Path) -> None:
    path = tmp_path / "windows.txt"
    original = b"\xef\xbb\xbffirst\r\n"
    path.write_bytes(original)
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()
    path.write_bytes(b"changed\r\n")
    manager.record_change(session.id, path, "first\n", path.read_bytes(), original)
    assert "Undid" in manager.undo(session.id)
    assert path.read_bytes() == original


def test_undo_restores_rename_and_overwritten_destination(tmp_path: Path) -> None:
    source = tmp_path / "old.txt"
    destination = tmp_path / "new.txt"
    source_bytes = b"source\r\n"
    destination_bytes = b"destination\n"
    source.write_bytes(source_bytes)
    destination.write_bytes(destination_bytes)
    db = SessionDatabase(tmp_path / "sessions.db")
    manager = SessionManager(db, Settings(project_root=tmp_path))
    session = manager.create()
    destination.unlink()
    source.replace(destination)
    manager.record_change(
        session.id,
        destination,
        None,
        destination.read_bytes(),
        destination_bytes,
        "move",
        str(source),
        destination_bytes,
    )
    assert "Undid" in manager.undo(session.id)
    assert source.read_bytes() == source_bytes
    assert destination.read_bytes() == destination_bytes
