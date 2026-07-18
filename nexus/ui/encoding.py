from __future__ import annotations

import locale
import os
import sys
from dataclasses import dataclass
from typing import IO


@dataclass(frozen=True)
class Symbols:
    check: str
    bullet: str
    gear: str
    bolt: str
    branch: str
    tree_mid: str
    tree_end: str
    tree_pipe: str
    line: str
    dot: str
    cross: str
    pending: str


UNICODE_SYMBOLS = Symbols("✓", "●", "⚙", "⚡", "⌁", "├", "└", "│", "─", "·", "✖", "□")
ASCII_SYMBOLS = Symbols("OK", "*", "@", "!", "git", "+", "`", "|", "-", ".", "X", "[ ]")


def configure_utf8_streams() -> None:
    """Use UTF-8 at the process boundary without altering stored text."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def supports_unicode(stream: IO[str] | None = None) -> bool:
    if os.getenv("THECODE_ASCII", "").lower() in {"1", "true", "yes"}:
        return False
    target = stream or sys.stdout
    encoding = getattr(target, "encoding", None) or locale.getpreferredencoding(False)
    try:
        "✓●⚙⚡├└│─".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def symbols(stream: IO[str] | None = None) -> Symbols:
    return UNICODE_SYMBOLS if supports_unicode(stream) else ASCII_SYMBOLS


def decode_subprocess_output(value: bytes) -> str:
    """Decode process output predictably, tolerating legacy Windows programs."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        if os.name == "nt":
            return value.decode(locale.getpreferredencoding(False), errors="replace")
        return value.decode("utf-8", errors="replace")
