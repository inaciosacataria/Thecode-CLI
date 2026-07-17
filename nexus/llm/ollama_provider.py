from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nexus.llm.base import LLMProvider, LLMResponse, Message, ToolCall


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str = "http://localhost:11434", client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=120)

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], model: str) -> LLMResponse:
        response = await self.client.post(f"{self.base_url}/api/chat", json={"model": model, "messages": [m.model_dump(exclude_none=True) for m in messages], "tools": [{"type": "function", "function": t} for t in tools], "stream": False})
        response.raise_for_status()
        message = response.json()["message"]
        calls = [ToolCall(id=f"ollama-{i}", name=c["function"]["name"], arguments=c["function"].get("arguments", {})) for i, c in enumerate(message.get("tool_calls", []))]
        return LLMResponse(content=message.get("content", ""), tool_calls=calls)

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, Any]], model: str
    ) -> AsyncIterator[LLMResponse]:
        payload = {
            "model": model,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "tools": [{"type": "function", "function": tool} for tool in tools],
            "stream": True,
        }
        calls: list[ToolCall] = []
        async with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                message = data.get("message", {})
                content = message.get("content") or ""
                if content:
                    yield LLMResponse(content=content)
                for item in message.get("tool_calls", []):
                    function = item["function"]
                    calls.append(
                        ToolCall(
                            id=f"ollama-{len(calls)}",
                            name=function["name"],
                            arguments=function.get("arguments", {}),
                        )
                    )
        if calls:
            yield LLMResponse(tool_calls=calls)
