from __future__ import annotations

from pathlib import Path

PRIMARY_INSTRUCTIONS = (
    "THECODE.md",
    "NEXUS.md",
    "AGENTS.md",
    "AGENT.md",
    "CLAUDE.md",
    ".cursorrules",
    "CONTRIBUTING.md",
    "README.md",
    ".nexus/memory.md",
)


def instruction_files(root: Path) -> list[Path]:
    project = root.resolve()
    files = [project / name for name in PRIMARY_INSTRUCTIONS if (project / name).is_file()]
    files.extend(sorted(path for path in (project / ".cursor" / "rules").glob("*.mdc") if path.is_file()))
    for base in (project / ".agents" / "skills", project / ".codex" / "skills"):
        if base.is_dir():
            files.extend(sorted(base.glob("*/SKILL.md")))
    return files


def load_project_instructions(root: Path, max_characters: int = 60_000) -> str:
    sections: list[str] = []
    used = 0
    for path in instruction_files(root):
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        remaining = max_characters - used
        if remaining <= 0:
            break
        content = content[:remaining]
        relative = path.relative_to(root.resolve()).as_posix()
        sections.append(f"## Project instructions: {relative}\n\n{content}")
        used += len(content)
    if not sections:
        return ""
    return (
        "Follow the project instructions below. More specific safety and user instructions take "
        "priority. Cursor rules and local skills apply only within this project.\n\n"
        + "\n\n".join(sections)
    )
