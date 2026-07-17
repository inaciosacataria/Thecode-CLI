from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from nexus.agent.loop import AgentLoop
from nexus.agent.prompts import SYSTEM_PROMPT
from nexus.agent.state import AgentStep
from nexus.config.loader import load_settings
from nexus.llm.base import Message
from nexus.llm.router import create_provider
from nexus.permissions.manager import PermissionManager, PermissionResponse
from nexus.repository.instructions import load_project_instructions
from nexus.sessions.database import SessionDatabase
from nexus.sessions.manager import SessionManager
from nexus.tools.registry import ToolRegistry
from nexus.ui.prompts import confirm_action
from nexus.ui.renderer import finish_actions, render_step, stream_end, stream_start, stream_token


def build_agent(
    root: Path,
    *,
    read_only: bool = False,
    session_id: str | None = None,
    confirm: Callable[[str], PermissionResponse | bool | Awaitable[PermissionResponse | bool]] = confirm_action,
    on_step: Callable[[AgentStep], None] = render_step,
    on_stream_start: Callable[[], None] = stream_start,
    on_token: Callable[[str], None] = stream_token,
    on_stream_end: Callable[[], None] = stream_end,
    on_actions_end: Callable[[], None] = finish_actions,
    workspace_roots: dict[str, Path] | None = None,
) -> AgentLoop:
    settings = load_settings(root)
    registry = ToolRegistry.defaults(root, read_only=read_only, workspace_roots=workspace_roots)
    permissions = PermissionManager("safe" if read_only else settings.permissions.mode, confirm)
    session_manager = SessionManager(SessionDatabase(), settings)
    if session_id:
        session = session_manager.database.get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        stored = session_manager.database.list_messages(session_id)
        history = [
            Message.model_validate({"role": item.role, "content": item.content})
            for item in stored
            if item.role in {"user", "assistant"}
        ]
    else:
        session = session_manager.create()
        history = []
    return AgentLoop(
        create_provider(settings.llm),
        registry,
        permissions,
        settings.llm.model,
        settings.agent.max_steps,
        on_step,
        on_stream_start,
        on_token,
        on_stream_end,
        on_actions_end,
        session_manager,
        session.id,
        history,
        SYSTEM_PROMPT + "\n\n" + load_project_instructions(root, settings.context.max_characters // 2),
    )
