from pathlib import Path


def should_ignore(path: Path, patterns: list[str]) -> bool:
    return any(part in patterns for part in path.parts)

