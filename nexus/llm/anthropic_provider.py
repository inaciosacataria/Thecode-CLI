from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nexus.llm.base import LLMProvider, LLMResponse, Message, ToolCall


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=120)

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], model: str) -> LLMResponse:
        system = "\n".join(m.content for m in messages if m.role == "system")
        converted = [m.model_dump(exclude_none=True) for m in messages if m.role != "system"]
        response = await self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json={"model": model, "system": system, "messages": converted, "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools], "max_tokens": 8192},
        )
        response.raise_for_status()
        data = response.json()
        text = "".join(block.get("text", "") for block in data["content"] if block["type"] == "text")
        calls = [ToolCall(id=block["id"], name=block["name"], arguments=block["input"]) for block in data["content"] if block["type"] == "tool_use"]
        return LLMResponse(content=text, tool_calls=calls, finish_reason=data.get("stop_reason"))

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, Any]], model: str
    ) -> AsyncIterator[LLMResponse]:
        system = "\n".join(message.content for message in messages if message.role == "system")
        converted = [
            message.model_dump(exclude_none=True) for message in messages if message.role != "system"
        ]
        payload = {
            "model": model,
            "system": system,
            "messages": converted,
            "tools": [
                {"name": tool["name"], "description": tool["description"], "input_schema": tool["parameters"]}
                for tool in tools
            ],
            "max_tokens": 8192,
            "stream": True,
        }
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        async with self.client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                data = json.loads(raw)
                event_type = data.get("type")
                if event_type == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        calls[int(data["index"])] = {
                            "id": block["id"],
                            "name": block["name"],
                            "arguments": "",
                        }
                elif event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield LLMResponse(content=delta.get("text", ""))
                    elif delta.get("type") == "input_json_delta":
                        calls[int(data["index"])]["arguments"] += delta.get("partial_json", "")
                elif event_type == "message_delta":
                    finish_reason = data.get("delta", {}).get("stop_reason")
        tool_calls = [
            ToolCall(
                id=value["id"],
                name=value["name"],
                arguments=json.loads(value["arguments"] or "{}"),
            )
            for _, value in sorted(calls.items())
        ]
        if tool_calls or finish_reason:
            yield LLMResponse(tool_calls=tool_calls, finish_reason=finish_reason)
