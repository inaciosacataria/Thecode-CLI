from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from nexus.llm.base import LLMProvider, LLMResponse, Message


@dataclass(frozen=True)
class ProviderCandidate:
    provider: LLMProvider
    model: str
    name: str


class FailoverProvider(LLMProvider):
    """Retry transient failures and move to configured providers before any output is emitted."""

    def __init__(
        self,
        candidates: list[ProviderCandidate],
        *,
        attempts: int = 2,
        timeout: float = 120,
    ) -> None:
        if not candidates:
            raise ValueError("At least one provider candidate is required")
        self.candidates = candidates
        self.attempts = attempts
        self.timeout = timeout

    async def chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> LLMResponse:
        chunks = [chunk async for chunk in self.stream_chat(messages, tools, model)]
        content = "".join(chunk.content for chunk in chunks)
        calls = [call for chunk in chunks for call in chunk.tool_calls]
        finish = next((chunk.finish_reason for chunk in reversed(chunks) if chunk.finish_reason), None)
        return LLMResponse(content=content, tool_calls=calls, finish_reason=finish)

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> AsyncIterator[LLMResponse]:
        errors: list[str] = []
        for candidate in self.candidates:
            for attempt in range(self.attempts):
                emitted = False
                try:
                    async with asyncio.timeout(self.timeout):
                        async for chunk in candidate.provider.stream_chat(
                            messages, tools, candidate.model
                        ):
                            emitted = True
                            yield chunk
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if emitted or not _transient(error):
                        raise
                    errors.append(f"{candidate.name}/{candidate.model}: {error}")
                    if attempt + 1 < self.attempts:
                        await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError("All configured providers failed: " + " | ".join(errors[-5:]))


def _transient(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, httpx.TimeoutException, httpx.RequestError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 409, 429} or error.response.status_code >= 500
    message = str(error).casefold()
    return any(
        marker in message
        for marker in ("rate limit", "timeout", "temporarily unavailable", "context length", "model not found")
    )
