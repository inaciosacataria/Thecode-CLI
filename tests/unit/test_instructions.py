from pathlib import Path

from nexus.repository.instructions import instruction_files, load_project_instructions


def test_loads_agent_cursor_rules_and_local_skills(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use pytest.", encoding="utf-8")
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "python.mdc").write_text("Prefer type hints.", encoding="utf-8")
    skill = tmp_path / ".agents" / "skills" / "release"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Release safely.", encoding="utf-8")

    content = load_project_instructions(tmp_path)
    files = instruction_files(tmp_path)

    assert "Use pytest." in content
    assert "Prefer type hints." in content
    assert "Release safely." in content
    assert len(files) == 3
