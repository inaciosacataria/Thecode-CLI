from collections.abc import Sequence
from pathlib import Path

from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nexus.agent.state import AgentStep
from nexus.security.secrets import looks_like_secret
from nexus.sessions.models import Session
from nexus.ui.console import console

LOGO = r"""
 _____  _    ____  _     _   _ _____ _____ ____ _   _
|_   _|/ \  | __ )| |   | | | |_   _| ____/ ___| | | |
  | | / _ \ |  _ \| |   | | | | | | |  _|| |   | |_| |
  | |/ ___ \| |_) | |___| |_| | | | | |__| |___|  _  |
  |_/_/   \_\____/|_____|\___/  |_| |_____\____|_| |_|
                         A G E N T
""".strip("\n")

_stream_live: Live | None = None
_stream_content = ""
_action_live: Live | None = None
_action_count = 0
_action_failures = 0


def content_width(maximum: int = 110) -> int:
    return max(20, min(console.width, maximum))


def _safe_symbol(symbol: str, fallback: str) -> str:
    try:
        symbol.encode(console.encoding or "utf-8")
    except UnicodeEncodeError:
        return fallback
    return symbol


def _response_panel(content: str) -> Panel:
    return Panel(
        Markdown(content or "Thinking…"),
        title="[brand] TheCode [/brand]",
        title_align="left",
        border_style="bright_black",
        padding=(0, 1),
        width=content_width(),
    )


def stream_start() -> None:
    global _stream_content, _stream_live
    finish_actions()
    stream_end()
    _stream_content = ""
    _stream_live = Live(_response_panel(""), console=console, refresh_per_second=12)
    _stream_live.start(refresh=True)


def stream_token(token: str) -> None:
    global _stream_content
    _stream_content += token
    if _stream_live:
        _stream_live.update(_response_panel(_stream_content), refresh=True)


def stream_end() -> None:
    global _stream_live
    if _stream_live:
        _stream_live.update(_response_panel(_stream_content), refresh=True)
        # Tool-only model turns should not leave an empty "Thinking" panel behind.
        _stream_live.transient = not bool(_stream_content)
        _stream_live.stop()
        _stream_live = None


def banner(root: Path, branch: str, provider: str, model: str, mode: str) -> None:
    if looks_like_secret(model):
        model = "[REDACTED: invalid model]"
    if console.width >= 67:
        console.print(Text(LOGO, style="brand"), overflow="crop", soft_wrap=True)
    else:
        compact_logo = Text("THECODE", style="brand")
        console.print(compact_logo)
    console.print()
    details = Text()
    details.append(root.name, style="bold white")
    details.append(f"  ·  {branch}\n", style="muted")
    details.append(f"{provider}/{model}", style="white")
    details.append("  ·  permissions ", style="muted")
    details.append(mode, style="warning" if mode == "ask" else "success")
    console.print(Panel(details, border_style="bright_black", padding=(0, 2), width=content_width()))


def format_duration(duration_ms: float) -> str:
    if duration_ms < 1000:
        return f"{duration_ms:.0f} ms"
    return f"{duration_ms / 1000:.1f} s"


def render_step(step: AgentStep) -> None:
    global _action_count, _action_failures, _action_live
    name = step.tool_name.replace("_", " ")
    if _action_live is None:
        _action_live = Live(Text(""), console=console, refresh_per_second=10)
        _action_live.start(refresh=True)
    if str(step.status) == "running":
        line = Text(f"{_safe_symbol('•', '*')} Working  ", style="muted")
        line.append(name, style="tool")
        _action_live.update(line, refresh=True)
        return

    _action_count += 1
    if str(step.status) in {"failed", "denied"}:
        _action_failures += 1
    _action_live.update(_action_summary(step), refresh=True)


def _action_summary(last_step: AgentStep | None = None) -> Text:
    failed = _action_failures > 0
    marker = "! " if failed else f"{_safe_symbol('✓', '+')} "
    line = Text(marker, style="warning" if failed else "success")
    noun = "action" if _action_count == 1 else "actions"
    line.append(f"Worked  ·  {_action_count} {noun}", style="muted")
    if failed:
        line.append(f"  ·  {_action_failures} failed", style="warning")
    if last_step and last_step.error:
        error = last_step.error.replace("\n", " ")
        if len(error) > 120:
            error = error[:117] + "…"
        line.append(f"\n  {error}", style="danger")
    return line


def finish_actions() -> None:
    global _action_count, _action_failures, _action_live
    if _action_live:
        _action_live.stop()
        if not console.is_terminal:
            console.print()
        _action_live = None
    _action_count = 0
    _action_failures = 0


def render_chat_hint() -> None:
    console.print(
        "[muted]Type a request or use[/muted] [command]/help[/command] "
        "[muted]for commands  ·  [/muted][command]/exit[/command] [muted]to leave[/muted]"
    )


def render_help() -> None:
    commands = [
        ("/status", "Git status"), ("/diff", "Current changes"),
        ("/context", "Context settings"), ("/session", "Session ID"),
        ("/compact", "Rebuild context"), ("/undo", "Undo guidance"),
        ("/config", "Effective config"), ("/model", "Active model"),
        ("/provider", "Change provider"), ("/permissions", "Permission mode"),
        ("/clear", "Clear screen"), ("/exit", "Leave TheCode"),
    ]
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style="command", no_wrap=True)
    table.add_column(style="muted")
    table.add_column(style="command", no_wrap=True)
    table.add_column(style="muted")
    midpoint = (len(commands) + 1) // 2
    for index in range(midpoint):
        left = commands[index]
        right = commands[index + midpoint] if index + midpoint < len(commands) else ("", "")
        table.add_row(left[0], left[1], right[0], right[1])
    console.print(
        Panel(
            table,
            title="[brand] Commands [/brand]",
            title_align="left",
            border_style="bright_black",
            width=content_width(),
        )
    )


def render_notice(message: str, *, kind: str = "info", title: str | None = None) -> None:
    styles = {"info": "bright_black", "success": "green", "warning": "yellow", "error": "red"}
    rendered_title = f" {title} " if title else None
    console.print(
        Panel(
            message,
            title=rendered_title,
            title_align="left",
            border_style=styles[kind],
            padding=(0, 1),
            width=content_width(),
        )
    )


def sessions_table(sessions: Sequence[Session]) -> Table:
    table = Table(
        "ID", "Project", "Provider / Model", "Status", "Updated",
        header_style="brand", border_style="bright_black", row_styles=("", "dim"),
        width=content_width(),
    )
    for item in sessions:
        table.add_row(item.id, Path(item.project_dir).name, f"{item.provider}/{item.model}", item.status, str(item.updated_at))
    return table
