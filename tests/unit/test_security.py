from pathlib import Path

import pytest

from nexus.permissions.risk import RiskLevel
from nexus.security.commands import classify_command, split_command
from nexus.security.paths import PathSecurityError, is_sensitive_path, resolve_project_path
from nexus.security.secrets import looks_like_secret, redact_secrets


def test_path_inside_project(tmp_path: Path) -> None:
    assert resolve_project_path(tmp_path, "src/app.py") == tmp_path / "src" / "app.py"


def test_path_traversal_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        resolve_project_path(tmp_path, "../secret.txt")


def test_sensitive_paths() -> None:
    assert is_sensitive_path(Path(".env.local"))
    assert is_sensitive_path(Path("id_rsa"))
    assert not is_sensitive_path(Path("app.py"))


def test_destructive_command_is_critical() -> None:
    assert classify_command("git reset --hard") is RiskLevel.CRITICAL
    assert classify_command("rm -rf /") is RiskLevel.CRITICAL


def test_shell_operators_are_rejected() -> None:
    with pytest.raises(ValueError):
        split_command("pytest && git status")


def test_secret_redaction() -> None:
    output = redact_secrets("API_KEY=supersecretvalue token: abcdef")
    assert "supersecretvalue" not in output
    assert "abcdef" not in output


def test_secret_detection_rejects_api_key_as_model() -> None:
    assert looks_like_secret("sk-example-secret")
    assert not looks_like_secret("anthropic/claude-sonnet-4")
