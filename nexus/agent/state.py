from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class AgentStep(BaseModel):
    number: int
    user_request: str
    tool_name: str
    arguments: dict[str, Any]
    result: str = ""
    duration_ms: float = 0
    status: StepStatus = StepStatus.RUNNING
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentState(BaseModel):
    original_request: str
    steps: list[AgentStep] = Field(default_factory=list)
    cancelled: bool = False
