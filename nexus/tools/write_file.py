from pydantic import BaseModel

from nexus.permissions.risk import RiskLevel
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult
from nexus.tools.text_files import read_text_document, write_text_document


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
        document = read_text_document(path) if existed else None
        previous = document.text if document else None
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_document(path, arguments.content, document)
        return ToolResult(
            success=True,
            output=f"{'Updated' if existed else 'Created'} {path.relative_to(self.project_root)}",
            metadata={
                "path": str(path), "previous": previous,
                "previous_bytes": document.raw if document else None,
                "created": not existed,
            },
        )
