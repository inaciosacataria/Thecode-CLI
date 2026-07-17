from pathlib import Path

import pytest

from nexus.tools.delete_file import DeleteFileInput, DeleteFileTool
from nexus.tools.edit_file import EditFileInput, EditFileTool
from nexus.tools.path_operations import (
    CopyFileTool,
    CreateDirectoryTool,
    DeleteDirectoryTool,
    MoveFileTool,
    PathInput,
    TransferPathInput,
)
from nexus.tools.read_file import ReadFileInput, ReadFileTool
from nexus.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_read_file_with_lines(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = await ReadFileTool(tmp_path).execute(ReadFileInput(path="a.txt", start_line=2, end_line=3))
    assert result.success
    assert result.output == "2: two\n3: three"


@pytest.mark.asyncio
async def test_edit_requires_unique_match(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("same same", encoding="utf-8")
    result = await EditFileTool(tmp_path).execute(EditFileInput(path="a.txt", old_text="same", new_text="new"))
    assert not result.success
    assert path.read_text(encoding="utf-8") == "same same"


def test_registry_definitions(tmp_path: Path) -> None:
    registry = ToolRegistry.defaults(tmp_path)
    names = {item["name"] for item in registry.definitions()}
    assert {
        "read_file", "edit_file", "delete_file", "create_directory", "copy_file", "move_file",
        "delete_directory", "file_info", "git_log", "git_show", "git_branches", "run_tests",
        "git_diff", "start_process", "stop_process", "list_processes",
    } <= names


def test_read_only_registry_hides_mutating_tools(tmp_path: Path) -> None:
    names = {item["name"] for item in ToolRegistry.defaults(tmp_path, read_only=True).definitions()}
    assert "read_file" in names
    assert "write_file" not in names
    assert "execute_command" not in names
    assert "delete_file" not in names
    assert "move_file" not in names
    assert "git_log" in names


@pytest.mark.asyncio
async def test_delete_file_removes_file_and_preserves_previous_content(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    path.write_text("<h1>Hello</h1>", encoding="utf-8")

    result = await DeleteFileTool(tmp_path).execute(DeleteFileInput(path="index.html"))

    assert result.success
    assert not path.exists()
    assert result.metadata["previous"] == "<h1>Hello</h1>"


@pytest.mark.asyncio
async def test_create_copy_move_and_delete_empty_directory(tmp_path: Path) -> None:
    created = await CreateDirectoryTool(tmp_path).execute(PathInput(path="assets/images"))
    assert created.success
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")

    copied = await CopyFileTool(tmp_path).execute(
        TransferPathInput(source="source.txt", destination="assets/copy.txt")
    )
    assert copied.success
    assert (tmp_path / "assets" / "copy.txt").read_text(encoding="utf-8") == "content"

    moved = await MoveFileTool(tmp_path).execute(
        TransferPathInput(source="assets/copy.txt", destination="assets/images/moved.txt")
    )
    assert moved.success
    assert not (tmp_path / "assets" / "copy.txt").exists()
    (tmp_path / "assets" / "images" / "moved.txt").unlink()

    deleted = await DeleteDirectoryTool(tmp_path).execute(PathInput(path="assets/images"))
    assert deleted.success


@pytest.mark.asyncio
async def test_delete_directory_refuses_non_empty_directory(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "keep.txt").write_text("keep", encoding="utf-8")

    result = await DeleteDirectoryTool(tmp_path).execute(PathInput(path="data"))

    assert not result.success
    assert directory.exists()
