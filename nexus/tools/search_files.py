from __future__ import annotations

import asyncio
import shutil

from pydantic import BaseModel, Field

from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult


class SearchFilesInput(BaseModel):
    query: str = Field(min_length=1)
    path: str = "."
    glob: str | None = None
    max_results: int = Field(default=200, ge=1, le=2000)


class SearchFilesTool(Tool[SearchFilesInput]):
    name = "search_files"
    description = "Search text in project files using ripgrep with a Python fallback."
    input_schema = SearchFilesInput

    async def execute(self, arguments: SearchFilesInput) -> ToolResult:
        base = resolve_project_path(self.project_root, arguments.path, must_exist=True)
        if shutil.which("rg"):
            command = ["rg", "--line-number", "--no-heading", "--color", "never"]
            if arguments.glob:
                command.extend(["--glob", arguments.glob])
            command.extend([arguments.query, str(base)])
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            lines = stdout.decode(errors="replace").splitlines()[: arguments.max_results]
            if process.returncode not in (0, 1):
                return ToolResult(success=False, error=stderr.decode(errors="replace"))
            return ToolResult(success=True, output="\n".join(lines), metadata={"engine": "rg"})
        results: list[str] = []
        pattern = arguments.glob or "*"
        for path in base.rglob(pattern):
            if not path.is_file() or ".git" in path.parts:
                continue
            try:
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if arguments.query in line:
                        results.append(f"{path.relative_to(self.project_root)}:{number}:{line}")
                        if len(results) >= arguments.max_results:
                            return ToolResult(success=True, output="\n".join(results), metadata={"engine": "python"})
            except (UnicodeDecodeError, OSError):
                continue
        return ToolResult(success=True, output="\n".join(results), metadata={"engine": "python"})

