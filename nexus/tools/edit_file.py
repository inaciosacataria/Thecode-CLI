from pydantic import BaseModel, Field

from nexus.permissions.risk import RiskLevel
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult
from nexus.tools.text_files import read_text_document, write_text_document


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
        document = read_text_document(path)
        old_text = arguments.old_text.replace("\r\n", "\n").replace("\r", "\n")
        new_text = arguments.new_text.replace("\r\n", "\n").replace("\r", "\n")
        count = document.text.count(old_text)
        if count != 1:
            return ToolResult(success=False, error=f"old_text must match exactly once; found {count}")
        updated = document.text.replace(old_text, new_text, 1)
        write_text_document(path, updated, document)
        return ToolResult(
            success=True,
            output=f"Updated {arguments.path}",
            metadata={"path": str(path), "previous": document.text, "previous_bytes": document.raw},
        )
