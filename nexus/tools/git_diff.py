from pydantic import BaseModel

from nexus.repository.git import run_git
from nexus.tools.base import Tool, ToolResult


class GitDiffInput(BaseModel):
    staged: bool = False


class GitDiffTool(Tool[GitDiffInput]):
    name = "git_diff"
    description = "Show unstaged or staged Git changes."
    input_schema = GitDiffInput

    async def execute(self, arguments: GitDiffInput) -> ToolResult:
        args = ["diff"] + (["--cached"] if arguments.staged else [])
        code, output, error = await run_git(self.project_root, *args)
        return ToolResult(success=code == 0, output=output, error=error or None)

