from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from nexus.permissions.risk import RiskLevel
from nexus.security.commands import classify_command, split_command
from nexus.security.paths import resolve_project_path
from nexus.tools.base import Tool, ToolResult

ProcessOutputCallback = Callable[[str, str, str, float | None], None]


def detect_progress(text: str) -> float | None:
    percent = re.search(r"(?<!\d)(100|\d{1,2})(?:\.\d+)?\s*%", text)
    if percent:
        return float(percent.group(1))
    fraction = re.search(r"\b(\d+)\s*/\s*(\d+)\b", text)
    if fraction and int(fraction.group(2)) > 0:
        return min(100.0, int(fraction.group(1)) / int(fraction.group(2)) * 100)
    return None


@dataclass
class ManagedProcess:
    id: str
    command: str
    cwd: Path
    process: asyncio.subprocess.Process
    started_at: float = field(default_factory=time.monotonic)
    status: str = "running"
    returncode: int | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    output: list[tuple[str, str]] = field(default_factory=list)


class ProcessManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.processes: dict[str, ManagedProcess] = {}
        self.output_callback: ProcessOutputCallback | None = None

    async def start(self, command: str, cwd: str = ".") -> ManagedProcess:
        risk = classify_command(command)
        if risk is RiskLevel.CRITICAL:
            raise ValueError("Critical command is blocked")
        argv = split_command(command)
        if not argv:
            raise ValueError("Empty command")
        working_directory = resolve_project_path(self.root, cwd, must_exist=True)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=working_directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        process_id = uuid.uuid4().hex[:8]
        managed = ManagedProcess(process_id, command, working_directory, process)
        self.processes[process_id] = managed
        if process.stdout:
            managed.tasks.append(asyncio.create_task(self._read_stream(managed, "stdout", process.stdout)))
        if process.stderr:
            managed.tasks.append(asyncio.create_task(self._read_stream(managed, "stderr", process.stderr)))
        managed.tasks.append(asyncio.create_task(self._wait(managed)))
        return managed

    async def _read_stream(
        self, managed: ManagedProcess, stream_name: str, stream: asyncio.StreamReader
    ) -> None:
        while line := await stream.readline():
            text = line.decode(errors="replace").rstrip("\r\n")
            managed.output.append((stream_name, text))
            if self.output_callback:
                self.output_callback(managed.id, stream_name, text, detect_progress(text))

    async def _wait(self, managed: ManagedProcess) -> None:
        managed.returncode = await managed.process.wait()
        managed.status = "completed" if managed.returncode == 0 else "failed"
        if self.output_callback:
            elapsed = time.monotonic() - managed.started_at
            self.output_callback(
                managed.id,
                "status",
                f"Process {managed.status} with exit code {managed.returncode} in {elapsed:.1f}s",
                100.0 if managed.returncode == 0 else None,
            )

    async def stop(self, process_id: str) -> ManagedProcess:
        managed = self.processes.get(process_id)
        if managed is None:
            raise ValueError(f"Unknown process: {process_id}")
        if managed.process.returncode is None:
            managed.process.terminate()
            try:
                await asyncio.wait_for(managed.process.wait(), 5)
            except TimeoutError:
                managed.process.kill()
                await managed.process.wait()
        managed.status = "stopped"
        managed.returncode = managed.process.returncode
        return managed

    async def stop_all(self) -> None:
        for process_id, managed in list(self.processes.items()):
            if managed.process.returncode is None:
                await self.stop(process_id)


class StartProcessInput(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = "."


class StopProcessInput(BaseModel):
    process_id: str


class ListProcessesInput(BaseModel):
    include_completed: bool = True


class StartProcessTool(Tool[StartProcessInput]):
    name = "start_process"
    description = "Start a persistent project process and stream its output to the Live Terminal."
    input_schema = StartProcessInput
    risk_level = RiskLevel.MEDIUM

    def __init__(self, project_root: Path, manager: ProcessManager) -> None:
        super().__init__(project_root)
        self.manager = manager

    async def execute(self, arguments: StartProcessInput) -> ToolResult:
        managed = await self.manager.start(arguments.command, arguments.cwd)
        return ToolResult(
            success=True,
            output=f"Started process {managed.id} (PID {managed.process.pid}): {arguments.command}",
            metadata={"process_id": managed.id, "pid": managed.process.pid},
        )


class StopProcessTool(Tool[StopProcessInput]):
    name = "stop_process"
    description = "Stop a persistent process previously started by start_process."
    input_schema = StopProcessInput
    risk_level = RiskLevel.MEDIUM

    def __init__(self, project_root: Path, manager: ProcessManager) -> None:
        super().__init__(project_root)
        self.manager = manager

    async def execute(self, arguments: StopProcessInput) -> ToolResult:
        managed = await self.manager.stop(arguments.process_id)
        return ToolResult(success=True, output=f"Stopped process {managed.id}")


class ListProcessesTool(Tool[ListProcessesInput]):
    name = "list_processes"
    description = "List persistent processes with PID, state, command, and elapsed time."
    input_schema = ListProcessesInput

    def __init__(self, project_root: Path, manager: ProcessManager) -> None:
        super().__init__(project_root)
        self.manager = manager

    async def execute(self, arguments: ListProcessesInput) -> ToolResult:
        lines: list[str] = []
        for managed in self.manager.processes.values():
            if not arguments.include_completed and managed.status != "running":
                continue
            elapsed = time.monotonic() - managed.started_at
            lines.append(
                f"{managed.id} pid={managed.process.pid} {managed.status} {elapsed:.1f}s {managed.command}"
            )
        return ToolResult(success=True, output="\n".join(lines) or "No processes")
