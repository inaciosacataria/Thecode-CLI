from pathlib import Path

from nexus.repository.architecture import analyze_architecture, render_architecture


def test_architecture_is_inferred_only_from_repository_files(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"main":"src/index.js","dependencies":{"express":"1","redis":"1"}}',
        encoding="utf-8",
    )
    (tmp_path / "src" / "routes").mkdir(parents=True)
    (tmp_path / "src" / "routes" / "fruit.js").write_text("", encoding="utf-8")
    (tmp_path / "src" / "index.js").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fruit.test.js").write_text("", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM node", encoding="utf-8")

    model = analyze_architecture(tmp_path)
    types = {node.type for node in model.nodes}
    rendered = render_architecture(model)

    assert {"framework", "entry-point", "routes", "tests", "docker", "integration"} <= types
    assert "Express" in rendered
    assert "Redis cache" in rendered
    assert "Kafka" not in rendered


def test_architecture_ignores_sensitive_and_generated_directories(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
    generated = tmp_path / "node_modules" / "controllers"
    generated.mkdir(parents=True)
    (generated / "fake.js").write_text("", encoding="utf-8")

    model = analyze_architecture(tmp_path)

    assert all(node.path != ".env" for node in model.nodes)
    assert all("node_modules" not in (node.path or "") for node in model.nodes)
