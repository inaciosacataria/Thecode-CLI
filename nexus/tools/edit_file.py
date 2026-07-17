from pydantic import BaseModel, Field

from nexus.permissions.risk import RiskLevel
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult


class EditFileInput(BaseModel):
    path: str
    old_text: str = Field(min_length=1)
    new_text: str


class EditFileTool(Tool[EditFileInput]):
    name = "edit_file"
    description = "Replace one exact, unique text block in a project file."
    input_schema = EditFileInput
    risk_level = RiskLevel.MEDIUM

    async def execute(self, arguments: EditFileInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        previous = path.read_text(encoding="utf-8")
        count = previous.count(arguments.old_text)
        if count != 1:
            return ToolResult(success=False, error=f"old_text must match exactly once; found {count}")
        updated = previous.replace(arguments.old_text, arguments.new_text, 1)
        path.write_text(updated, encoding="utf-8")
        return ToolResult(success=True, output=f"Updated {arguments.path}", metadata={"path": str(path), "previous": previous})

