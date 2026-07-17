from pydantic import BaseModel, Field

from nexus.repository.git import run_git
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult


class GitLogInput(BaseModel):
    max_count: int = Field(default=20, ge=1, le=200)
    path: str | None = None


class GitLogTool(Tool[GitLogInput]):
    name = "git_log"
    description = "Show recent Git commits, optionally limited to one project path."
    input_schema = GitLogInput

    async def execute(self, arguments: GitLogInput) -> ToolResult:
        args = ["log", f"--max-count={arguments.max_count}", "--date=short", "--pretty=format:%h %ad %an %s"]
        if arguments.path:
            path = resolve_project_path(self.project_root, arguments.path)
            args.extend(["--", path.relative_to(self.project_root).as_posix()])
        code, output, error = await run_git(self.project_root, *args)
        return ToolResult(success=code == 0, output=output, error=error or None)


class GitShowInput(BaseModel):
    revision: str = Field(default="HEAD", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/~^{}-]*$")
    path: str | None = None
    max_characters: int = Field(default=50_000, ge=1000, le=500_000)


class GitShowTool(Tool[GitShowInput]):
    name = "git_show"
    description = "Show a Git revision or one file at a revision without changing the worktree."
    input_schema = GitShowInput

    async def execute(self, arguments: GitShowInput) -> ToolResult:
        target = f"{arguments.revision}:{arguments.path}" if arguments.path else arguments.revision
        code, output, error = await run_git(self.project_root, "show", "--no-ext-diff", target)
        if len(output) > arguments.max_characters:
            output = output[: arguments.max_characters] + "\n[truncated]"
        return ToolResult(success=code == 0, output=output, error=error or None)


class GitBranchesInput(BaseModel):
    include_remote: bool = False


class GitBranchesTool(Tool[GitBranchesInput]):
    name = "git_branches"
    description = "List local Git branches and optionally remote-tracking branches."
    input_schema = GitBranchesInput

    async def execute(self, arguments: GitBranchesInput) -> ToolResult:
        args = ["branch", "--all"] if arguments.include_remote else ["branch"]
        code, output, error = await run_git(self.project_root, *args)
        return ToolResult(success=code == 0, output=output, error=error or None)
