from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nexus.agent.loop import AgentLoop
from nexus.agent.state import AgentStep, StepStatus
from nexus.llm.base import LLMProvider, LLMResponse, Message, ToolCall
from nexus.permissions.manager import PermissionManager
from nexus.tools.registry import ToolRegistry


class MockProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall(id="1", name="list_files", arguments={})])
        return LLMResponse(content="Done")


class EndlessProvider(LLMProvider):
    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        return LLMResponse(tool_calls=[ToolCall(id="1", name="list_files", arguments={})])


class RepeatedFailureProvider(LLMProvider):
    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        return LLMResponse(
            tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "missing.txt"})]
        )


class StreamingProvider(LLMProvider):
    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        raise AssertionError("stream_chat should be used")

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> AsyncIterator[LLMResponse]:
        yield LLMResponse(content="Hello")
        yield LLMResponse(content=" world", finish_reason="stop")


class ContextProvider(LLMProvider):
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    async def chat(self, messages: list[Message], tools: list[dict[str, object]], model: str) -> LLMResponse:
        self.requests.append([message.model_copy() for message in messages])
        return LLMResponse(content=f"reply {len(self.requests)}")


class CorrectionProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "new"})]
            )
        if self.calls == 2:
            assert "Inspect the relevant project files" in messages[-1].content
            return LLMResponse(
                tool_calls=[ToolCall(id="r1", name="read_file", arguments={"path": "app.py"})]
            )
        if self.calls == 3:
            return LLMResponse(
                tool_calls=[ToolCall(id="w2", name="write_file", arguments={"path": "app.py", "content": "new"})]
            )
        return LLMResponse(content="Updated app.py after inspection.")


@pytest.mark.asyncio
async def test_agent_executes_tools(tmp_path: Path) -> None:
    loop = AgentLoop(MockProvider(), ToolRegistry.defaults(tmp_path), PermissionManager("safe"), "mock")
    answer, state = await loop.run("inspect")
    assert answer == "Done"
    assert len(state.steps) == 1


@pytest.mark.asyncio
async def test_agent_honors_step_limit(tmp_path: Path) -> None:
    loop = AgentLoop(EndlessProvider(), ToolRegistry.defaults(tmp_path), PermissionManager("safe"), "mock", max_steps=2)
    answer, state = await loop.run("inspect")
    assert "maximum of 2" in answer
    assert len(state.steps) == 2


@pytest.mark.asyncio
async def test_agent_stops_repeated_identical_failed_action(tmp_path: Path) -> None:
    loop = AgentLoop(
        RepeatedFailureProvider(),
        ToolRegistry.defaults(tmp_path),
        PermissionManager("safe"),
        "mock",
    )

    answer, state = await loop.run("read missing file")

    assert "repeated the same failed action" in answer
    assert len(state.steps) == 2


@pytest.mark.asyncio
async def test_agent_streams_tokens(tmp_path: Path) -> None:
    tokens: list[str] = []
    events: list[str] = []
    loop = AgentLoop(
        StreamingProvider(),
        ToolRegistry.defaults(tmp_path),
        PermissionManager("safe"),
        "mock",
        on_stream_start=lambda: events.append("start"),
        on_token=tokens.append,
        on_stream_end=lambda: events.append("end"),
    )

    answer, _ = await loop.run("hello")

    assert answer == "Hello world"
    assert tokens == ["Hello", " world"]
    assert events == ["start", "end"]


@pytest.mark.asyncio
async def test_agent_reports_running_and_completed_tool_states(tmp_path: Path) -> None:
    states: list[StepStatus] = []

    def capture(step: AgentStep) -> None:
        states.append(step.status)

    loop = AgentLoop(
        MockProvider(), ToolRegistry.defaults(tmp_path), PermissionManager("safe"), "mock", on_step=capture
    )
    await loop.run("inspect")

    assert states == [StepStatus.RUNNING, StepStatus.SUCCEEDED]


@pytest.mark.asyncio
async def test_agent_preserves_conversation_context_between_runs(tmp_path: Path) -> None:
    provider = ContextProvider()
    loop = AgentLoop(
        provider, ToolRegistry.defaults(tmp_path), PermissionManager("safe"), "mock"
    )

    await loop.run("delete the file")
    await loop.run("yes")

    second = provider.requests[1]
    assert [(message.role, message.content) for message in second[-3:]] == [
        ("user", "delete the file"),
        ("assistant", "reply 1"),
        ("user", "yes"),
    ]


def test_write_permission_description_contains_unified_diff(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    loop = AgentLoop(
        ContextProvider(), ToolRegistry.defaults(tmp_path), PermissionManager("safe"), "mock"
    )

    description = loop._action_description(
        "write_file", {"path": "app.py", "content": "new\n"}
    )

    assert "---DIFF---" in description
    assert "-old" in description
    assert "+new" in description


@pytest.mark.asyncio
async def test_agent_recovers_after_write_before_inspection(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old", encoding="utf-8")
    loop = AgentLoop(
        CorrectionProvider(),
        ToolRegistry.defaults(tmp_path),
        PermissionManager("agent"),
        "mock",
    )
    answer, state = await loop.run("update app")
    assert answer == "Updated app.py after inspection."
    assert [step.status for step in state.steps] == [
        StepStatus.FAILED,
        StepStatus.SUCCEEDED,
        StepStatus.SUCCEEDED,
    ]
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "new"
