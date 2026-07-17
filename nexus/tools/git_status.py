from pydantic import BaseModel

from nexus.repository.git import run_git
from nexus.tools.base import Tool, ToolResult


class GitStatusInput(BaseModel):
    pass


class GitStatusTool(Tool[GitStatusInput]):
    name = "git_status"
    description = "Show the current Git branch and working tree status."
    input_schema = GitStatusInput

    async def execute(self, arguments: GitStatusInput) -> ToolResult:
        code, output, error = await run_git(self.project_root, "status", "--short", "--branch")
        return ToolResult(success=code == 0, output=output, error=error or None)

