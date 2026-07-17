from pydantic import BaseModel, Field

from nexus.security.paths import is_sensitive_path, resolve_project_path
from nexus.tools.base import Tool, ToolResult


class ReadFileInput(BaseModel):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    allow_sensitive: bool = False


class ReadFileTool(Tool[ReadFileInput]):
    name = "read_file"
    description = "Read a UTF-8 project file, optionally within a line range."
    input_schema = ReadFileInput

    async def execute(self, arguments: ReadFileInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        if is_sensitive_path(path) and not arguments.allow_sensitive:
            return ToolResult(success=False, error="Sensitive file requires explicit authorization")
        if not path.is_file():
            return ToolResult(success=False, error="Path is not a file")
        lines = path.read_text(encoding="utf-8").splitlines()
        end = arguments.end_line or len(lines)
        if end < arguments.start_line:
            return ToolResult(success=False, error="end_line must be >= start_line")
        selected = lines[arguments.start_line - 1 : end]
        numbered = "\n".join(
            f"{number}: {line}" for number, line in enumerate(selected, arguments.start_line)
        )
        return ToolResult(success=True, output=numbered, metadata={"path": str(path), "lines": len(selected)})
