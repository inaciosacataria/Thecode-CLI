from collections.abc import AsyncIterator

import httpx
import pytest

from nexus.llm.base import LLMProvider, LLMResponse, Message
from nexus.llm.failover import FailoverProvider, ProviderCandidate


class FailingProvider(LLMProvider):
    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        raise httpx.ReadTimeout("timeout")


class WorkingProvider(LLMProvider):
    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        return LLMResponse(content=f"ok:{model}")


class PartialFailureProvider(LLMProvider):
    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        raise AssertionError

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> AsyncIterator[LLMResponse]:
        yield LLMResponse(content="partial")
        raise httpx.ReadTimeout("after output")


@pytest.mark.asyncio
async def test_transient_failure_retries_then_falls_back() -> None:
    provider = FailoverProvider(
        [
            ProviderCandidate(FailingProvider(), "primary", "first"),
            ProviderCandidate(WorkingProvider(), "fallback", "second"),
        ],
        attempts=2,
        timeout=1,
    )
    response = await provider.chat([Message(role="user", content="hi")], [], "ignored")
    assert response.content == "ok:fallback"


@pytest.mark.asyncio
async def test_failover_never_replays_after_streaming_started() -> None:
    provider = FailoverProvider(
        [
            ProviderCandidate(PartialFailureProvider(), "primary", "first"),
            ProviderCandidate(WorkingProvider(), "fallback", "second"),
        ],
        timeout=1,
    )
    with pytest.raises(httpx.ReadTimeout):
        _ = [chunk async for chunk in provider.stream_chat([], [], "ignored")]
