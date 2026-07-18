from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from nexus.security.paths import is_sensitive_path


@dataclass(frozen=True)
class ArchitectureNode:
    id: str
    label: str
    type: str
    path: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchitectureEdge:
    source: str
    target: str
    relation: str


@dataclass
class ArchitectureModel:
    nodes: list[ArchitectureNode] = field(default_factory=list)
    edges: list[ArchitectureEdge] = field(default_factory=list)


DIRECTORY_TYPES = {
    "routes": "routes", "controllers": "controllers", "controller": "controllers",
    "services": "services", "service": "services", "repositories": "repositories",
    "repository": "repositories", "tests": "tests", "test": "tests", "__tests__": "tests",
    "agent": "module", "tools": "module", "llm": "module", "ui": "module",
    "sessions": "module", "security": "module", "config": "module",
}


def analyze_architecture(root: Path, max_files: int = 2000) -> ArchitectureModel:
    """Infer a conservative architecture model from files that actually exist."""
    root = root.resolve()
    model = ArchitectureModel([ArchitectureNode("project", root.name, "project", ".")])
    seen: set[tuple[str, str]] = set()

    def add(node_type: str, label: str, path: Path) -> str:
        key = (node_type, str(path))
        node_id = f"{node_type}:{path.as_posix()}"
        if key in seen:
            return node_id
        seen.add(key)
        model.nodes.append(ArchitectureNode(node_id, label, node_type, path.relative_to(root).as_posix()))
        model.edges.append(ArchitectureEdge("project", node_id, "contains"))
        return node_id

    package = _read_json(root / "package.json")
    raw_dependencies = package.get("dependencies")
    dependencies = " ".join(raw_dependencies.keys()).casefold() if isinstance(raw_dependencies, dict) else ""
    framework = _detect_framework(root, dependencies)
    if framework:
        model.nodes.append(ArchitectureNode("framework", framework, "framework"))
        model.edges.append(ArchitectureEdge("project", "framework", "uses"))

    entry_points = _entry_points(package)
    files = 0
    for path in root.rglob("*"):
        if files >= max_files:
            break
        if not path.is_file() or is_sensitive_path(path) or _ignored(path, root):
            continue
        files += 1
        relative = path.relative_to(root)
        for index, part in enumerate(relative.parts[:-1]):
            node_type = DIRECTORY_TYPES.get(part.casefold())
            if node_type:
                directory = root.joinpath(*relative.parts[: index + 1])
                add(node_type, directory.name, directory)
                break
        if relative.as_posix() in entry_points:
            add("entry-point", relative.name, path)

    for candidate in (root / "Dockerfile", root / "docker-compose.yml", root / "compose.yaml"):
        if candidate.exists():
            add("docker", candidate.name, candidate)
    for candidate in (root / ".github" / "workflows", root / ".gitlab-ci.yml", root / "Jenkinsfile"):
        if candidate.exists():
            add("ci-cd", candidate.name, candidate)
    for name in ("prisma", "migrations", "schema.sql", "alembic.ini"):
        candidate = root / name
        if candidate.exists():
            add("database", candidate.name, candidate)
    for name in _detected_infrastructure(dependencies):
        model.nodes.append(ArchitectureNode(f"integration:{name}", name, "integration"))
        model.edges.append(ArchitectureEdge("project", f"integration:{name}", "integrates"))
    return model


def render_architecture(model: ArchitectureModel) -> str:
    if len(model.nodes) == 1:
        return "No architecture components detected yet."
    lines = ["Architecture detected from repository files", ""]
    for node in model.nodes:
        if node.id != "project":
            location = f"  [{node.path}]" if node.path else ""
            lines.append(f"{node.type.upper():<14} {node.label}{location}")
    labels = {node.id: node.label for node in model.nodes}
    if model.edges:
        lines.extend(("", "Relationships"))
        for edge in model.edges:
            lines.append(f"{labels.get(edge.source, edge.source)} → {labels.get(edge.target, edge.target)}  ({edge.relation})")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _detect_framework(root: Path, dependencies: str) -> str | None:
    candidates = {
        "next": "Next.js", "express": "Express", "fastify": "Fastify", "nestjs": "NestJS",
        "react": "React", "vue": "Vue", "django": "Django", "fastapi": "FastAPI",
        "flask": "Flask", "spring": "Spring", "quarkus": "Quarkus",
        "textual": "Textual", "typer": "Typer CLI",
    }
    project_text = dependencies
    for marker in (root / "pyproject.toml", root / "requirements.txt", root / "pom.xml"):
        try:
            project_text += " " + marker.read_text(encoding="utf-8")[:100_000].casefold()
        except (OSError, UnicodeDecodeError):
            pass
    return next((label for token, label in candidates.items() if token in project_text), None)


def _entry_points(package: dict[str, object]) -> set[str]:
    values = {
        "main.py", "app.py", "manage.py", "src/main.py", "src/index.ts", "src/index.js",
        "nexus/__main__.py", "nexus/cli.py",
    }
    main = package.get("main")
    if isinstance(main, str):
        values.add(Path(main).as_posix())
    return values


def _detected_infrastructure(dependencies: str) -> list[str]:
    candidates = {
        "postgres": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB",
        "redis": "Redis cache", "rabbitmq": "RabbitMQ", "kafka": "Kafka",
        "stripe": "Stripe API", "openai": "OpenAI API", "anthropic": "Anthropic API",
    }
    return [label for token, label in candidates.items() if token in dependencies]


def _ignored(path: Path, root: Path) -> bool:
    return any(
        part in {".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".cache"}
        for part in path.relative_to(root).parts
    )
