from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from nexus.security.paths import is_sensitive_path
from nexus.security.secrets import redact_secrets
from nexus.ui.encoding import UNICODE_SYMBOLS, decode_subprocess_output, supports_unicode
from nexus.ui.state import ActivityStatus, ActivityType, UIState


def test_unicode_symbols_round_trip_without_mojibake() -> None:
    expected = "✓ ● ⚙ ⚡ ├ └ │ ─"
    assert decode_subprocess_output(expected.encode("utf-8")) == expected
    assert "â" not in decode_subprocess_output(expected.encode("utf-8"))
    assert UNICODE_SYMBOLS.check == "✓"


def test_unicode_capability_has_ascii_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THECODE_ASCII", "1")
    assert not supports_unicode(StringIO())


def test_activity_updates_one_item_and_enters_history_once() -> None:
    state = UIState(project="demo")
    state.upsert_activity(
        "tool-1", "npm test", ActivityStatus.RUNNING, 20,
        activity_type=ActivityType.RUN_TESTS,
    )
    state.upsert_activity("tool-1", "npm test", ActivityStatus.RUNNING, 75)
    item = state.upsert_activity("tool-1", "npm test", ActivityStatus.COMPLETED, 100)
    state.upsert_activity("tool-1", "npm test", ActivityStatus.COMPLETED, 100)

    assert len(state.activities) == 1
    assert item.progress == 100
    assert item.duration_ms is not None
    assert state.activity_history == [item]


def test_sensitive_paths_and_tool_text_are_protected() -> None:
    for name in (
        ".env", ".env.local", "server.pem", "id_ed25519", "credentials.json",
        "service-account.json", "secrets.yaml", "config.production.json",
    ):
        assert is_sensitive_path(Path(name))
    secret = "OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnopqrstuvwxyz"
    redacted = redact_secrets(secret)
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "REDACTED" in redacted


def test_provider_unicode_is_preserved() -> None:
    content = "Resposta: ✓ concluído · ação ⚡"
    assert decode_subprocess_output(content.encode("utf-8")) == content
