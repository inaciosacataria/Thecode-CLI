from __future__ import annotations

from typing import Any

from nexus.security.secrets import redact_secrets

MAX_CONVERSATION_CHARS = 100_000
MAX_TOOL_OUTPUT_CHARS = 20_000
MAX_METADATA_TEXT_CHARS = 4_000
TRANSIENT_MESSAGES = {
    "thinking...", "thinking…", "ready", "loaded project", "response completed",
}


def sanitize_message(role: str, content: str) -> str | None:
    normalized = content.strip().casefold()
    if role not in {"user", "assistant", "tool"} or normalized in TRANSIENT_MESSAGES:
        return None
    limit = MAX_TOOL_OUTPUT_CHARS if role == "tool" else MAX_CONVERSATION_CHARS
    safe = redact_secrets(content)
    if len(safe) > limit:
        safe = safe[:limit] + "\n[content truncated before persistence]"
    return safe


def sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_metadata(item) for key, item in list(value.items())[:50]}
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value[:100]]
    if isinstance(value, str):
        safe = redact_secrets(value)
        return safe[:MAX_METADATA_TEXT_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_secrets(str(value))[:MAX_METADATA_TEXT_CHARS]
