from __future__ import annotations

import fnmatch
from pathlib import Path


class PathSecurityError(ValueError):
    pass


SENSITIVE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
    "secrets.yml",
    "secrets.yaml",
    "config.production.json",
)


def resolve_project_path(root: Path, value: str | Path, *, must_exist: bool = False) -> Path:
    project = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project / candidate
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(project):
        raise PathSecurityError(f"Path is outside the project: {value}")
    return resolved


def is_sensitive_path(path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in SENSITIVE_PATTERNS)
