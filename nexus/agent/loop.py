from __future__ import annotations

import json
import time
from collections.abc import Callable
from difflib import unified_diff
from typing import Any

from nexus.agent.prompts import SYSTEM_PROMPT
from nexus.agent.state import AgentState, AgentStep, StepStatus
from nexus.llm.base import LLMProvider, LLMResponse, Message
from nexus.permissions.manager import PermissionManager
from nexus.security.paths import resolve_project_path
from nexus.sessions.manager import SessionManager
from nexus.sessions.models import StoredMessage
from nexus.tools.registry import ToolRegistry


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        permissions: PermissionManager,
        model: str,
        max_steps: int = 30,
        on_step: Callable[[AgentStep], None] | None = None,
        on_stream_start: Callable[[], None] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_stream_end: Callable[[], None] | None = None,
        on_actions_end: Callable[[], None] | None = None,
        session_manager: SessionManager | None = None,
        session_id: str | None = None,
        initial_messages: list[Message] | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.permissions = permissions
        self.model = model
        self.max_steps = max_steps
        self.on_step = on_step
        self.on_stream_start = on_stream_start
        self.on_token = on_token
        self.on_stream_end = on_stream_end
        self.on_actions_end = on_actions_end
        self.session_manager = session_manager
        self.session_id = session_id
        self.messages = [Message(role="system", content=system_prompt)]
        if initial_messages:
            self.messages.extend(initial_messages)

    def _store_message(self, role: str, content: str) -> None:
        if self.session_manager and self.session_id:
            self.session_manager.database.add_message(
                StoredMessage(session_id=self.session_id, role=role, content=content)
            )

    def _action_description(self, name: str, arguments: dict[str, Any]) -> str:
        label = name.replace("_", " ").title()
        path = arguments.get("path")
        command = arguments.get("command")
        source = arguments.get("source")
        destination = arguments.get("destination")
        if isinstance(path, str):
            content = arguments.get("content")
            detail = f"\n{path}"
            if isinstance(content, str):
                detail += f"\n{len(content)} characters"
            preview = self._change_preview(name, path, arguments)
            return label + detail + (f"\n---DIFF---\n{preview}" if preview else "")
        if isinstance(command, str):
            preview = command if len(command) <= 160 else command[:157] + "…"
            return f"{label}\n{preview}"
        if isinstance(source, str) and isinstance(destination, str):
            return f"{label}\n{source} → {destination}"
        keys = ", ".join(arguments) if arguments else "No arguments"
        return f"{label}\n{keys}"

    def _change_preview(self, name: str, value: str, arguments: dict[str, Any]) -> str:
        if name not in {"write_file", "edit_file", "delete_file"}:
            return ""
        try:
            path = resolve_project_path(self.registry.get(name).project_root, value)
            previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError, ValueError):
            return ""
        updated = previous
        if name == "write_file" and isinstance(arguments.get("content"), str):
            updated = arguments["content"]
        elif name == "edit_file":
            old = arguments.get("old_text")
            new = arguments.get("new_text")
            if isinstance(old, str) and isinstance(new, str) and previous.count(old) == 1:
                updated = previous.replace(old, new, 1)
        elif name == "delete_file":
            updated = ""
        diff = "".join(
            unified_diff(
                previous.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{value}",
                tofile=f"b/{value}",
            )
        )
        return diff[:20_000] + ("\n[diff truncated]" if len(diff) > 20_000 else "")

    async def run(self, request: str) -> tuple[str, AgentState]:
        state = AgentState(original_request=request)
        failed_calls: dict[str, str] = {}
        self._store_message("user", request)
        self.messages.append(Message(role="user", content=request))
        messages = self.messages
        for number in range(1, self.max_steps + 1):
            content_parts: list[str] = []
            tool_calls = []
            finish_reason: str | None = None
            if self.on_stream_start:
                self.on_stream_start()
            try:
                async for chunk in self.provider.stream_chat(
                    messages, self.registry.definitions(), self.model
                ):
                    if chunk.content:
                        content_parts.append(chunk.content)
                        if self.on_token:
                            self.on_token(chunk.content)
                    tool_calls.extend(chunk.tool_calls)
                    finish_reason = chunk.finish_reason or finish_reason
            finally:
                if self.on_stream_end:
                    self.on_stream_end()
            response = LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
            if response.content:
                messages.append(Message(role="assistant", content=response.content))
                self._store_message("assistant", response.content)
            if not response.tool_calls:
                return response.content, state
            for call in response.tool_calls:
                tool = self.registry.get(call.name)
                step = AgentStep(number=number, user_request=request, tool_name=call.name, arguments=call.arguments)
                started = time.perf_counter()
                signature = json.dumps(
                    {"name": call.name, "arguments": call.arguments}, sort_keys=True, default=str
                )
                if signature in failed_calls:
                    step.status = StepStatus.FAILED
                    step.error = "Repeated failed action blocked"
                    step.result = failed_calls[signature]
                    state.steps.append(step)
                    if self.on_step:
                        self.on_step(step)
                    message = (
                        f"Stopped because `{call.name}` repeated the same failed action. "
                        f"Last error: {failed_calls[signature]}"
                    )
                    self._present_local_response(message)
                    self._store_message("assistant", message)
                    return message, state
                decision = await self.permissions.authorize_async(
                    self._action_description(call.name, call.arguments), tool.risk_level
                )
                if not decision.allowed:
                    step.status, step.error = StepStatus.DENIED, decision.reason
                    result_text = f"Permission denied: {decision.reason}"
                else:
                    if self.on_step:
                        self.on_step(step)
                    try:
                        parsed = tool.input_schema.model_validate(call.arguments)
                        result = await tool.execute(parsed)
                        result_text = result.output if result.success else (result.error or "Tool failed")
                        step.status = StepStatus.SUCCEEDED if result.success else StepStatus.FAILED
                        step.error = result.error
                        if result.success and call.name in {"write_file", "edit_file", "delete_file"}:
                            self._record_file_change(result.metadata)
                    except (ValueError, OSError) as error:
                        result_text = str(error)
                        step.status, step.error = StepStatus.FAILED, str(error)
                step.duration_ms = (time.perf_counter() - started) * 1000
                step.result = result_text
                if step.status in {StepStatus.FAILED, StepStatus.DENIED}:
                    failed_calls[signature] = step.error or result_text
                state.steps.append(step)
                if self.on_step:
                    self.on_step(step)
                messages.append(Message(role="tool", content=result_text, name=call.name, tool_call_id=call.id))
                self._store_message("tool", result_text)
        if self.on_actions_end:
            self.on_actions_end()
        return f"Stopped after reaching the maximum of {self.max_steps} steps.", state

    def _present_local_response(self, message: str) -> None:
        if self.on_stream_start:
            self.on_stream_start()
        if self.on_token:
            self.on_token(message)
        if self.on_stream_end:
            self.on_stream_end()

    def _record_file_change(self, metadata: dict[str, object]) -> None:
        if not self.session_manager or not self.session_id:
            return
        path_value = metadata.get("path")
        if not isinstance(path_value, str):
            return
        path = self.session_manager.settings.project_root / path_value
        path = path.resolve()
        previous_value = metadata.get("previous")
        previous = previous_value if isinstance(previous_value, str) else None
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        self.session_manager.record_change(self.session_id, path, previous, current)
