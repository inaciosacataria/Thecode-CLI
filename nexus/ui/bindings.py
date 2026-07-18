from textual.binding import Binding

APP_BINDINGS: list[Binding | tuple[str, str] | tuple[str, str, str]] = [
    Binding("ctrl+r", "run_prompt", "Run"),
    Binding("ctrl+t", "tests", "Tests"),
    Binding("ctrl+l", "clear", "Clear"),
    Binding("ctrl+k", "commands", "Commands", priority=True),
    Binding("ctrl+p", "quick_open", "Quick Open", priority=True),
    Binding("ctrl+c", "cancel", "Cancel", priority=True),
    Binding("escape", "cancel", "Cancel"),
    Binding("ctrl+q", "quit", "Quit"),
    Binding("r", "reveal_sensitive", "Reveal sensitive preview", show=False),
]
