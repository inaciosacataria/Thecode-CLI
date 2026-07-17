import json
from pathlib import Path

import pytest

from nexus.tools.registry import ToolRegistry
from nexus.tools.workspace_tools import WorkspaceSearchInput, WorkspaceSearchTool
from nexus.workspace import CodeWorkspace, discover_workspace


def test_loads_vscode_workspace_with_comments_and_trailing_commas(tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend").mkdir()
    path = tmp_path / "platform.code-workspace"
    path.write_text(
        """{
        // VS Code style comment
        "folders": [
          {"path": "frontend", "name": "web"},
          {"path": "backend"},
        ],
        "settings": {},
        }""",
        encoding="utf-8",
    )

    workspace = CodeWorkspace.load(path)

    assert [folder.name for folder in workspace.folders] == ["web", "backend"]
    assert workspace.folder("WEB").path == tmp_path / "frontend"


def test_add_folder_preserves_workspace_settings(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    path = tmp_path / "project.code-workspace"
    path.write_text(
        json.dumps({"folders": [{"path": "first"}], "settings": {"editor.tabSize": 2}}),
        encoding="utf-8",
    )

    workspace = CodeWorkspace.load(path)
    workspace.add_folder(second, "api")
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["settings"] == {"editor.tabSize": 2}
    assert saved["folders"][-1] == {"path": "second", "name": "api"}


def test_discovers_only_unambiguous_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "one.code-workspace"
    workspace.write_text("{}", encoding="utf-8")
    assert discover_workspace(tmp_path) == workspace
    (tmp_path / "two.code-workspace").write_text("{}", encoding="utf-8")
    assert discover_workspace(tmp_path) is None


@pytest.mark.asyncio
async def test_workspace_search_is_limited_to_configured_folders(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
    (frontend / "app.ts").write_text("sharedSymbol", encoding="utf-8")
    (backend / "api.py").write_text("sharedSymbol", encoding="utf-8")
    roots = {"web": frontend, "api": backend}

    result = await WorkspaceSearchTool(frontend, roots).execute(
        WorkspaceSearchInput(query="sharedSymbol")
    )
    names = {item["name"] for item in ToolRegistry.defaults(frontend, workspace_roots=roots).definitions()}

    assert result.success
    assert "[web]" in result.output and "[api]" in result.output
    assert {"workspace_search", "workspace_list_files"} <= names
