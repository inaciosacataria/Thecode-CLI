from pydantic import BaseModel

from nexus.permissions.risk import RiskLevel
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult


class WriteFileInput(BaseModel):
    path: str
    content: str


class WriteFileTool(Tool[WriteFileInput]):
    name = "write_file"
    description = "Create or replace a UTF-8 project file."
    input_schema = WriteFileInput
    risk_level = RiskLevel.MEDIUM

    async def execute(self, arguments: WriteFileInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path)
        existed = path.exists()
        previous = path.read_text(encoding="utf-8") if existed else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
        return ToolResult(
            success=True,
            output=f"{'Updated' if existed else 'Created'} {path.relative_to(self.project_root)}",
            metadata={"path": str(path), "previous": previous, "created": not existed},
        )
