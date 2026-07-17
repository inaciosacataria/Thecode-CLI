from __future__ import annotations

import json
from pathlib import Path

INSTRUCTION_FILES = ("THECODE.md", "NEXUS.md", "AGENTS.md", "AGENT.md", "CLAUDE.md", ".cursorrules", "CONTRIBUTING.md", "README.md")
LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".java": "Java",
    ".go": "Go", ".rs": "Rust", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
}


def project_summary(root: Path, max_files: int = 2000) -> dict[str, object]:
    counts: dict[str, int] = {}
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in {".git", "node_modules", ".venv"} for part in path.parts):
            continue
        files.append(path)
        language = LANGUAGES.get(path.suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
        if len(files) >= max_files:
            break
    markers = [name for name in ("pyproject.toml", "package.json", "pom.xml", "go.mod", "Cargo.toml", "Dockerfile") if (root / name).exists()]
    instructions = [name for name in INSTRUCTION_FILES if (root / name).exists()]
    return {"name": root.name, "languages": counts, "markers": markers, "instructions": instructions, "files_scanned": len(files)}


def render_project_summary(root: Path) -> str:
    return json.dumps(project_summary(root), indent=2, ensure_ascii=False)
