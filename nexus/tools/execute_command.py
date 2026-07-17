from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from nexus.permissions.risk import RiskLevel
from nexus.tools.base import Tool, ToolResult
from nexus.tools.processes import ProcessManager


class ExecuteCommandInput(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = "."
    timeout: float = Field(default=120, gt=0, le=3600)
    max_output: int = Field(default=50_000, ge=1000, le=1_000_000)


class ExecuteCommandTool(Tool[ExecuteCommandInput]):
    name = "execute_command"
    description = "Execute a command without a shell inside the project."
    input_schema = ExecuteCommandInput
    risk_level = RiskLevel.MEDIUM

    def __init__(self, project_root: Path, manager: ProcessManager | None = None) -> None:
        super().__init__(project_root)
        self.manager = manager or ProcessManager(project_root)

    async def execute(self, arguments: ExecuteCommandInput) -> ToolResult:
        managed = await self.manager.start(arguments.command, arguments.cwd)
        try:
            await asyncio.wait_for(managed.process.wait(), arguments.timeout)
        except TimeoutError:
            await self.manager.stop(managed.id)
            return ToolResult(success=False, error=f"Command timed out after {arguments.timeout}s")
        await asyncio.gather(*managed.tasks, return_exceptions=True)
        rendered = "\n".join(
            f"[stderr] {text}" if stream == "stderr" else text
            for stream, text in managed.output
        )[-arguments.max_output :]
        return ToolResult(
            success=managed.returncode == 0,
            output=rendered,
            error=None if managed.returncode == 0 else f"Exit code {managed.returncode}",
            metadata={"returncode": managed.returncode, "process_id": managed.id},
        )
