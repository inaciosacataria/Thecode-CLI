from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    OptionList,
    ProgressBar,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
    Tree,
)

from nexus.agent.loop import AgentLoop
from nexus.agent.state import AgentStep
from nexus.app import build_agent
from nexus.config.loader import load_settings
from nexus.config.models import is_free_openrouter_model
from nexus.config.wizard import FALLBACK_MODELS, save_provider_configuration
from nexus.permissions.manager import PermissionResponse
from nexus.repository.architecture import analyze_architecture, render_architecture
from nexus.repository.git import current_branch
from nexus.repository.instructions import instruction_files
from nexus.security.paths import is_sensitive_path, resolve_project_path
from nexus.security.secrets import redact_secrets
from nexus.sessions.database import SessionDatabase
from nexus.tools.registry import ToolRegistry
from nexus.ui.bindings import APP_BINDINGS
from nexus.ui.encoding import symbols
from nexus.ui.responsive import layout_for_width
from nexus.ui.state import ActivityStatus, ConversationMessage, UIState
from nexus.ui.themes import CUSTOM_THEMES
from nexus.workspace import CodeWorkspace


class PromptArea(TextArea):
    BINDINGS = [Binding("shift+enter", "newline", "New line", show=False, priority=True)]

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def action_newline(self) -> None:
        self.insert("\n")

    def on_key(self, event: events.Key) -> None:
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key in {"up", "down"} and not self.text.lstrip().startswith("/"):
            navigate = getattr(self.app, "navigate_prompt_history", None)
            if navigate and navigate(-1 if event.key == "up" else 1):
                event.prevent_default()
                event.stop()
                return
        if event.key == "down" and self.text.lstrip().startswith("/"):
            suggestions = self.app.query_one("#command-suggestions", OptionList)
            if suggestions.display and suggestions.option_count:
                event.prevent_default()
                event.stop()
                suggestions.focus()
                return
        if event.key == "tab" and self.text.lstrip().startswith("/"):
            event.prevent_default()
            event.stop()
            apply_suggestion = getattr(self.app, "apply_first_command_suggestion", None)
            if apply_suggestion:
                apply_suggestion()
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            value = self.text
            self.load_text("")
            self.post_message(self.Submitted(value))


SPLASH_ART = """
████████████████████
██                ██
██    THECODE     ██
██                ██
████████████████████
""".strip("\n")


class PermissionScreen(ModalScreen[PermissionResponse]):
    BINDINGS = [
        Binding("o", "choose_once", "Allow once"),
        Binding("a", "choose_session", "Allow session"),
        Binding("d", "choose_deny", "Deny"),
        Binding("escape", "choose_deny", "Deny"),
    ]
    def __init__(self, description: str) -> None:
        super().__init__()
        self.description = description

    def compose(self) -> ComposeResult:
        summary, _, diff = self.description.partition("\n---DIFF---\n")
        with Vertical(id="permission-dialog"):
            yield Static("Permission required", classes="section-title")
            yield Static(summary, id="permission-description")
            if diff:
                yield RichLog(id="diff-review", wrap=False)
            with Horizontal(id="permission-actions"):
                yield Button("Deny", id="deny", variant="error")
                yield Button("Allow once", id="once", variant="primary")
                yield Button("Allow session", id="session", variant="success")

    def on_mount(self) -> None:
        _, separator, diff = self.description.partition("\n---DIFF---\n")
        if separator:
            self.query_one("#diff-review", RichLog).write(
                Syntax(diff, "diff", line_numbers=False, word_wrap=False)
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        values: dict[str, PermissionResponse] = {"deny": "deny", "once": "once", "session": "session"}
        self.dismiss(values[event.button.id or "deny"])

    def action_choose_once(self) -> None:
        self.dismiss("once")

    def action_choose_session(self) -> None:
        self.dismiss("session")

    def action_choose_deny(self) -> None:
        self.dismiss("deny")


COMMANDS = (
    ("Ctrl+P", "Quick Open files with fuzzy search"),
    ("@path/file", "Attach a project file to the prompt"),
    ("/commands", "Show every TUI command"),
    ("/config", "Configure provider, key, model, and permissions"),
    ("/models", "Show suggested models for the active provider"),
    ("/architect OBJECTIVE", "Analyze Current/Proposed architecture before applying"),
    ("/plan OBJECTIVE", "Create an approval-gated implementation plan"),
    ("/status", "Show repository status"),
    ("/diff", "Show current changes"),
    ("/review", "Review current changes"),
    ("/commit", "Create an approved Git commit"),
    ("/branch", "Show Git branches"),
    ("/theme NAME", "Change the visual theme"),
    ("/workspace NAME", "Switch the active workspace folder"),
    ("/processes", "Show active and completed processes"),
    ("/session", "Show the active session identifier"),
    ("/rules", "Show active project instructions"),
    ("/memory", "Show persistent project memory"),
    ("/remember TEXT", "Add a convention to project memory"),
    ("/new", "Start a new persisted session"),
    ("/delete-session", "Delete the current session after confirmation"),
    ("/clear", "Clear chat output"),
    ("/exit", "Close TheCode"),
)


COMMAND_CENTER_ACTIONS = (
    ("new-session", "New session", "Start with a clean conversation", ""),
    ("resume-session", "Resume session", "Show the current session and resume options", ""),
    ("change-model", "Change model", "Configure the active model", ""),
    ("change-provider", "Change provider", "Configure the LLM provider", ""),
    ("change-permissions", "Change permissions", "Choose safe, ask, or auto mode", ""),
    ("open-file", "Open file", "Search project files", "Ctrl+P"),
    ("run-tests", "Run tests", "Run the relevant test suite", "Ctrl+T"),
    ("review-changes", "Review changes", "Review the current repository changes", ""),
    ("show-diff", "Show diff", "Open the session diff", ""),
    ("show-architecture", "Show architecture", "Open Architect mode", ""),
    ("clear-conversation", "Clear conversation", "Clear only the visible conversation", "Ctrl+L"),
    ("cancel-task", "Cancel task", "Cancel the current operation", "Esc"),
    ("exit", "Exit", "Close TheCode", "Ctrl+Q"),
)


class CommandsScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self) -> None:
        super().__init__()
        self.actions = {action: (title, description, shortcut) for action, title, description, shortcut in COMMAND_CENTER_ACTIONS}

    def compose(self) -> ComposeResult:
        with Vertical(id="commands-dialog"):
            yield Static("Command center", classes="section-title")
            yield Input(placeholder="Search commands…", id="command-query")
            yield OptionList(*self._options(""), id="command-list")

    def _options(self, query: str) -> list[str]:
        words = query.casefold().split()
        rendered: list[str] = []
        for action, (title, description, shortcut) in self.actions.items():
            searchable = f"{title} {description} {action}".casefold()
            if all(self._fuzzy_match(word, searchable) for word in words):
                suffix = f"  [{shortcut}]" if shortcut else ""
                rendered.append(f"{title}{suffix}  —  {description}")
        return rendered

    @staticmethod
    def _fuzzy_match(query: str, value: str) -> bool:
        iterator = iter(value)
        return all(any(character == candidate for candidate in iterator) for character in query)

    @on(Input.Changed, "#command-query")
    def filter_commands(self, event: Input.Changed) -> None:
        self.query_one("#command-list", OptionList).set_options(self._options(event.value))

    @on(OptionList.OptionSelected, "#command-list")
    def choose_command(self, event: OptionList.OptionSelected) -> None:
        title = str(event.option.prompt).split("  [", 1)[0].split("  —", 1)[0]
        action = next(
            (key for key, values in self.actions.items() if values[0] == title),
            None,
        )
        self.dismiss(action)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen[dict[str, str] | None]):
    def __init__(self, provider: str, model: str, permission_mode: str) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.permission_mode = permission_mode

    def _model_options(self, provider: str, current: str) -> list[tuple[str, str]]:
        values = list(dict.fromkeys(FALLBACK_MODELS[provider]))
        if provider == "openrouter":
            values = [value for value in values if is_free_openrouter_model(value)]
        if current and current in values:
            values = [current, *[value for value in values if value != current]]
        return [(value, value) for value in values]

    def compose(self) -> ComposeResult:
        providers = [
            (name.title(), name)
            for name in ("openrouter", "openai", "anthropic", "gemini", "ollama")
        ]
        permissions = [(name.title(), name) for name in ("ask", "plan", "agent", "auto")]
        model_options = self._model_options(self.provider, self.model)
        initial_model = model_options[0][1]
        if self.model in {value for value, _ in model_options}:
            initial_model = self.model
        with Vertical(id="settings-dialog"):
            yield Static("AI configuration", classes="section-title")
            yield Static("Provider", classes="field-label")
            yield Select(providers, value=self.provider, id="settings-provider")
            yield Static("API key (optional for OpenRouter, not required for Ollama)", classes="field-label")
            yield Input(password=True, placeholder="Paste a new key", id="settings-key")
            yield Static("Model identifier", classes="field-label")
            yield Select(model_options, value=initial_model, id="settings-model")
            yield Static("Permission mode", classes="field-label")
            yield Select(permissions, value=self.permission_mode, id="settings-permissions")
            with Horizontal(id="settings-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        provider = str(self.query_one("#settings-provider", Select).value)
        permission = str(self.query_one("#settings-permissions", Select).value)
        self.dismiss(
            {
                "provider": provider,
                "credential": self.query_one("#settings-key", Input).value,
                "model": str(self.query_one("#settings-model", Select).value),
                "permission": permission,
            }
        )

    @on(Select.Changed, "#settings-provider")
    def update_provider_defaults(self, event: Select.Changed) -> None:
        provider = str(event.value)
        if provider in FALLBACK_MODELS:
            model_select = self.query_one("#settings-model", Select)
            current = str(model_select.value or "")
            options = self._model_options(provider, current or FALLBACK_MODELS[provider][0])
            model_select.set_options(options)
            available = {value for value, _ in options}
            if current == self.model or not current.strip() or current not in available:
                model_select.value = options[0][1]
            key_input = self.query_one("#settings-key", Input)
            key_input.placeholder = (
                "Optional for OpenRouter"
                if provider == "openrouter"
                else "Not required for local Ollama"
                if provider == "ollama"
                else "Paste a new key or keep the existing key"
            )


class QuickOpenScreen(ModalScreen[Path | None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, roots: dict[str, Path]) -> None:
        super().__init__()
        self.files: dict[str, Path] = {}
        for root_name, root in roots.items():
            for path in root.rglob("*"):
                if not path.is_file() or any(
                    part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts
                ) or is_sensitive_path(path):
                    continue
                label = f"{root_name}/{path.relative_to(root).as_posix()}"
                self.files[label] = path
                if len(self.files) >= 5000:
                    break

    def compose(self) -> ComposeResult:
        with Vertical(id="quick-open-dialog"):
            yield Static("Quick Open", classes="section-title")
            yield Input(placeholder="Type a file name…", id="quick-query")
            yield OptionList(*list(self.files)[:200], id="quick-results")

    @on(Input.Changed, "#quick-query")
    def filter_files(self, event: Input.Changed) -> None:
        query = event.value.casefold().replace(" ", "")
        labels = list(self.files)
        if query:
            labels = [label for label in labels if self._matches(query, label.casefold())]
        self.query_one("#quick-results", OptionList).set_options(labels[:200])

    @staticmethod
    def _matches(query: str, value: str) -> bool:
        iterator = iter(value)
        return all(any(character == candidate for candidate in iterator) for character in query)

    @on(OptionList.OptionSelected, "#quick-results")
    def choose_file(self, event: OptionList.OptionSelected) -> None:
        label = str(event.option.prompt)
        self.dismiss(self.files.get(label))


class TheCodeApp(App[None]):
    CSS_PATH = "thecode.tcss"
    TITLE = "TheCode"
    BINDINGS = APP_BINDINGS

    def __init__(
        self, root: Path, session_id: str | None = None, workspace_path: Path | None = None
    ) -> None:
        super().__init__()
        self.workspace = CodeWorkspace.load(workspace_path) if workspace_path else None
        self.root = self._initial_root(root)
        self.session_id = session_id
        self.settings = load_settings(root)
        self.ui_state = UIState(
            project=self.root.name,
            provider=self.settings.llm.provider,
            model=self.settings.llm.model,
            session_id=session_id,
        )
        self.symbols = symbols()
        self.agent: AgentLoop | None = None
        self.stream = ""
        self.last_response = ""
        self.action_count = 0
        self.startup_error: str | None = None
        self.last_prompt = ""
        self.branch = "not a git repository"
        self._sidebar_signature: tuple[object, ...] | None = None
        self.spinner_index = 0
        self.request_started = 0.0
        self.session_started = time.monotonic()
        self.first_token_ms: float | None = None
        self.output_tokens_estimate = 0
        self.input_tokens_estimate = 0
        self.context_percent = 0
        self.task_items: list[tuple[str, str]] = []
        self.task_item_ids: dict[str, int] = {}
        self.activities = self.ui_state.activities
        self.activity_history = self.ui_state.activity_history
        self.request_number = 0
        self.request_activity_id: str | None = None
        self.architect_mode = False
        self.architect_objective = ""
        self.architect_previous_permission_mode: str | None = None
        self.suppress_command_suggestions = False
        self.prompt_history: list[str] = []
        self.prompt_history_index = 0
        self.prompt_draft = ""
        self.indexed_file_count = 0
        self._boot_visible = True
        self.active_task_id: str | None = None
        self.task_cancelled = False
        self.last_stream_render = 0.0
        self.resize_generation = 0
        self.protected_preview_path: Path | None = None
        self.sensitive_preview_revealed = False

    def _initial_root(self, fallback: Path) -> Path:
        if not self.workspace:
            return fallback
        state = self.workspace.path.parent / ".nexus" / "workspace-active"
        if state.exists():
            try:
                return self.workspace.folder(state.read_text(encoding="utf-8").strip()).path
            except ValueError:
                pass
        return self.workspace.folders[0].path

    def compose(self) -> ComposeResult:
        with Vertical(id="boot"):
            yield Static(SPLASH_ART, id="boot-brand")
            yield Static("", id="boot-title")
            yield Static("", id="boot-status")
        with Vertical(id="shell"):
            with Horizontal(id="topbar"):
                yield Static("THECODE", id="brand")
                yield Static(self.root.name, id="header-context")
                yield Static(self.settings.llm.model, id="model")
            with Horizontal(id="workspace"):
                with Vertical(id="sidebar"):
                    yield Static("", id="sidebar-info")
                    yield Static("", id="tasks")
                    yield Tree("Project", id="project-tree")
                with Vertical(id="main"):
                    yield Static(
                        "[b $primary]THECODE[/]\n[dim]Ask anything about this repository.\n"
                        "Type /help for commands.[/]",
                        id="empty-state",
                    )
                    yield RichLog(id="history", wrap=True, markup=True, max_lines=500)
                    yield Static("", id="response")
                    yield RichLog(id="activity-log", wrap=True, markup=True, max_lines=100)
                    yield Static("", id="activity")
                    yield ProgressBar(total=100, show_eta=False, id="progress")
                    yield OptionList(id="command-suggestions")
                    yield PromptArea(placeholder="Ask anything · @file adds context · / opens commands", id="prompt")
                with TabbedContent("Preview", "Diff", "Files", "Architecture", "Terminal", "Tools", "Logs", id="preview"):
                    with TabPane("Preview", id="preview-tab"):
                        yield RichLog(id="code-preview", wrap=True, markup=True)
                    with TabPane("Diff", id="diff-tab"):
                        yield RichLog(id="diff-preview", wrap=False, markup=True)
                    with TabPane("Files", id="files-tab"):
                        yield RichLog(id="files-preview", wrap=True, markup=True)
                    with TabPane("Architecture", id="architecture-tab"):
                        yield RichLog(id="architecture-preview", wrap=True, markup=True)
                        with Horizontal(id="architecture-actions"):
                            yield Button("Discard", id="discard-architecture")
                            yield Button("Edit plan", id="edit-architecture")
                            yield Button("Execute plan", id="apply-architecture", variant="success")
                    with TabPane("Terminal", id="terminal-tab"):
                        yield RichLog(id="terminal-log", wrap=True, markup=True, max_lines=2000)
                    with TabPane("Tools", id="tools-tab"):
                        yield RichLog(id="tool-inspector", wrap=True, markup=True, max_lines=500)
                    with TabPane("Logs", id="logs-tab"):
                        yield RichLog(id="debug-log", wrap=True, markup=True, max_lines=1000)
        yield Static("Ready", id="statusbar")
        yield Static(
            "[b $primary]ENTER[/] Send   [b $primary]TAB[/] Focus   [b $primary]CTRL+R[/] Run   "
            "[b $primary]CTRL+T[/] Tests   [b $primary]CTRL+K[/] Commands   "
            "[b $primary]CTRL+L[/] Clear   [b $primary]ESC[/] Cancel",
            id="bottom",
        )

    async def on_mount(self) -> None:
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)
        self.theme = self._saved_theme()
        self.query_one("#shell").display = False
        self.query_one("#boot").display = True
        self.indexed_file_count = self._visible_file_count()
        await self._refresh_sidebar()
        self.query_one("#code-preview", RichLog).write(
            "[b $primary]PREVIEW[/]\nSelect a file in the project tree or ask TheCode to read it."
        )
        self.query_one("#terminal-log", RichLog).write(
            "[b $primary]LIVE TERMINAL[/]\nProcess output will appear here in real time."
        )
        self.query_one("#diff-preview", RichLog).write("[dim]Changes will appear here.[/]")
        self.query_one("#files-preview", RichLog).write("[dim]Recently accessed files will appear here.[/]")
        self.query_one("#tool-inspector", RichLog).write("[b $primary]TOOL INSPECTOR[/]")
        self.query_one("#architecture-preview", RichLog).write(
            "[b $primary]ARCHITECT MODE[/]\nUse /architect OBJECTIVE to analyze a current and proposed design."
        )
        self.query_one("#architecture-actions").display = False
        self.query_one("#progress", ProgressBar).display = False
        self.query_one("#command-suggestions").display = False
        if not self.is_headless:
            try:
                agent = build_agent(
                    self.root,
                    session_id=self.session_id,
                    confirm=self.confirm_action,
                    on_step=self.render_step,
                    on_stream_start=self.stream_start,
                    on_token=self.stream_token,
                    on_stream_end=self.stream_end,
                    on_actions_end=self.actions_end,
                    workspace_roots=self._workspace_roots(),
                )
                self.agent = agent
                self.session_id = agent.session_id
                self.ui_state.session_id = agent.session_id
                self.ui_state.connection_status = "connected"
                self._restore_conversation(agent.messages)
                if agent.registry.process_manager:
                    self._attach_process_manager(agent)
            except Exception as error:
                self.startup_error = str(error)
                self.ui_state.connection_status = "unavailable"
                self._show_error("Agent unavailable", error)
        self.query_one("#prompt", PromptArea).focus()
        self.query_one("#response").display = False
        self.query_one("#empty-state").display = True
        self.query_one("#activity").display = False
        self.query_one("#response", Static).border_title = "Assistant"
        self._apply_responsive_layout(self.size.width)
        self.set_interval(1.0, self.update_process_clock)
        self.set_interval(1.0, self._refresh_statusbar)
        self.set_interval(2.0, self.refresh_repository_state)
        await self._refresh_sidebar()
        self._build_project_tree()
        self.load_repository_architecture()
        if self.is_headless:
            self._finish_boot()
        else:
            self.set_timer(5.0, self._finish_boot)

    def _finish_boot(self) -> None:
        if not self._boot_visible:
            return
        self._boot_visible = False
        self.query_one("#boot").display = False
        self.query_one("#shell").display = True
        self.query_one("#prompt", PromptArea).focus()

    def _restore_conversation(self, messages: list[Any]) -> None:
        visible = [
            message
            for message in messages
            if message.role in {"user", "assistant"}
            and message.content.strip().casefold()
            not in {"thinking...", "thinking…", "ready", "loaded project"}
        ][-100:]
        if not visible:
            return
        history = self.query_one("#history", RichLog)
        self.ui_state.conversation.clear()
        for message in visible:
            role = message.role
            content = message.content
            self.ui_state.conversation.append(ConversationMessage(role, content))
            if role == "user":
                history.write(Text(f"You  ›  {content}", style="bold"))
            else:
                history.write(RichMarkdown(content))
        self.query_one("#empty-state").display = False

    async def _refresh_sidebar(self) -> None:
        self.branch = await current_branch(self.root)
        tools = len(
            ToolRegistry.defaults(self.root, workspace_roots=self._workspace_roots()).definitions()
        )
        instructions = instruction_files(self.root)
        skills = sum(path.name == "SKILL.md" for path in instructions)
        rules = len(instructions) - skills
        signature = (
            self.root,
            self.branch,
            self.settings.llm.provider,
            self.settings.llm.model,
            self.session_id,
            tools,
            self.output_tokens_estimate,
            self.input_tokens_estimate,
            self.context_percent,
            self.first_token_ms,
            rules,
            skills,
            self.indexed_file_count,
            self.agent is not None,
            self.startup_error,
        )
        if signature == self._sidebar_signature:
            return
        self._sidebar_signature = signature
        folders = ""
        if self.workspace:
            rendered = [
                f"{'●' if item.path == self.root else '○'} {item.name}"
                for item in self.workspace.folders
            ]
            folders = "[b $primary]WORKSPACE[/]\n" + "\n".join(rendered) + "\n\n"
        latency = f"{self.first_token_ms:.0f} ms first token" if self.first_token_ms is not None else "waiting"
        connection = (
            "[green]● Connected[/]"
            if self.agent
            else "[red]● Unavailable[/]"
            if self.startup_error
            else "[yellow]● Initializing[/]"
        )
        session = (self.session_id or "new")[:12]
        sidebar_markup = (
            f"{folders}[b $primary]PROJECT[/]\n{self.root.name}\n{self.branch}\n\n"
            f"[b $primary]MODEL[/]\n{self.settings.llm.provider}\n{self.settings.llm.model}\n\n"
            f"[b $primary]SESSION[/]\n{tools} tools\n{session}\n"
            f"{rules} rules · {skills} skills\n\n"
            f"[b $primary]STATUS[/]\n{connection}\n\n"
            f"[b $primary]INDEX[/]\n{self.indexed_file_count} files\n\n"
            f"[b $primary]USAGE[/]\nContext {self.context_percent}%\n"
            f"{self.input_tokens_estimate} input · {self.output_tokens_estimate} output\n"
            f"[dim]Cost unavailable · {latency}[/]"
        )
        self.query_one("#sidebar-info", Static).update(sidebar_markup)
        self.query_one("#header-context", Static).update(f"{self.root.name} · {self.branch}")

    def _visible_file_count(self) -> int:
        count = 0
        for path in self.root.rglob("*"):
            if path.is_file() and not is_sensitive_path(path) and not any(
                part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts
            ):
                count += 1
                if count >= 5000:
                    break
        return count

    def _build_project_tree(self) -> None:
        tree = self.query_one("#project-tree", Tree)
        tree.clear()
        tree.root.set_label(self.root.name)
        tree.root.data = self.root
        try:
            items = sorted(self.root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            items = []
        for item in items[:100]:
            if item.name in {".git", ".venv", "node_modules"} or is_sensitive_path(item):
                continue
            node = tree.root.add(f"{'▸' if item.is_dir() else '·'} {item.name}", data=item)
            if item.is_dir():
                node.add_leaf("…", data=None)
        tree.root.expand()

    @on(Tree.NodeExpanded, "#project-tree")
    def load_tree_directory(self, event: Tree.NodeExpanded[Path]) -> None:
        path = event.node.data
        if not isinstance(path, Path) or not path.is_dir():
            return
        if not any(child.data is None for child in event.node.children):
            return
        event.node.remove_children()
        try:
            items = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            return
        for item in items[:100]:
            if item.name in {".git", ".venv", "node_modules", "dist", "build", "__pycache__"} or is_sensitive_path(item):
                continue
            child = event.node.add(f"{'▸' if item.is_dir() else '·'} {item.name}", data=item)
            if item.is_dir():
                child.add_leaf("…", data=None)

    @work(thread=True, exclusive=True, group="architecture-scan")
    def load_repository_architecture(self) -> None:
        model = analyze_architecture(self.root)
        self.call_from_thread(self._render_repository_architecture, render_architecture(model))

    def _render_repository_architecture(self, content: str) -> None:
        if not self.is_running:
            return
        preview = self.query_one("#architecture-preview", RichLog)
        if not self.architect_mode:
            preview.clear()
            preview.write(Text(content))

    @on(Tree.NodeSelected, "#project-tree")
    def preview_tree_file(self, event: Tree.NodeSelected[Path]) -> None:
        path = event.node.data
        if not isinstance(path, Path) or not path.is_file():
            return
        if is_sensitive_path(path):
            self._show_protected_preview(path)
            return
        self._clear_sensitive_preview_state()
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.notify("File cannot be previewed as text", severity="warning")
            return
        preview = self.query_one("#code-preview", RichLog)
        preview.clear()
        preview.write(Syntax(content[:200_000], self._lexer(str(path)), line_numbers=True, word_wrap=True))
        self.ui_state.selected_file = path.relative_to(self.root).as_posix()
        self.query_one("#preview", TabbedContent).active = "preview-tab"

    async def refresh_repository_state(self) -> None:
        branch = await current_branch(self.root)
        if branch != self.branch:
            self._sidebar_signature = None
            await self._refresh_sidebar()

    def on_resize(self, event: events.Resize) -> None:
        self.resize_generation += 1
        generation = self.resize_generation
        width = event.size.width
        self.set_timer(
            0.075,
            lambda: self._apply_responsive_layout(width)
            if generation == self.resize_generation
            else None,
        )

    def _apply_responsive_layout(self, width: int) -> None:
        try:
            layout = layout_for_width(width)
            compact = layout.density == "compact"
            medium = layout.density == "medium"
            self.query_one("#sidebar").display = layout.sidebar
            self.query_one("#preview").display = layout.preview
            self.query_one("#header-context").display = layout.sidebar
            self.query_one("#model").display = False
            self.query_one("#activity-log").display = layout.activity
            self.query_one("#progress").display = layout.activity and any(
                item.finished_at is None and item.progress is not None
                for item in self.activities.values()
            )
            prompt = self.query_one("#prompt", PromptArea)
            prompt.styles.height = layout.prompt_height
            bottom = self.query_one("#bottom", Static)
            if compact:
                bottom.update(
                    "[b $primary]ENTER[/] Send   [b $primary]CTRL+K[/] Commands   "
                    "[b $primary]ESC[/] Cancel"
                )
            elif medium:
                bottom.update(
                    "[b $primary]ENTER[/] Send   [b $primary]TAB[/] Focus   "
                    "[b $primary]CTRL+K[/] Commands   [b $primary]CTRL+L[/] Clear   "
                    "[b $primary]ESC[/] Cancel"
                )
            else:
                bottom.update(
                    "[b $primary]ENTER[/] Send   [b $primary]TAB[/] Focus   "
                    "[b $primary]CTRL+R[/] Run   [b $primary]CTRL+T[/] Tests   "
                    "[b $primary]CTRL+P[/] Files   [b $primary]CTRL+K[/] Commands   "
                    "[b $primary]CTRL+L[/] Clear   "
                    "[b $primary]ESC[/] Cancel"
                )
        except NoMatches:
            pass

    def _saved_theme(self) -> str:
        path = self.root / ".nexus" / "theme"
        return path.read_text(encoding="utf-8").strip() if path.exists() else "nexus-aurora"

    def _workspace_roots(self) -> dict[str, Path] | None:
        if not self.workspace:
            return None
        return {folder.name: folder.path for folder in self.workspace.folders}

    async def confirm_action(self, description: str) -> PermissionResponse:
        if self.architect_mode:
            self.notify("Architect analysis is read-only", severity="warning")
            return "deny"
        return await self.push_screen_wait(PermissionScreen(description))

    @on(PromptArea.Submitted)
    def submit_prompt(self, event: PromptArea.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        if prompt.startswith("/"):
            self.handle_command(prompt)
            return
        self._archive_response()
        self.query_one("#empty-state").display = False
        self.ui_state.errors.clear()
        self.ui_state.status_message = ""
        self.last_prompt = prompt
        self.ui_state.current_task = prompt
        if not self.prompt_history or self.prompt_history[-1] != prompt:
            self.prompt_history.append(prompt)
            self.prompt_history = self.prompt_history[-100:]
        self.prompt_history_index = len(self.prompt_history)
        self.prompt_draft = ""
        self.input_tokens_estimate += max(1, len(prompt) // 4)
        self.query_one("#history", RichLog).write(Text(f"You  ›  {prompt}", style="bold"))
        try:
            expanded, attached = self._expand_file_mentions(prompt)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        if attached:
            self.query_one("#history", RichLog).write(
                Text(f"  Attached: {', '.join(attached)}", style="dim")
            )
        self.run_agent(expanded)

    def navigate_prompt_history(self, direction: int) -> bool:
        if not self.prompt_history:
            return False
        prompt = self.query_one("#prompt", PromptArea)
        if self.prompt_history_index == len(self.prompt_history):
            self.prompt_draft = prompt.text
        self.prompt_history_index = max(
            0, min(len(self.prompt_history), self.prompt_history_index + direction)
        )
        value = (
            self.prompt_draft
            if self.prompt_history_index == len(self.prompt_history)
            else self.prompt_history[self.prompt_history_index]
        )
        prompt.load_text(value)
        prompt.move_cursor(prompt.document.end)
        return True

    @on(TextArea.Changed, "#prompt")
    def update_command_suggestions(self) -> None:
        prompt = self.query_one("#prompt", PromptArea).text.lstrip()
        suggestions = self.query_one("#command-suggestions", OptionList)
        if self.suppress_command_suggestions:
            self.suppress_command_suggestions = False
            suggestions.display = False
            return
        if not prompt.startswith("/") or "\n" in prompt:
            suggestions.display = False
            return
        query = prompt.casefold()
        matches = [
            f"{command}  —  {description}"
            for command, description in COMMANDS
            if command.casefold().startswith(query)
            or command.split()[0].casefold().startswith(query)
        ]
        suggestions.set_options(matches[:8])
        suggestions.display = bool(matches)

    @on(OptionList.OptionSelected, "#command-suggestions")
    def choose_command_suggestion(self, event: OptionList.OptionSelected) -> None:
        command = str(event.option.prompt).split("  —  ", 1)[0]
        self._apply_command_suggestion(command)

    def apply_first_command_suggestion(self) -> None:
        suggestions = self.query_one("#command-suggestions", OptionList)
        if not suggestions.option_count:
            return
        option = suggestions.get_option_at_index(0)
        command = str(option.prompt).split("  —  ", 1)[0]
        self._apply_command_suggestion(command)

    def _apply_command_suggestion(self, command: str) -> None:
        value = command.replace(" NAME", " ").replace(" OBJECTIVE", " ")
        prompt = self.query_one("#prompt", PromptArea)
        self.suppress_command_suggestions = True
        prompt.load_text(value)
        suggestions = self.query_one("#command-suggestions", OptionList)
        suggestions.display = False
        prompt.focus()

    def _expand_file_mentions(self, prompt: str) -> tuple[str, list[str]]:
        mentions = re.findall(r"(?<!\w)@([^\s,;]+)", prompt)
        if not mentions:
            return prompt, []
        roots = self._workspace_roots() or {self.root.name: self.root}
        attachments: list[str] = []
        blocks: list[str] = []
        for mention in dict.fromkeys(mentions):
            if mention in {"git", "diff"}:
                tool = "git_status" if mention == "git" else "git_diff"
                attachments.append(f"@{mention}")
                blocks.append(f"<context_request>Use {tool} before answering.</context_request>")
                continue
            if mention in {"terminal", "problems"}:
                lines = self.ui_state.terminal_output[-100:]
                if mention == "problems":
                    lines = [line for line in lines if line.startswith("stderr: ")]
                attachments.append(f"@{mention}")
                blocks.append(
                    f'<attached_context type="{mention}">\n'
                    + redact_secrets("\n".join(lines))
                    + "\n</attached_context>"
                )
                continue
            if mention == "selection":
                if not self.ui_state.selected_file:
                    raise ValueError("Cannot attach @selection: no file is selected")
                mention = self.ui_state.selected_file
            if mention.startswith("folder/"):
                relative_folder = mention.removeprefix("folder/")
                folder = resolve_project_path(self.root, relative_folder, must_exist=True)
                if not folder.is_dir():
                    raise ValueError(f"Cannot attach @{mention}: not a folder")
                selected = [
                    path
                    for path in folder.rglob("*")
                    if path.is_file()
                    and not is_sensitive_path(path)
                    and not any(
                        part in {".git", "node_modules", ".venv", "dist", "build"}
                        for part in path.parts
                    )
                    and path.stat().st_size <= self.settings.context.max_file_size
                ][: self.settings.context.max_files_per_turn]
                rendered: list[str] = []
                used = 0
                budget = self.settings.context.max_characters // 2
                for path in selected:
                    try:
                        block = (
                            f'<file path="{path.relative_to(self.root).as_posix()}">\n'
                            + path.read_text(encoding="utf-8")
                            + "\n</file>"
                        )
                        if used + len(block) > budget:
                            break
                        rendered.append(block)
                        used += len(block)
                    except (OSError, UnicodeDecodeError):
                        continue
                attachments.append(f"@{mention}")
                blocks.append(
                    f'<attached_folder path="{relative_folder}">\n'
                    + "\n".join(rendered)
                    + "\n</attached_folder>"
                )
                continue
            root = self.root
            relative = mention
            if "/" in mention:
                prefix, remainder = mention.split("/", 1)
                if prefix in roots:
                    root, relative = roots[prefix], remainder
            try:
                path = resolve_project_path(root, relative, must_exist=True)
            except (OSError, ValueError) as error:
                raise ValueError(f"Cannot attach @{mention}: {error}") from error
            if not path.is_file():
                raise ValueError(f"Cannot attach @{mention}: not a file")
            if is_sensitive_path(path):
                raise ValueError(f"Cannot attach @{mention}: sensitive files are protected")
            if path.stat().st_size > self.settings.context.max_file_size:
                raise ValueError(f"Cannot attach @{mention}: file exceeds context size limit")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(f"Cannot attach @{mention}: not a UTF-8 text file") from error
            attachments.append(mention)
            blocks.append(f"<attached_file path=\"{mention}\">\n{content}\n</attached_file>")
        return prompt + "\n\n" + "\n\n".join(blocks), attachments

    @work(exclusive=True, group="agent")
    async def run_agent(self, prompt: str) -> None:
        task_id = uuid.uuid4().hex
        self.active_task_id = task_id
        self.task_cancelled = False
        input_widget = self.query_one("#prompt", PromptArea)
        input_widget.disabled = True
        try:
            if self.agent is None:
                raise RuntimeError(self.startup_error or "Agent is not initialized")
            self.agent.on_stream_start = lambda: self.stream_start(task_id)
            self.agent.on_token = lambda token: self.stream_token(token, task_id)
            self.agent.on_stream_end = lambda: self.stream_end(task_id)
            self.agent.on_step = lambda step: self.render_step(step, task_id)
            self.agent.on_actions_end = lambda: self.actions_end(task_id)
            await self.agent.run(prompt)
        except Exception as error:
            if self._event_is_current(task_id):
                self._show_error("Request failed", error)
        finally:
            if self.active_task_id == task_id:
                self.active_task_id = None
                input_widget.disabled = False
                input_widget.focus()

    def _event_is_current(self, task_id: str | None) -> bool:
        return task_id is None or (
            task_id == self.active_task_id and not self.task_cancelled and self.is_running
        )

    def _show_error(self, title: str, error: Exception) -> None:
        message = str(error) or error.__class__.__name__
        self.ui_state.errors.append(f"{title}: {message}")
        self.ui_state.errors = self.ui_state.errors[-20:]
        self.stream = f"## {title}\n\n{message}\n\nRun `thecode doctor` to verify the configuration."
        self.last_response = self.stream
        response = self.query_one("#response", Static)
        response.display = True
        response.update(RichMarkdown(self.stream))
        self.query_one("#activity", Static).update("✖ Failed")
        self.notify(message, title=title, severity="error", timeout=10)

    def _archive_response(self) -> None:
        if self.last_response:
            self.query_one("#history", RichLog).write(RichMarkdown(self.last_response))
            self.last_response = ""
            self.query_one("#response", Static).update("")
            self.query_one("#response").display = False

    def stream_start(self, task_id: str | None = None) -> None:
        if not self._event_is_current(task_id):
            return
        self._archive_response()
        self.stream = ""
        self.last_stream_render = 0.0
        self.last_response = ""
        self.request_started = time.monotonic()
        self.first_token_ms = None
        response = self.query_one("#response", Static)
        response.display = True
        response.update("Analyzing request…")
        self.query_one("#activity", Static).update(f"{self.symbols.bullet} Analyzing request…")
        self.request_number += 1
        self.request_activity_id = f"request-{self.request_number}"
        self._update_activity(
            self.request_activity_id, "Generate response", ActivityStatus.RUNNING, 10
        )
        self.task_items = []
        self.task_item_ids = {}
        self._render_tasks()
        self._update_assistant_status("Analyzing request…", progress=10)

    def stream_token(self, token: str, task_id: str | None = None) -> None:
        if not self._event_is_current(task_id):
            return
        if self.first_token_ms is None and self.request_started:
            self.first_token_ms = (time.monotonic() - self.request_started) * 1000
        self.stream += token
        if self.stream:
            self.query_one("#activity", Static).update("Streaming response…")
        if self.request_activity_id:
            progress = min(90, 10 + len(self.stream) // 80)
            self._update_activity(
                self.request_activity_id, "Generate response", ActivityStatus.RUNNING, progress
            )
        self._render_stream()

    def _render_stream(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_stream_render < 1 / 30:
            return
        self.last_stream_render = now
        response = self.query_one("#response", Static)
        response.display = True
        response.update(RichMarkdown(self.stream))

    def stream_end(self, task_id: str | None = None) -> None:
        if not self._event_is_current(task_id):
            return
        self._render_stream(force=True)
        self.last_response = self.stream
        self.output_tokens_estimate += max(1, len(self.stream) // 4) if self.stream else 0
        if self.agent:
            context_characters = sum(len(message.content) for message in self.agent.messages)
            self.context_percent = min(
                100, round(context_characters / self.settings.context.max_characters * 100)
            )
        self._sidebar_signature = None
        self.run_worker(self._refresh_sidebar(), group="sidebar-metrics", exclusive=True)
        self.query_one("#response").display = True
        self.query_one("#activity", Static).update("Ready")
        if self.request_activity_id:
            self._update_activity(
                self.request_activity_id, "Generate response", ActivityStatus.COMPLETED, 100
            )
        if self.architect_mode and self.stream:
            architecture = self.query_one("#architecture-preview", RichLog)
            architecture.clear()
            architecture.write(RichMarkdown(self.stream))
            self.query_one("#architecture-actions").display = True
            self.query_one("#preview", TabbedContent).active = "architecture-tab"

    def render_step(self, step: AgentStep, task_id: str | None = None) -> None:
        if not self._event_is_current(task_id):
            return
        name = step.tool_name.replace("_", " ")
        activity_id = f"tool-{self.request_number}-{step.number}"
        if str(step.status) == "running":
            self.query_one("#activity", Static).update(f"⚙ Running {name}…")
            self._update_activity(activity_id, name, ActivityStatus.RUNNING, 15)
            if activity_id not in self.task_item_ids:
                self.task_item_ids[activity_id] = len(self.task_items)
                self.task_items.append((name, "running"))
            else:
                self.task_items[self.task_item_ids[activity_id]] = (name, "running")
            self._render_tasks()
            path = str(step.arguments.get("path", step.arguments.get("query", "—")))
            self._update_assistant_status(
                "Working on the repository…",
                current_tool=f"{step.tool_name}()",
                current_file=path,
                progress=min(90, 20 + self.action_count * 12),
            )
            self._inspect_tool(step)
            return
        self.action_count += 1
        marker = "✓" if str(step.status) == "succeeded" else "✖"
        self.query_one("#activity", Static).update(f"{marker} {name}")
        color = "green" if marker == "✓" else "red"
        del color
        final_status = ActivityStatus.COMPLETED if marker == "✓" else ActivityStatus.FAILED
        self._update_activity(activity_id, name, final_status, 100)
        index = self.task_item_ids.get(activity_id)
        if index is not None:
            self.task_items[index] = (name, "done" if marker == "✓" else "failed")
        self._render_tasks()
        self._inspect_tool(step)
        if final_status == ActivityStatus.COMPLETED and step.tool_name in {
            "write_file",
            "edit_file",
            "delete_file",
        }:
            raw_path = step.metadata.get("path") or step.arguments.get("path")
            changed_path = Path(str(raw_path))
            try:
                label = changed_path.resolve().relative_to(self.root).as_posix()
            except ValueError:
                label = changed_path.as_posix()
            change = (
                "D"
                if step.tool_name == "delete_file"
                else "A"
                if bool(step.metadata.get("created"))
                else "M"
            )
            self.ui_state.changed_files[label] = change
            self._render_changed_files()
        if step.tool_name == "run_tests" and final_status in {
            ActivityStatus.COMPLETED,
            ActivityStatus.FAILED,
        }:
            marker = "✓" if final_status == ActivityStatus.COMPLETED else "✖"
            self.ui_state.test_result = f"{marker} {redact_secrets(step.result[-2000:])}"
        if step.tool_name in {"read_file", "git_diff", "git_show"} and step.result:
            preview_path = Path(str(step.arguments.get("path", "")))
            if step.tool_name == "read_file" and is_sensitive_path(preview_path):
                self._show_protected_preview(preview_path)
                return
            lexer = "diff" if "diff" in step.tool_name else self._lexer(str(step.arguments.get("path", "")))
            target = "#diff-preview" if "diff" in step.tool_name else "#code-preview"
            tab = "diff-tab" if "diff" in step.tool_name else "preview-tab"
            preview = self.query_one(target, RichLog)
            preview.clear()
            preview.write(Syntax(step.result, lexer, line_numbers=True, word_wrap=True))
            self.query_one("#preview", TabbedContent).active = tab
            if step.tool_name == "read_file":
                self.query_one("#files-preview", RichLog).write(f"[green]✓[/] {preview_path}")

    def process_output(
        self,
        process_id: str,
        stream: str,
        text: str,
        progress: float | None,
        session_id: str | None = None,
    ) -> None:
        if session_id is not None and session_id != self.session_id:
            return
        preview = self.query_one("#terminal-log", RichLog)
        self.query_one("#preview", TabbedContent).active = "terminal-tab"
        rendered = Text.from_ansi(f"[{process_id}] {text}")
        if stream == "stderr":
            rendered.stylize("red")
        elif stream == "status":
            rendered.stylize("dim")
        preview.write(rendered)
        prefix = "stderr: " if stream == "stderr" else "stdout: "
        self.ui_state.terminal_output.append(prefix + text)
        self.ui_state.terminal_output = self.ui_state.terminal_output[-500:]
        self.query_one("#activity", Static).update(f"⚙ Process {process_id}  ·  {text[:60]}")
        progress_bar = self.query_one("#progress", ProgressBar)
        if progress is not None:
            progress_bar.display = True
            progress_bar.update(progress=progress)
            self._update_activity(
                f"process-{process_id}", f"Process {process_id}", ActivityStatus.RUNNING, int(progress)
            )
        elif stream == "status":
            progress_bar.display = False
            lowered = text.lower()
            status = (
                ActivityStatus.COMPLETED
                if "complete" in lowered
                else ActivityStatus.CANCELLED
                if "stopped" in lowered
                else ActivityStatus.FAILED
            )
            self._update_activity(f"process-{process_id}", f"Process {process_id}", status, 100)

    def update_process_clock(self) -> None:
        if not self.agent or not self.agent.registry.process_manager:
            return
        running = [
            item
            for item in self.agent.registry.process_manager.processes.values()
            if item.status == "running"
        ]
        if running:
            process = running[-1]
            elapsed = time.monotonic() - process.started_at
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = frames[self.spinner_index % len(frames)]
            self.spinner_index += 1
            self.query_one("#activity", Static).update(
                f"{frame} Process running  ·  {elapsed:.0f}s"
            )

    def actions_end(self, task_id: str | None = None) -> None:
        if not self._event_is_current(task_id):
            return
        self.query_one("#activity", Static).update(f"✓ Completed  ·  {self.action_count} actions")
        details: list[str] = []
        if self.ui_state.changed_files:
            changed = "\n".join(
                f"{status} {path}" for path, status in self.ui_state.changed_files.items()
            )
            details.append(f"### Changed files\n\n```text\n{changed}\n```")
        if self.ui_state.test_result:
            details.append(f"### Tests\n\n{self.ui_state.test_result}")
        if details and self.last_response:
            if self.ui_state.changed_files:
                non_tests = [
                    Path(path).stem
                    for path in self.ui_state.changed_files
                    if "test" not in path.casefold()
                ]
                subject = non_tests[0].replace("_", "-") if non_tests else "tests"
                kind = "feat" if non_tests else "test"
                details.append(f"### Suggested commit\n\n`{kind}: update {subject}`")
            self.last_response += "\n\n" + "\n\n".join(details)
            self.stream = self.last_response
            self.query_one("#response", Static).update(RichMarkdown(self.last_response))
        self.action_count = 0
        self.set_timer(2.0, self._set_ready)

    def _update_activity(
        self,
        activity_id: str,
        label: str,
        status: ActivityStatus,
        progress: int | None,
    ) -> None:
        previous = self.activities.get(activity_id)
        if previous and previous.label == label and previous.status == status and previous.progress == progress:
            return
        self.ui_state.upsert_activity(activity_id, label, status, progress)
        if status in {ActivityStatus.COMPLETED, ActivityStatus.FAILED, ActivityStatus.CANCELLED}:
            self.set_timer(3.0, lambda activity_id=activity_id: self._retire_activity(activity_id))
        self._render_activities()

    def _retire_activity(self, activity_id: str) -> None:
        self.ui_state.retire_activity(activity_id)
        self._render_activities()

    def _render_activities(self) -> None:
        try:
            log = self.query_one("#activity-log", RichLog)
        except NoMatches:
            return
        log.clear()
        active = [item for item in self.activities.values() if item.finished_at is None]
        if active:
            log.write("[b $primary]CURRENT ACTIVITY[/]")
            for item in active:
                marker = self.symbols.pending if item.status == ActivityStatus.PENDING else self.symbols.gear
                progress = f"  [dim]{item.progress}%[/]" if item.progress is not None else ""
                log.write(f"[yellow]{marker}[/] {item.label}{progress}")
        if self.activity_history:
            log.write("[b $primary]HISTORY[/]")
            for item in self.activity_history[-20:]:
                marker = {
                    ActivityStatus.COMPLETED: "[green]✓[/]",
                    ActivityStatus.FAILED: "[red]✖[/]",
                    ActivityStatus.CANCELLED: "[yellow]■[/]",
                }.get(item.status, "[dim]·[/]")
                duration = (item.duration_ms or 0) / 1000
                log.write(f"{marker} {item.label} {item.status.value.lower()} ({duration:.1f}s)")

    def _inspect_tool(self, step: AgentStep) -> None:
        inspector = self.query_one("#tool-inspector", RichLog)
        status = str(step.status)
        marker = "⚙" if status == "running" else ("✓" if status == "succeeded" else "✖")
        inspector.write(f"\n[b]{marker} {step.tool_name}()[/]")
        inspector.write("[dim]Arguments[/]")
        safe_arguments = redact_secrets(json.dumps(step.arguments, indent=2, default=str))
        inspector.write(Syntax(safe_arguments, "json"))
        if status != "running":
            summary = step.result[:4000] if step.result else (step.error or "No output")
            summary = redact_secrets(summary)
            inspector.write(f"[dim]Result · {step.duration_ms:.0f} ms[/]")
            inspector.write(Text(summary))

    def _render_changed_files(self) -> None:
        preview = self.query_one("#files-preview", RichLog)
        preview.clear()
        for path, status in self.ui_state.changed_files.items():
            style = "red" if status == "D" else "green" if status == "A" else "yellow"
            preview.write(f"[{style}]{status}[/] {path}")

    def _update_assistant_status(
        self,
        task: str,
        *,
        current_tool: str = "Preparing…",
        current_file: str = "—",
        progress: int = 0,
    ) -> None:
        if self.stream:
            return
        response = self.query_one("#response", Static)
        response.display = True
        response.update(
            f"[b]Current Task[/]\n{task}\n\n"
            f"[b]Current Tool[/]  {current_tool}\n"
            f"[b]Current File[/]  {current_file}\n\n"
            f"[dim]Activity progress[/]  {progress}%"
        )
        bar = self.query_one("#progress", ProgressBar)
        bar.display = True
        bar.update(progress=progress)

    def _refresh_statusbar(self) -> None:
        elapsed = int(time.monotonic() - self.session_started)
        running = 0
        if self.agent and self.agent.registry.process_manager:
            running = sum(
                process.status == "running"
                for process in self.agent.registry.process_manager.processes.values()
            )
        active = [item for item in self.activities.values() if item.finished_at is None]
        state = (
            f"Error: {self.ui_state.errors[-1]}"
            if self.ui_state.errors
            else active[-1].label
            if active
            else self.ui_state.status_message
            if self.ui_state.status_message
            else "Ready"
        )
        task_label = "task" if len(active) == 1 else "tasks"
        cost = f"${self.ui_state.cost:.3f}" if self.ui_state.cost is not None else "Cost —"
        separator = f" {self.symbols.tree_pipe} "
        try:
            self.query_one("#statusbar", Static).update(
                separator.join(
                    (
                        state,
                        f"{len(active)} {task_label}",
                        f"{running} processes",
                        f"Context {self.context_percent}%",
                        cost,
                        f"{elapsed // 60:02d}:{elapsed % 60:02d}",
                    )
                )
            )
        except NoMatches:
            return

    def _render_tasks(self) -> None:
        markers = {
            "running": "[yellow]□[/]",
            "done": "[green]✓[/]",
            "failed": "[red]✖[/]",
        }
        goal = self.ui_state.current_task
        lines = [f"[b $accent]CURRENT GOAL[/]\n{goal}", "[b $primary]PLAN[/]"]
        lines.extend(f"{markers[state]} {name}" for name, state in self.task_items[-5:])
        self.query_one("#tasks", Static).update("\n".join(lines) if self.task_items else "")

    def _show_protected_preview(self, path: Path) -> None:
        self.protected_preview_path = path
        self.sensitive_preview_revealed = False
        preview = self.query_one("#code-preview", RichLog)
        preview.clear()
        preview.write(
            f"[b $warning]Sensitive preview hidden[/]\n"
            f"[dim]{path.name} is protected to prevent credential exposure.\n"
            "Press R to reveal a redacted preview temporarily.[/]"
        )
        self.query_one("#preview", TabbedContent).active = "preview-tab"
        self.notify("Sensitive file preview is protected", severity="warning")

    def action_reveal_sensitive(self) -> None:
        if self.protected_preview_path is not None and not self.sensitive_preview_revealed:
            self.confirm_sensitive_reveal()

    @work(exclusive=True, group="sensitive-preview")
    async def confirm_sensitive_reveal(self) -> None:
        path = self.protected_preview_path
        if path is None:
            return
        decision = await self.push_screen_wait(
            PermissionScreen(f"Reveal redacted sensitive preview\n{path.name}")
        )
        if decision not in {"once", "session"} or path != self.protected_preview_path:
            return
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.notify("Sensitive file cannot be previewed as text", severity="error")
            return
        preview = self.query_one("#code-preview", RichLog)
        preview.clear()
        preview.write(
            Syntax(redact_secrets(content[:100_000]), self._lexer(str(path)), line_numbers=True)
        )
        self.sensitive_preview_revealed = True

    @on(TabbedContent.TabActivated, "#preview")
    def clear_sensitive_preview_on_tab_change(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id != "preview-tab" and self.sensitive_preview_revealed:
            self._clear_sensitive_preview_state(clear_widget=True)

    def _clear_sensitive_preview_state(self, clear_widget: bool = False) -> None:
        self.protected_preview_path = None
        self.sensitive_preview_revealed = False
        if clear_widget:
            self.query_one("#code-preview", RichLog).clear()

    def _set_ready(self) -> None:
        if not (
            self.agent
            and self.agent.registry.process_manager
            and any(item.status == "running" for item in self.agent.registry.process_manager.processes.values())
        ):
            self.query_one("#activity", Static).update("Ready")

    @staticmethod
    def _lexer(path: str) -> str:
        suffix = Path(path).suffix.lower()
        return {".py": "python", ".ts": "typescript", ".js": "javascript", ".java": "java", ".kt": "kotlin", ".go": "go", ".rs": "rust", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".sh": "bash"}.get(suffix, "text")

    def handle_command(self, command: str) -> None:
        parts = command.split()
        if parts[0] in {"/", "/commands", "/help"}:
            self.push_screen(CommandsScreen(), self.execute_command_center_action)
        elif parts[0] in {"/config", "/setup"}:
            self.push_screen(
                SettingsScreen(
                    self.settings.llm.provider,
                    self.settings.llm.model,
                    self.settings.permissions.mode,
                ),
                self.save_settings,
            )
        elif parts[0] == "/models":
            preview = self.query_one("#code-preview", RichLog)
            self.query_one("#preview", TabbedContent).active = "preview-tab"
            preview.clear()
            preview.write(f"[b $primary]MODELS · {self.settings.llm.provider}[/]")
            for model in FALLBACK_MODELS[self.settings.llm.provider]:
                preview.write(f"  ◇ {model}")
        elif parts[0] in {"/architect", "/plan"}:
            objective = command.removeprefix(parts[0]).strip()
            if not objective:
                self.notify("Usage: /architect OBJECTIVE", severity="warning")
                return
            self.architect_mode = True
            self.architect_objective = objective
            if self.agent:
                self.architect_previous_permission_mode = self.agent.permissions.mode
                self.agent.permissions.mode = "plan"
            architecture = self.query_one("#architecture-preview", RichLog)
            architecture.clear()
            architecture.write(f"[yellow]●[/] Analyzing architecture for: {objective}")
            self.query_one("#architecture-actions").display = False
            self.query_one("#preview", TabbedContent).active = "architecture-tab"
            self.run_agent(
                "PLAN MODE — ANALYSIS ONLY. Do not modify files or execute commands. Inspect relevant "
                "repository files and produce: Objective, numbered Plan, Risk (Low/Medium/High), Files "
                f"expected, Current Architecture, and Proposed Architecture. Objective: {objective}"
            )
        elif parts[0] == "/status":
            self.run_agent("Use git_status and report the repository status. Do not modify files.")
        elif parts[0] == "/diff":
            self.run_agent("Use git_diff and summarize the current changes. Do not modify files.")
        elif parts[0] == "/review":
            self.run_agent(
                "Review current changes for bugs, security, regressions, missing tests, breaking "
                "changes, performance, dead code, secrets, and concurrency errors. Do not modify files."
            )
        elif parts[0] == "/commit":
            self.run_agent(
                "The user explicitly requested a Git commit. Inspect status and diff, run relevant "
                "tests, choose a conventional commit message, and execute git commit after approval. "
                "Never push."
            )
        elif parts[0] == "/branch":
            self.run_agent("Use git_branches and report the current and available branches.")
        elif parts[0] == "/processes":
            preview = self.query_one("#terminal-log", RichLog)
            self.query_one("#preview", TabbedContent).active = "terminal-tab"
            preview.clear()
            preview.write("[b $primary]PROCESSES[/]")
            manager = self.agent.registry.process_manager if self.agent else None
            if not manager or not manager.processes:
                preview.write("No processes")
            else:
                for process in manager.processes.values():
                    elapsed = time.monotonic() - process.started_at
                    preview.write(
                        f"{process.id}  {process.status}  {elapsed:.0f}s\n  {process.command}"
                    )
        elif parts[0] == "/session":
            self.notify(f"Session: {self.session_id or 'not started'}")
        elif parts[0] == "/rules":
            preview = self.query_one("#code-preview", RichLog)
            preview.clear()
            files = instruction_files(self.root)
            preview.write("[b $primary]PROJECT RULES[/]")
            for path in files:
                preview.write(path.relative_to(self.root).as_posix())
            self.query_one("#preview", TabbedContent).active = "preview-tab"
        elif parts[0] == "/memory":
            memory = self.root / ".nexus" / "memory.md"
            preview = self.query_one("#code-preview", RichLog)
            preview.clear()
            content = memory.read_text(encoding="utf-8") if memory.exists() else "No project memory yet."
            preview.write(RichMarkdown(content))
            self.query_one("#preview", TabbedContent).active = "preview-tab"
        elif parts[0] == "/remember":
            value = command.removeprefix("/remember").strip()
            if not value:
                self.notify("Usage: /remember TEXT", severity="warning")
                return
            memory = self.root / ".nexus" / "memory.md"
            memory.parent.mkdir(exist_ok=True)
            with memory.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"- {redact_secrets(value)}\n")
            if self.agent and self.agent.messages:
                self.agent.messages[0].content += f"\n\nProject memory: {redact_secrets(value)}"
            self.notify("Project memory updated")
        elif parts[0] == "/new":
            self.start_new_session()
        elif parts[0] == "/delete-session":
            self.delete_current_session()
        elif parts[0] == "/theme" and len(parts) > 1:
            if self.get_theme(parts[1]):
                self.theme = parts[1]
                path = self.root / ".nexus" / "theme"
                path.parent.mkdir(exist_ok=True)
                path.write_text(parts[1], encoding="utf-8")
                self.notify(f"Theme: {parts[1]}")
            else:
                self.notify("Unknown theme", severity="error")
        elif parts[0] in {"/exit", "/quit"}:
            self.exit()
        elif parts[0] == "/clear":
            self.action_clear()
        elif parts[0] == "/workspace" and len(parts) > 1:
            self.switch_workspace(parts[1])
        else:
            self.notify("Use /workspace NAME, /theme NAME, /clear, or /exit", severity="warning")

    @on(Button.Pressed, "#apply-architecture")
    def apply_architecture(self) -> None:
        if not self.architect_mode or not self.architect_objective:
            return
        objective = self.architect_objective
        self.architect_mode = False
        if self.agent and self.architect_previous_permission_mode:
            self.agent.permissions.mode = self.architect_previous_permission_mode
        self.architect_previous_permission_mode = None
        self.query_one("#architecture-actions").display = False
        self.run_agent(
            "The user approved the proposed architecture. Implement it carefully, request normal "
            f"permissions, run relevant tests, and summarize changes. Objective: {objective}"
        )

    @on(Button.Pressed, "#discard-architecture")
    def discard_architecture(self) -> None:
        self.architect_mode = False
        self.architect_objective = ""
        if self.agent and self.architect_previous_permission_mode:
            self.agent.permissions.mode = self.architect_previous_permission_mode
        self.architect_previous_permission_mode = None
        self.query_one("#architecture-actions").display = False
        self.notify("Architecture proposal discarded")

    @on(Button.Pressed, "#edit-architecture")
    def edit_architecture(self) -> None:
        objective = self.architect_objective
        self.discard_architecture()
        prompt = self.query_one("#prompt", PromptArea)
        prompt.load_text(f"/plan {objective}")
        prompt.focus()

    def save_settings(self, values: dict[str, str] | None) -> None:
        if values:
            self.apply_settings(values)

    @work(exclusive=True, group="settings")
    async def apply_settings(self, values: dict[str, str]) -> None:
        try:
            if self.agent and self.agent.registry.process_manager:
                await self.agent.registry.process_manager.stop_all()
            save_provider_configuration(
                self.root,
                values["provider"],
                values["model"],
                values["credential"],
                values["permission"],
            )
            self.settings = load_settings(self.root)
            self.agent = build_agent(
                self.root,
                confirm=self.confirm_action,
                on_step=self.render_step,
                on_stream_start=self.stream_start,
                on_token=self.stream_token,
                on_stream_end=self.stream_end,
                on_actions_end=self.actions_end,
                workspace_roots=self._workspace_roots(),
            )
            self.session_id = self.agent.session_id
            if self.agent.registry.process_manager:
                self._attach_process_manager(self.agent)
            self.query_one("#model", Static).update(self.settings.llm.model)
            self._sidebar_signature = None
            await self._refresh_sidebar()
            self.notify("Configuration saved", severity="information")
        except Exception as error:
            self._show_error("Configuration failed", error)

    @work(exclusive=True, group="workspace")
    async def switch_workspace(self, name: str) -> None:
        if not self.workspace:
            self.notify("No .code-workspace is open", severity="warning")
            return
        try:
            folder = self.workspace.folder(name)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        if self.agent and self.agent.registry.process_manager:
            await self.agent.registry.process_manager.stop_all()
        self.root = folder.path
        self.settings = load_settings(self.root)
        self.session_id = None
        self.agent = build_agent(
            self.root,
            confirm=self.confirm_action,
            on_step=self.render_step,
            on_stream_start=self.stream_start,
            on_token=self.stream_token,
            on_stream_end=self.stream_end,
            on_actions_end=self.actions_end,
            workspace_roots=self._workspace_roots(),
        )
        self.session_id = self.agent.session_id
        if self.agent.registry.process_manager:
            self._attach_process_manager(self.agent)
        state = self.workspace.path.parent / ".nexus" / "workspace-active"
        state.parent.mkdir(exist_ok=True)
        state.write_text(folder.name, encoding="utf-8")
        self.query_one("#model", Static).update(self.settings.llm.model)
        await self._refresh_sidebar()
        self._build_project_tree()
        self.notify(f"Active project: {folder.name}")

    def action_run_prompt(self) -> None:
        if self.last_prompt:
            self.run_agent(self.last_prompt)
        else:
            self.query_one("#prompt", PromptArea).focus()

    def action_commands(self) -> None:
        self.push_screen(CommandsScreen(), self.execute_command_center_action)

    def execute_command_center_action(self, action: str | None) -> None:
        if action is None:
            return
        if action in {"change-model", "change-provider", "change-permissions"}:
            self.handle_command("/config")
        elif action == "open-file":
            self.action_quick_open()
        elif action == "run-tests":
            self.action_tests()
        elif action == "review-changes":
            self.run_agent("Review current changes for defects, risks, and missing tests.")
        elif action == "show-diff":
            self.query_one("#preview", TabbedContent).active = "diff-tab"
        elif action == "show-architecture":
            prompt = self.query_one("#prompt", PromptArea)
            prompt.load_text("/architect ")
            prompt.focus()
        elif action == "clear-conversation":
            self.action_clear()
        elif action == "cancel-task":
            self.action_cancel()
        elif action == "new-session":
            self.start_new_session()
        elif action == "resume-session":
            self.notify(f"Session: {self.session_id or 'not started'}")
        elif action == "exit":
            self.exit()

    def action_quick_open(self) -> None:
        roots = self._workspace_roots() or {self.root.name: self.root}
        self.push_screen(QuickOpenScreen(roots), self.open_quick_file)

    def open_quick_file(self, path: Path | None) -> None:
        if path is None:
            return
        if is_sensitive_path(path):
            self._show_protected_preview(path)
            return
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.notify("File cannot be previewed as text", severity="warning")
            return
        preview = self.query_one("#code-preview", RichLog)
        preview.clear()
        preview.write(Syntax(content[:200_000], self._lexer(str(path)), line_numbers=True, word_wrap=True))
        self.ui_state.selected_file = path.relative_to(self.root).as_posix()
        self.query_one("#preview", TabbedContent).active = "preview-tab"

    def action_tests(self) -> None:
        self.run_agent("Run the relevant test suite and summarize failures.")

    def _attach_process_manager(self, agent: AgentLoop) -> None:
        manager = agent.registry.process_manager
        if manager is None:
            return
        session_id = agent.session_id
        manager.output_callback = (
            lambda process_id, stream, text, progress: self.process_output(
                process_id, stream, text, progress, session_id
            )
        )

    def action_clear(self) -> None:
        self.query_one("#history", RichLog).clear()
        self.query_one("#response", Static).update("")
        self.query_one("#response").display = False
        self.query_one("#empty-state").display = False
        self.last_response = ""
        self.stream = ""
        self.activity_history.clear()
        self.ui_state.changed_files.clear()
        self.ui_state.test_result = None
        self._render_activities()
        self.ui_state.status_message = "Ready"

    async def on_unmount(self) -> None:
        if self.agent and self.agent.registry.process_manager:
            await self.agent.registry.process_manager.stop_all()

    def action_cancel(self) -> None:
        if self.active_task_id is None and not any(
            item.finished_at is None for item in self.activities.values()
        ):
            self.query_one("#prompt", PromptArea).focus()
            return
        self.task_cancelled = True
        self.workers.cancel_group(self, "agent")
        for item in list(self.activities.values()):
            if item.finished_at is None:
                self._update_activity(
                    item.activity_id, item.label, ActivityStatus.CANCELLED, item.progress
                )
        self.active_task_id = None
        self.query_one("#progress", ProgressBar).display = False
        self.ui_state.status_message = "Cancelled"
        if self.agent and self.agent.registry.process_manager:
            self.run_worker(
                self.agent.registry.process_manager.stop_all(),
                group="cancel-processes",
                exclusive=True,
            )

    @work(exclusive=True, group="session-change")
    async def start_new_session(self) -> None:
        if self.agent and self.agent.registry.process_manager:
            await self.agent.registry.process_manager.stop_all()
        self.action_cancel()
        self.action_clear()
        self.agent = build_agent(
            self.root,
            confirm=self.confirm_action,
            on_step=self.render_step,
            on_stream_start=self.stream_start,
            on_token=self.stream_token,
            on_stream_end=self.stream_end,
            on_actions_end=self.actions_end,
            workspace_roots=self._workspace_roots(),
        )
        self.session_id = self.agent.session_id
        self.ui_state.session_id = self.session_id
        self._attach_process_manager(self.agent)
        self.ui_state.status_message = "New session"
        self._sidebar_signature = None
        await self._refresh_sidebar()

    @work(exclusive=True, group="session-delete")
    async def delete_current_session(self) -> None:
        session_id = self.session_id
        if not session_id:
            self.notify("No active session", severity="warning")
            return
        decision = await self.push_screen_wait(
            PermissionScreen(f"Delete session\n{session_id}\nConversation history will be removed.")
        )
        if decision not in {"once", "session"}:
            return
        if SessionDatabase().delete_session(session_id):
            self.session_id = None
            self.start_new_session()
            self.notify("Session deleted")


def run_tui(
    root: Path, session_id: str | None = None, workspace_path: Path | None = None
) -> None:
    TheCodeApp(root, session_id, workspace_path).run()


