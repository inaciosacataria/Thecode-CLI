from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, Field

from nexus.permissions.risk import RiskLevel

InputT = TypeVar("InputT", bound=BaseModel)


class ToolResult(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Tool(ABC, Generic[InputT]):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[type[BaseModel]]
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema.model_json_schema(),
        }

    @abstractmethod
    async def execute(self, arguments: InputT) -> ToolResult: ...

