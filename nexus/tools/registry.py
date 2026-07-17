from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus.tools.base import Tool
from nexus.tools.delete_file import DeleteFileTool
from nexus.tools.edit_file import EditFileTool
from nexus.tools.execute_command import ExecuteCommandTool
from nexus.tools.git_diff import GitDiffTool
from nexus.tools.git_history import GitBranchesTool, GitLogTool, GitShowTool
from nexus.tools.git_status import GitStatusTool
from nexus.tools.list_files import ListFilesTool
from nexus.tools.path_operations import (
    CopyFileTool,
    CreateDirectoryTool,
    DeleteDirectoryTool,
    FileInfoTool,
    MoveFileTool,
)
from nexus.tools.processes import (
    ListProcessesTool,
    ProcessManager,
    StartProcessTool,
    StopProcessTool,
)
from nexus.tools.project_map import ProjectMapTool
from nexus.tools.read_file import ReadFileTool
from nexus.tools.run_tests import RunTestsTool
from nexus.tools.search_files import SearchFilesTool
from nexus.tools.workspace_tools import WorkspaceListTool, WorkspaceSearchTool
from nexus.tools.write_file import WriteFileTool


class ToolRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        self.process_manager = ProcessManager(root) if root else None

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any]:
        try:
            return self._tools[name]
        except KeyError as error:
            raise KeyError(f"Unknown tool: {name}") from error

    def definitions(self) -> list[dict[str, object]]:
        return [tool.definition() for tool in self._tools.values()]

    @classmethod
    def defaults(
        cls,
        root: Path,
        *,
        read_only: bool = False,
        workspace_roots: dict[str, Path] | None = None,
    ) -> ToolRegistry:
        registry = cls(root)
        read_tools = (
            ReadFileTool,
            ListFilesTool,
            SearchFilesTool,
            GitStatusTool,
            GitDiffTool,
            ProjectMapTool,
            FileInfoTool,
            GitLogTool,
            GitShowTool,
            GitBranchesTool,
        )
        for tool_type in read_tools:
            registry.register(tool_type(root))
        if workspace_roots and len(workspace_roots) > 1:
            registry.register(WorkspaceSearchTool(root, workspace_roots))
            registry.register(WorkspaceListTool(root, workspace_roots))
        if not read_only:
            registry.register(WriteFileTool(root))
            registry.register(EditFileTool(root))
            registry.register(DeleteFileTool(root))
            registry.register(CreateDirectoryTool(root))
            registry.register(DeleteDirectoryTool(root))
            registry.register(CopyFileTool(root))
            registry.register(MoveFileTool(root))
            if registry.process_manager:
                registry.register(ExecuteCommandTool(root, registry.process_manager))
                registry.register(RunTestsTool(root, registry.process_manager))
                registry.register(StartProcessTool(root, registry.process_manager))
                registry.register(StopProcessTool(root, registry.process_manager))
                registry.register(ListProcessesTool(root, registry.process_manager))
        return registry
