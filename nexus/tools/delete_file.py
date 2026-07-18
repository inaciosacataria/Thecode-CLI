from pydantic import BaseModel

from nexus.permissions.risk import RiskLevel
from nexus.security.paths import is_sensitive_path, resolve_project_path
from nexus.tools.base import Tool, ToolResult
from nexus.tools.text_files import read_text_document


class DeleteFileInput(BaseModel):
    path: str
    allow_sensitive: bool = False


class DeleteFileTool(Tool[DeleteFileInput]):
    name = "delete_file"
    description = (
        "Delete one project file. Use this instead of shell commands such as rm, del, or Python. "
        "Directories cannot be deleted."
    )
    input_schema = DeleteFileInput
    risk_level = RiskLevel.HIGH

    async def execute(self, arguments: DeleteFileInput) -> ToolResult:
        path = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        if not path.is_file():
            return ToolResult(success=False, error=f"Not a file: {arguments.path}")
        if is_sensitive_path(path) and not arguments.allow_sensitive:
            return ToolResult(
                success=False,
                error="Sensitive file deletion requires allow_sensitive=true and user approval",
            )
        document = read_text_document(path)
        path.unlink()
        return ToolResult(
            success=True,
            output=f"Deleted {path.relative_to(self.project_root)}",
            metadata={
                "path": str(path), "previous": document.text,
                "previous_bytes": document.raw, "deleted": True,
            },
        )
