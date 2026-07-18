from __future__ import annotations

import shutil

from pydantic import BaseModel

from nexus.permissions.risk import RiskLevel
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult


class PathInput(BaseModel):
    path: str


class TransferPathInput(BaseModel):
    source: str
    destination: str
    overwrite: bool = False


class CreateDirectoryTool(Tool[PathInput]):
    name = "create_directory"
    description = "Create a project directory, including missing parent directories."
    input_schema = PathInput
    risk_level = RiskLevel.LOW

    async def execute(self, arguments: PathInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path)
        if path.exists() and not path.is_dir():
            return ToolResult(success=False, error=f"A file already exists at {arguments.path}")
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        return ToolResult(
            success=True,
            output=f"{'Already exists' if existed else 'Created'} {path.relative_to(self.project_root)}/",
            metadata={"path": str(path), "created": not existed},
        )


class DeleteDirectoryTool(Tool[PathInput]):
    name = "delete_directory"
    description = (
        "Delete one empty project directory. It never deletes a directory recursively; remove "
        "its files explicitly first."
    )
    input_schema = PathInput
    risk_level = RiskLevel.HIGH

    async def execute(self, arguments: PathInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        if path == self.project_root:
            return ToolResult(success=False, error="The project root cannot be deleted")
        if not path.is_dir():
            return ToolResult(success=False, error=f"Not a directory: {arguments.path}")
        try:
            path.rmdir()
        except OSError:
            return ToolResult(success=False, error="Directory is not empty")
        return ToolResult(success=True, output=f"Deleted {path.relative_to(self.project_root)}/")


class CopyFileTool(Tool[TransferPathInput]):
    name = "copy_file"
    description = "Copy one project file to another project path."
    input_schema = TransferPathInput
    risk_level = RiskLevel.MEDIUM

    async def execute(self, arguments: TransferPathInput) -> ToolResult:
        source = resolve_project_path(self.project_root, arguments.source, must_exist=True)
        destination = resolve_project_path(self.project_root, arguments.destination)
        if not source.is_file():
            return ToolResult(success=False, error=f"Not a file: {arguments.source}")
        if destination.is_dir():
            return ToolResult(success=False, error=f"Destination is a directory: {arguments.destination}")
        if destination.exists() and not arguments.overwrite:
            return ToolResult(success=False, error=f"Destination exists: {arguments.destination}")
        previous = destination.read_bytes() if destination.is_file() else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return ToolResult(
            success=True,
            output=f"Copied {arguments.source} to {arguments.destination}",
            metadata={
                "path": str(destination), "previous_bytes": previous, "operation": "copy"
            },
        )


class MoveFileTool(Tool[TransferPathInput]):
    name = "move_file"
    description = "Move or rename one project file to another project path."
    input_schema = TransferPathInput
    risk_level = RiskLevel.MEDIUM

    async def execute(self, arguments: TransferPathInput) -> ToolResult:
        source = resolve_project_path(self.project_root, arguments.source, must_exist=True)
        destination = resolve_project_path(self.project_root, arguments.destination)
        if not source.is_file():
            return ToolResult(success=False, error=f"Not a file: {arguments.source}")
        if destination.is_dir():
            return ToolResult(success=False, error=f"Destination is a directory: {arguments.destination}")
        if destination.exists() and not arguments.overwrite:
            return ToolResult(success=False, error=f"Destination exists: {arguments.destination}")
        previous = destination.read_bytes() if destination.is_file() else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        source.replace(destination)
        return ToolResult(
            success=True,
            output=f"Moved {arguments.source} to {arguments.destination}",
            metadata={
                "source": str(source), "path": str(destination),
                "previous_bytes": previous, "operation": "move",
            },
        )


class FileInfoTool(Tool[PathInput]):
    name = "file_info"
    description = "Show type, size, and resolved project-relative path for a file or directory."
    input_schema = PathInput

    async def execute(self, arguments: PathInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        kind = "directory" if path.is_dir() else "file" if path.is_file() else "other"
        size = path.stat().st_size
        return ToolResult(
            success=True,
            output=f"path: {path.relative_to(self.project_root).as_posix()}\ntype: {kind}\nsize: {size}",
        )
