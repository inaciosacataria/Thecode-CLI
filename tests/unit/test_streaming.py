import json

import httpx
import pytest

from nexus.llm.base import Message
from nexus.llm.ollama_provider import OllamaProvider
from nexus.llm.openai_provider import OpenAIProvider


@pytest.mark.asyncio
async def test_openai_stream_assembles_text_and_tool_call() -> None:
    events = [
        {"choices": [], "usage": {"prompt_tokens": 10}},
        {"choices": [{"delta": {"content": "Working"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "read_file", "arguments": '{"path":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"README.md"}'}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body)

    provider = OpenAIProvider("test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    chunks = [chunk async for chunk in provider.stream_chat([Message(role="user", content="hi")], [], "mock")]

    assert chunks[0].content == "Working"
    assert chunks[-1].tool_calls[0].arguments == {"path": "README.md"}


@pytest.mark.asyncio
async def test_ollama_stream_yields_incremental_text() -> None:
    body = "\n".join(
        [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": True}),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body)

    provider = OllamaProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    chunks = [chunk async for chunk in provider.stream_chat([Message(role="user", content="hi")], [], "mock")]

    assert [chunk.content for chunk in chunks] == ["Hello", " world"]
