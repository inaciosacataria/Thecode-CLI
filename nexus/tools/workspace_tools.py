from pathlib import Path

from pydantic import BaseModel, Field

from nexus.tools.base import Tool, ToolResult
from nexus.tools.list_files import ListFilesInput, ListFilesTool
from nexus.tools.search_files import SearchFilesInput, SearchFilesTool


class WorkspaceSearchInput(BaseModel):
    query: str = Field(min_length=1)
    glob: str | None = None
    max_results_per_folder: int = Field(default=100, ge=1, le=1000)


class WorkspaceListInput(BaseModel):
    recursive: bool = False
    max_results_per_folder: int = Field(default=200, ge=1, le=2000)


class WorkspaceSearchTool(Tool[WorkspaceSearchInput]):
    name = "workspace_search"
    description = "Search text across every explicitly configured workspace folder."
    input_schema = WorkspaceSearchInput

    def __init__(self, project_root: Path, folders: dict[str, Path]) -> None:
        super().__init__(project_root)
        self.folders = folders

    async def execute(self, arguments: WorkspaceSearchInput) -> ToolResult:
        sections: list[str] = []
        for name, root in self.folders.items():
            result = await SearchFilesTool(root).execute(
                SearchFilesInput(
                    query=arguments.query,
                    glob=arguments.glob,
                    max_results=arguments.max_results_per_folder,
                )
            )
            if result.output:
                sections.append(f"[{name}]\n{result.output}")
        return ToolResult(success=True, output="\n\n".join(sections) or "No matches")


class WorkspaceListTool(Tool[WorkspaceListInput]):
    name = "workspace_list_files"
    description = "List files from every explicitly configured workspace folder."
    input_schema = WorkspaceListInput

    def __init__(self, project_root: Path, folders: dict[str, Path]) -> None:
        super().__init__(project_root)
        self.folders = folders

    async def execute(self, arguments: WorkspaceListInput) -> ToolResult:
        sections: list[str] = []
        for name, root in self.folders.items():
            result = await ListFilesTool(root).execute(
                ListFilesInput(
                    recursive=arguments.recursive,
                    max_results=arguments.max_results_per_folder,
                )
            )
            sections.append(f"[{name}]\n{result.output}")
        return ToolResult(success=True, output="\n\n".join(sections))
