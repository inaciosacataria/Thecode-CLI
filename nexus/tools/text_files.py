from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TextDocument:
    text: str
    encoding: str
    newline: str
    raw: bytes


def read_text_document(path: Path) -> TextDocument:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    elif raw.startswith(b"\xff\xfe"):
        encoding = "utf-16-le"
    elif raw.startswith(b"\xfe\xff"):
        encoding = "utf-16-be"
    else:
        encoding = "utf-8"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as error:
        raise ValueError(f"Unsupported text encoding for {path.name}") from error
    newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextDocument(normalized, encoding, newline, raw)


def write_text_document(path: Path, text: str, template: TextDocument | None = None) -> bytes:
    encoding = template.encoding if template else "utf-8"
    newline = template.newline if template else "\n"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    rendered = normalized.replace("\n", newline)
    raw = rendered.encode(encoding)
    path.write_bytes(raw)
    return raw
