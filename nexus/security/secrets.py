import re

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 2:
            redacted = pattern.sub(r"\1=[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def looks_like_secret(value: str) -> bool:
    normalized = value.strip()
    prefixes = ("sk-", "sk_", "api_", "key-")
    return normalized.lower().startswith(prefixes) or "private key" in normalized.lower()
