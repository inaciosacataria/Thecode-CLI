from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nexus.llm.base import LLMProvider, LLMResponse, Message, ToolCall


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=120)

    def _authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], model: str) -> LLMResponse:
        payload: dict[str, Any] = {"model": model, "messages": [m.model_dump(exclude_none=True) for m in messages]}
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self._authorization_headers(),
            json=payload,
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        message = choice["message"]
        calls = [ToolCall(id=item["id"], name=item["function"]["name"], arguments=json.loads(item["function"]["arguments"])) for item in message.get("tool_calls", [])]
        return LLMResponse(content=message.get("content") or "", tool_calls=calls, finish_reason=choice.get("finish_reason"))

    async def stream_chat(
        self, messages: list[Message], tools: list[dict[str, Any]], model: str
    ) -> AsyncIterator[LLMResponse]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        received = False
        async with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._authorization_headers(),
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                data = json.loads(raw)
                if data.get("error"):
                    error = data["error"]
                    message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                    raise RuntimeError(f"Provider stream error: {message}")
                choices = data.get("choices") or []
                if not choices:
                    # OpenRouter may send usage/accounting events without choices.
                    continue
                choice = choices[0]
                received = True
                delta = choice.get("delta", {})
                content = delta.get("content") or ""
                if content:
                    yield LLMResponse(content=content)
                for item in delta.get("tool_calls", []):
                    index = int(item.get("index", 0))
                    current = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    current["id"] += item.get("id") or ""
                    function = item.get("function", {})
                    current["name"] += function.get("name") or ""
                    current["arguments"] += function.get("arguments") or ""
                finish_reason = choice.get("finish_reason") or finish_reason
        tool_calls = [
            ToolCall(
                id=value["id"] or f"tool-{index}",
                name=value["name"],
                arguments=json.loads(value["arguments"] or "{}"),
            )
            for index, value in sorted(calls.items())
        ]
        if tool_calls or finish_reason:
            yield LLMResponse(tool_calls=tool_calls, finish_reason=finish_reason)
        elif not received:
            raise RuntimeError("Provider returned an empty streaming response")


class OpenRouterProvider(OpenAIProvider):
    def __init__(self, api_key: str = "", client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key, "https://openrouter.ai/api/v1", client)

    def _authorization_headers(self) -> dict[str, str]:
        if not self.api_key.strip():
            return {}
        return super()._authorization_headers()
