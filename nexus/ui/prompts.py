import os
from collections.abc import Callable
from typing import cast

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from nexus.permissions.manager import PermissionResponse
from nexus.ui.console import console
from nexus.ui.renderer import content_width, finish_actions


class CleanPrompt(Prompt):
    prompt_suffix = " "

_PERMISSION_OPTIONS: tuple[tuple[str, str, PermissionResponse], ...] = (
    ("o", "Allow once", "once"),
    ("a", "Allow all this session", "session"),
    ("d", "Deny", "deny"),
)


def _permission_menu(selected: int) -> Group:
    lines: list[Text] = []
    for index, (shortcut, label, _) in enumerate(_PERMISSION_OPTIONS):
        active = index == selected
        marker = "›" if active else " "
        style = "reverse bold white" if active else "muted"
        lines.append(Text(f" {marker} [{shortcut}] {label} ", style=style))
    lines.append(Text("\n ↑/↓ navigate  •  Enter select  •  Esc deny", style="muted"))
    return Group(*lines)


def _interactive_permission_choice() -> PermissionResponse | None:
    if os.name != "nt" or not console.is_terminal:
        return None

    import msvcrt

    selected = 2
    shortcuts = {option[0]: index for index, option in enumerate(_PERMISSION_OPTIONS)}
    getwch = cast(Callable[[], str], getattr(msvcrt, "getwch"))
    with Live(_permission_menu(selected), console=console, auto_refresh=False) as live:
        while True:
            key = getwch()
            if key in {"\x00", "\xe0"}:
                arrow = getwch()
                if arrow == "H":
                    selected = (selected - 1) % len(_PERMISSION_OPTIONS)
                elif arrow == "P":
                    selected = (selected + 1) % len(_PERMISSION_OPTIONS)
                live.update(_permission_menu(selected), refresh=True)
                continue
            if key in {"\r", "\n"}:
                return _PERMISSION_OPTIONS[selected][2]
            if key == "\x1b":
                return "deny"
            shortcut = key.lower()
            if shortcut in shortcuts:
                return _PERMISSION_OPTIONS[shortcuts[shortcut]][2]


def confirm_action(description: str) -> PermissionResponse:
    finish_actions()
    console.print(
        Panel(
            description,
            title="[warning] Permission required [/warning]",
            subtitle="[muted]Review before continuing[/muted]",
            subtitle_align="left",
            border_style="bright_black",
            padding=(0, 1),
            width=content_width(100),
        )
    )
    interactive_choice = _interactive_permission_choice()
    if interactive_choice is not None:
        return interactive_choice

    console.print("[command][o][/command] Allow once   [command][a][/command] Allow all this session   [command][d][/command] Deny")
    choice = CleanPrompt.ask(
        "[brand]Choose[/brand] [muted]›[/muted]",
        choices=["o", "a", "d"],
        default="d",
        show_choices=False,
    )
    responses: dict[str, PermissionResponse] = {
        "o": "once",
        "a": "session",
        "d": "deny",
    }
    return responses[choice]


def user_input() -> str:
    console.print()
    return CleanPrompt.ask("[brand]You[/brand] [muted]›[/muted]")


def confirm_prompt(message: str, *, default: bool = False) -> bool:
    default_value = "y" if default else "n"
    answer = CleanPrompt.ask(
        f"[brand]{message}[/brand] [muted](y/n) ›[/muted]",
        choices=["y", "n"],
        default=default_value,
        show_choices=False,
    )
    return answer == "y"
