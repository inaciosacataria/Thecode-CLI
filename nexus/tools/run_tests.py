from pathlib import Path

from pydantic import BaseModel, Field

from nexus.permissions.risk import RiskLevel
from nexus.tools.base import Tool, ToolResult
from nexus.tools.execute_command import ExecuteCommandInput, ExecuteCommandTool
from nexus.tools.processes import ProcessManager


class RunTestsInput(BaseModel):
    path: str = "."
    extra_args: list[str] = Field(default_factory=list)
    timeout: float = Field(default=300, gt=0, le=3600)


def detect_test_command(root: Path) -> list[str]:
    candidates = (
        ("pyproject.toml", ["pytest"]),
        ("pytest.ini", ["pytest"]),
        ("pnpm-lock.yaml", ["pnpm", "test"]),
        ("package.json", ["npm", "test"]),
        ("pom.xml", ["mvn", "test"]),
        ("gradlew.bat", ["gradlew.bat", "test"]),
        ("gradlew", ["./gradlew", "test"]),
        ("go.mod", ["go", "test", "./..."]),
        ("Cargo.toml", ["cargo", "test"]),
        ("pubspec.yaml", ["flutter", "test"]),
    )
    for marker, command in candidates:
        if (root / marker).exists():
            return command
    raise ValueError("Could not detect a supported test runner")


class RunTestsTool(Tool[RunTestsInput]):
    name = "run_tests"
    description = "Detect and run the project's test suite."
    input_schema = RunTestsInput
    risk_level = RiskLevel.LOW

    def __init__(self, project_root: Path, manager: ProcessManager | None = None) -> None:
        super().__init__(project_root)
        self.manager = manager

    async def execute(self, arguments: RunTestsInput) -> ToolResult:
        command = detect_test_command(self.project_root) + arguments.extra_args
        return await ExecuteCommandTool(self.project_root, self.manager).execute(
            ExecuteCommandInput(command=" ".join(command), cwd=arguments.path, timeout=arguments.timeout)
        )
