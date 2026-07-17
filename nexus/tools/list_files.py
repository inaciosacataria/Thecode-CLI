from pydantic import BaseModel, Field

from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult


class ListFilesInput(BaseModel):
    path: str = "."
    recursive: bool = False
    max_results: int = Field(default=500, ge=1, le=5000)


class ListFilesTool(Tool[ListFilesInput]):
    name = "list_files"
    description = "List project files and directories."
    input_schema = ListFilesInput

    async def execute(self, arguments: ListFilesInput) -> ToolResult:
        base = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        iterator = base.rglob("*") if arguments.recursive else base.iterdir()
        items: list[str] = []
        for item in iterator:
            if ".git" in item.parts:
                continue
            suffix = "/" if item.is_dir() else ""
            items.append(f"{item.relative_to(self.project_root).as_posix()}{suffix}")
            if len(items) >= arguments.max_results:
                break
        return ToolResult(success=True, output="\n".join(sorted(items)), metadata={"count": len(items)})
