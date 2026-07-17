from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[Message], tools: list[dict[str, Any]], model: str
    ) -> LLMResponse: ...

    async def models(self) -> list[str]:
        return []

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, Any]], model: str
    ) -> AsyncIterator[LLMResponse]:
        """Yield response chunks; providers without streaming fall back to one complete chunk."""
        yield await self.chat(messages, tools, model)
