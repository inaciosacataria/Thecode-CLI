from pydantic import BaseModel

from nexus.repository.scanner import render_project_summary
from nexus.tools.base import Tool, ToolResult


class ProjectMapInput(BaseModel):
    pass


class ProjectMapTool(Tool[ProjectMapInput]):
    name = "project_map"
    description = "Summarize languages, manifests, instructions and repository size."
    input_schema = ProjectMapInput

    async def execute(self, arguments: ProjectMapInput) -> ToolResult:
        return ToolResult(success=True, output=render_project_summary(self.project_root))

