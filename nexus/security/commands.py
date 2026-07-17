from __future__ import annotations

import re
import shlex
import sys

from nexus.permissions.risk import RiskLevel

CRITICAL_PATTERNS = (
    r"(^|\s)(rm|rmdir)\s+(-[^ ]*r[^ ]*f|-[^ ]*f[^ ]*r)",
    r"git\s+reset\s+--hard",
    r"git\s+push\s+.*--force",
    r"\b(format|mkfs|diskpart)\b",
    r"\b(shutdown|reboot)\b",
)
SHELL_OPERATORS = re.compile(r"(?:&&|\|\||[|;<>`]|\$\()")


def split_command(command: str) -> list[str]:
    if SHELL_OPERATORS.search(command):
        raise ValueError("Shell operators and redirections are not supported")
    return shlex.split(command, posix=sys.platform != "win32")


def classify_command(command: str) -> RiskLevel:
    normalized = " ".join(command.lower().split())
    if any(re.search(pattern, normalized) for pattern in CRITICAL_PATTERNS):
        return RiskLevel.CRITICAL
    if normalized.startswith(("git status", "git diff", "git log", "rg ", "pytest", "npm test", "pnpm test")):
        return RiskLevel.LOW
    if normalized.startswith(("git push", "git commit", "pip install", "npm install")):
        return RiskLevel.HIGH
    return RiskLevel.MEDIUM

