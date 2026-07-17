from pathlib import Path

from nexus.repository.scanner import project_summary


def build_index(root: Path) -> dict[str, object]:
    return project_summary(root)

