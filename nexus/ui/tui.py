from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

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
from nexus.config.wizard import FALLBACK_MODELS, save_provider_configuration
from nexus.permissions.manager import PermissionResponse
from nexus.repository.git import current_branch
from nexus.repository.instructions import instruction_files
from nexus.security.paths import is_sensitive_path, resolve_project_path
from nexus.tools.registry import ToolRegistry
from nexus.ui.themes import CUSTOM_THEMES
from nexus.workspace import CodeWorkspace


class ActivityStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class ActivityItem:
    activity_id: str
    label: str
    status: ActivityStatus
    progress: int
    started_at: float
    updated_at: float
    finished_at: float | None = None
    timestamp: str = ""


class PromptArea(TextArea):
    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def on_key(self, event: events.Key) -> None:
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
    ("/theme NAME", "Change the visual theme"),
    ("/workspace NAME", "Switch the active workspace folder"),
    ("/processes", "Show active and completed processes"),
    ("/session", "Show the active session identifier"),
    ("/clear", "Clear chat output"),
    ("/exit", "Close TheCode"),
)


class CommandsScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="commands-dialog"):
            yield Static("Command center", classes="section-title")
            yield Static("\n".join(f"[b $primary]{command:<20}[/] {description}" for command, description in COMMANDS), id="command-list")
            yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()


class SettingsScreen(ModalScreen[dict[str, str] | None]):
    def __init__(self, provider: str, model: str, permission_mode: str) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.permission_mode = permission_mode

    def compose(self) -> ComposeResult:
        providers = [(name.title(), name) for name in ("openrouter", "openai", "anthropic", "ollama")]
        permissions = [(name.title(), name) for name in ("safe", "ask", "auto")]
        with Vertical(id="settings-dialog"):
            yield Static("AI configuration", classes="section-title")
            yield Static("Provider", classes="field-label")
            yield Select(providers, value=self.provider, id="settings-provider")
            yield Static("API key (not required for Ollama)", classes="field-label")
            yield Input(password=True, placeholder="Paste a new key", id="settings-key")
            yield Static("Model identifier", classes="field-label")
            yield Input(value=self.model, id="settings-model")
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
                "model": self.query_one("#settings-model", Input).value,
                "permission": permission,
            }
        )

    @on(Select.Changed, "#settings-provider")
    def update_provider_defaults(self, event: Select.Changed) -> None:
        provider = str(event.value)
        if provider in FALLBACK_MODELS:
            model_input = self.query_one("#settings-model", Input)
            if model_input.value == self.model or not model_input.value.strip():
                model_input.value = FALLBACK_MODELS[provider][0]
            key_input = self.query_one("#settings-key", Input)
            key_input.placeholder = (
                "Not required for local Ollama" if provider == "ollama" else "Paste a new key or keep the existing key"
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
    BINDINGS = [
        ("ctrl+r", "run_prompt", "Run"),
        ("ctrl+t", "tests", "Tests"),
        ("ctrl+l", "clear", "Clear"),
        Binding("ctrl+k", "commands", "Commands", priority=True),
        Binding("ctrl+p", "quick_open", "Quick Open", priority=True),
        ("escape", "cancel", "Cancel"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self, root: Path, session_id: str | None = None, workspace_path: Path | None = None
    ) -> None:
        super().__init__()
        self.workspace = CodeWorkspace.load(workspace_path) if workspace_path else None
        self.root = self._initial_root(root)
        self.session_id = session_id
        self.settings = load_settings(root)
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
        self.first_token_ms: float | None = None
        self.output_tokens_estimate = 0
        self.input_tokens_estimate = 0
        self.context_percent = 0
        self.task_items: list[tuple[str, str]] = []
        self.activities: dict[str, ActivityItem] = {}
        self.activity_history: list[ActivityItem] = []
        self.request_number = 0
        self.request_activity_id: str | None = None
        self.architect_mode = False
        self.architect_objective = ""
        self.architect_previous_permission_mode: str | None = None
        self.suppress_command_suggestions = False
        self.indexed_file_count = 0

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
        with Horizontal(id="topbar"):
            yield Static("◆  THECODE\n   AI SOFTWARE ENGINEER", id="brand")
            yield Static(self.root.name, id="header-context")
            yield Static(self.settings.llm.model, id="model")
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Static("", id="sidebar-info")
                yield Static("", id="tasks")
                yield Tree("Project", id="project-tree")
            with Vertical(id="main"):
                yield RichLog(id="history", wrap=True, markup=True)
                yield Static("", id="response")
                yield RichLog(id="activity-log", wrap=True, markup=True)
                yield Static("Ready", id="activity")
                yield ProgressBar(total=100, show_eta=False, id="progress")
                yield OptionList(id="command-suggestions")
                yield PromptArea(placeholder="Ask anything · @file adds context · / opens commands", id="prompt")
            with TabbedContent("Preview", "Diff", "Files", "Architecture", "Terminal", "Logs", id="preview"):
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
                        yield Button("Apply architecture", id="apply-architecture", variant="success")
                with TabPane("Terminal", id="terminal-tab"):
                    yield RichLog(id="terminal-log", wrap=True, markup=True)
                with TabPane("Logs", id="logs-tab"):
                    yield RichLog(id="tool-inspector", wrap=True, markup=True)
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
                if agent.registry.process_manager:
                    agent.registry.process_manager.output_callback = self.process_output
            except Exception as error:
                self.startup_error = str(error)
                self._show_error("Agent unavailable", error)
        self.query_one("#prompt", PromptArea).focus()
        self.query_one("#response").display = False
        self.query_one("#response", Static).border_title = "Assistant"
        self._apply_responsive_layout(self.size.width)
        self.set_interval(1.0, self.update_process_clock)
        self.set_interval(1.0, self._refresh_statusbar)
        self.set_interval(2.0, self.refresh_repository_state)
        await self._refresh_sidebar()
        self._build_project_tree()

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
            folders = "[b $primary]WORKSPACE[/]\n\n" + "\n".join(rendered) + "\n\n"
        latency = f"{self.first_token_ms:.0f} ms first token" if self.first_token_ms is not None else "waiting"
        sidebar_markup = (
            f"{folders}[b $primary]PROJECT[/]\n\n◆ {self.root.name}\n⌁ {self.branch}\n\n"
            f"[b $primary]MODEL[/]\n\n◈ {self.settings.llm.provider}\n◇ {self.settings.llm.model}\n\n"
            f"[b $primary]SESSION[/]\n\n⚙ {tools} tools\n◉ {self.session_id or 'new'}\n\n"
            f"⌘ {rules} rules  ·  {skills} skills\n\n"
            f"[b $primary]STATUS[/]\n\n"
            f"{'[green]● Connected[/]' if self.agent else '[red]● Unavailable[/]' if self.startup_error else '[yellow]● Initializing[/]'}\n\n"
            f"[b $primary]INDEX[/]\n\n[green]✓[/] {self.indexed_file_count} files\n\n"
            f"[b $primary]USAGE[/]\n\n◴ Context {self.context_percent}%\n"
            f"◫ ~{self.input_tokens_estimate} in  ·  ~{self.output_tokens_estimate} out\n"
            "Cost unavailable\n"
            f"⚡ {latency}\n\n"
            "[b $primary]QUICK ACTIONS[/]\n\n"
            "⌘ Ctrl+K  Commands\n/config    AI setup\n/models    Model list"
        )
        # Keep repository navigation dominant; metadata remains available in a compact block.
        sidebar_markup = sidebar_markup.replace("\n\n", "\n")
        self.query_one("#sidebar-info", Static).update(sidebar_markup)
        self.query_one("#header-context", Static).update(f"{self.root.name}  ·  {self.branch}")

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
                try:
                    for child in sorted(item.iterdir(), key=lambda path: path.name.lower())[:40]:
                        if child.name not in {".git", "node_modules", "__pycache__"} and not is_sensitive_path(child):
                            node.add_leaf(child.name, data=child)
                except OSError:
                    pass
        tree.root.expand()

    @on(Tree.NodeSelected, "#project-tree")
    def preview_tree_file(self, event: Tree.NodeSelected[Path]) -> None:
        path = event.node.data
        if not isinstance(path, Path) or not path.is_file():
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
        self.query_one("#preview", TabbedContent).active = "preview-tab"

    async def refresh_repository_state(self) -> None:
        branch = await current_branch(self.root)
        if branch != self.branch:
            self._sidebar_signature = None
            await self._refresh_sidebar()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        try:
            self.query_one("#sidebar").display = width >= 100
            self.query_one("#preview").display = width >= 100
            self.query_one("#model").display = False
            bottom = self.query_one("#bottom", Static)
            if width < 100:
                bottom.update(
                    "[b $primary]ENTER[/] Send   [b $primary]CTRL+K[/] Commands   "
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
        self.last_prompt = prompt
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
        input_widget = self.query_one("#prompt", PromptArea)
        input_widget.disabled = True
        try:
            if self.agent is None:
                raise RuntimeError(self.startup_error or "Agent is not initialized")
            await self.agent.run(prompt)
        except Exception as error:
            self._show_error("Request failed", error)
        finally:
            input_widget.disabled = False
            input_widget.focus()

    def _show_error(self, title: str, error: Exception) -> None:
        message = str(error) or error.__class__.__name__
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

    def stream_start(self) -> None:
        self._archive_response()
        self.stream = ""
        self.last_response = ""
        self.request_started = time.monotonic()
        self.first_token_ms = None
        response = self.query_one("#response", Static)
        response.display = True
        response.update("Thinking…")
        self.query_one("#activity", Static).update("● Thinking…")
        self.request_number += 1
        self.request_activity_id = f"request-{self.request_number}"
        self._update_activity(
            self.request_activity_id, "Generate response", ActivityStatus.RUNNING, 10
        )
        self.task_items = []
        self._render_tasks()
        self._update_assistant_status("Analyzing request…", progress=10)

    def stream_token(self, token: str) -> None:
        if self.first_token_ms is None and self.request_started:
            self.first_token_ms = (time.monotonic() - self.request_started) * 1000
        self.stream += token
        if self.request_activity_id:
            progress = min(90, 10 + len(self.stream) // 80)
            self._update_activity(
                self.request_activity_id, "Generate response", ActivityStatus.RUNNING, progress
            )
        response = self.query_one("#response", Static)
        response.display = True
        response.update(RichMarkdown(self.stream))

    def stream_end(self) -> None:
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

    def render_step(self, step: AgentStep) -> None:
        name = step.tool_name.replace("_", " ")
        activity_id = f"tool-{self.request_number}-{step.number}"
        if str(step.status) == "running":
            self.query_one("#activity", Static).update(f"⚙ Running {name}…")
            self._update_activity(activity_id, name, ActivityStatus.RUNNING, 15)
            self.task_items.append((name, "running"))
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
        marker = "✔" if str(step.status) == "succeeded" else "✖"
        self.query_one("#activity", Static).update(f"{marker} {name}")
        color = "green" if marker == "✔" else "red"
        del color
        final_status = ActivityStatus.COMPLETED if marker == "✔" else ActivityStatus.FAILED
        self._update_activity(activity_id, name, final_status, 100)
        for index in range(len(self.task_items) - 1, -1, -1):
            if self.task_items[index] == (name, "running"):
                self.task_items[index] = (name, "done" if marker == "✔" else "failed")
                break
        self._render_tasks()
        self._inspect_tool(step)
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
        self, process_id: str, stream: str, text: str, progress: float | None
    ) -> None:
        preview = self.query_one("#terminal-log", RichLog)
        self.query_one("#preview", TabbedContent).active = "terminal-tab"
        rendered = Text.from_ansi(f"[{process_id}] {text}")
        if stream == "stderr":
            rendered.stylize("#FF6666")
        elif stream == "status":
            rendered.stylize("#AFAFAF")
        preview.write(rendered)
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
                f"{frame} Process {process.id}  ·  PID {process.process.pid}  ·  {elapsed:.0f}s"
            )

    def actions_end(self) -> None:
        self.query_one("#activity", Static).update(f"✔ Completed  ·  {self.action_count} actions")
        self.action_count = 0
        self.set_timer(2.0, self._set_ready)

    def _update_activity(
        self,
        activity_id: str,
        label: str,
        status: ActivityStatus,
        progress: int,
    ) -> None:
        now = time.monotonic()
        item = self.activities.get(activity_id)
        if item is None:
            item = ActivityItem(
                activity_id,
                label,
                status,
                max(0, min(100, progress)),
                now,
                now,
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )
            self.activities[activity_id] = item
        else:
            if (
                item.label == label
                and item.status == status
                and item.progress == max(0, min(100, progress))
            ):
                return
            item.label = label
            item.status = status
            item.progress = max(0, min(100, progress))
            item.updated_at = now
        if status in {ActivityStatus.COMPLETED, ActivityStatus.FAILED, ActivityStatus.CANCELLED}:
            item.finished_at = now
            if not any(event.activity_id == activity_id for event in self.activity_history):
                self.activity_history.append(item)
                self.activity_history = self.activity_history[-20:]
            self.set_timer(3.0, lambda activity_id=activity_id: self._retire_activity(activity_id))
        self._render_activities()

    def _retire_activity(self, activity_id: str) -> None:
        self.activities.pop(activity_id, None)
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
                marker = "□" if item.status == ActivityStatus.PENDING else "⚙"
                log.write(f"[yellow]{marker}[/] {item.label}  [dim]{item.progress}%[/]")
        if self.activity_history:
            log.write("[b $primary]HISTORY[/]")
            for item in self.activity_history[-20:]:
                marker = {
                    ActivityStatus.COMPLETED: "[green]✓[/]",
                    ActivityStatus.FAILED: "[red]✖[/]",
                    ActivityStatus.CANCELLED: "[yellow]■[/]",
                }.get(item.status, "[dim]·[/]")
                duration = (item.finished_at or item.updated_at) - item.started_at
                log.write(f"[dim]{item.timestamp}[/]  {marker} {item.label} {item.status.value.lower()} ({duration:.1f}s)")

    def _inspect_tool(self, step: AgentStep) -> None:
        inspector = self.query_one("#tool-inspector", RichLog)
        status = str(step.status)
        marker = "⚙" if status == "running" else ("✓" if status == "succeeded" else "✖")
        inspector.write(f"\n[b]{marker} {step.tool_name}()[/]")
        inspector.write("[dim]Arguments[/]")
        inspector.write(Syntax(json.dumps(step.arguments, indent=2, default=str), "json"))
        if status != "running":
            summary = step.result[:4000] if step.result else (step.error or "No output")
            inspector.write(f"[dim]Result · {step.duration_ms:.0f} ms[/]")
            inspector.write(Text(summary))

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
        elapsed = int(time.monotonic() - self.request_started) if self.request_started else 0
        running = 0
        if self.agent and self.agent.registry.process_manager:
            running = sum(
                process.status == "running"
                for process in self.agent.registry.process_manager.processes.values()
            )
        state = "Working" if self.request_started and not self.last_response else "Ready"
        try:
            self.query_one("#statusbar", Static).update(
                f"{state}   ⚙ {running} processes   Context {self.context_percent}%   "
                f"Cost —   {elapsed // 60:02d}:{elapsed % 60:02d} request"
            )
        except NoMatches:
            return

    def _render_tasks(self) -> None:
        markers = {
            "running": "[yellow]□[/]",
            "done": "[green]✓[/]",
            "failed": "[red]✖[/]",
        }
        lines = ["[b $primary]CURRENT TASK[/]"]
        lines.extend(f"{markers[state]} {name}" for name, state in self.task_items[-5:])
        self.query_one("#tasks", Static).update("\n".join(lines) if self.task_items else "")

    def _show_protected_preview(self, path: Path) -> None:
        preview = self.query_one("#code-preview", RichLog)
        preview.clear()
        preview.write(
            f"[b #FFC857]Sensitive preview hidden[/]\n"
            f"[dim]{path.name} is protected to prevent credential exposure.[/]"
        )
        self.query_one("#preview", TabbedContent).active = "preview-tab"
        self.notify("Sensitive file preview is protected", severity="warning")

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
            self.push_screen(CommandsScreen())
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
        elif parts[0] == "/architect":
            objective = command.removeprefix("/architect").strip()
            if not objective:
                self.notify("Usage: /architect OBJECTIVE", severity="warning")
                return
            self.architect_mode = True
            self.architect_objective = objective
            if self.agent:
                self.architect_previous_permission_mode = self.agent.permissions.mode
                self.agent.permissions.mode = "safe"
            architecture = self.query_one("#architecture-preview", RichLog)
            architecture.clear()
            architecture.write(f"[yellow]●[/] Analyzing architecture for: {objective}")
            self.query_one("#architecture-actions").display = False
            self.query_one("#preview", TabbedContent).active = "architecture-tab"
            self.run_agent(
                "ARCHITECT MODE — ANALYSIS ONLY. Do not modify files or execute destructive commands. "
                "Inspect the repository and produce these sections: Current Architecture, Proposed "
                "Architecture, ASCII dependency graph, Estimated Changes, Risks, and Implementation "
                f"Plan. Objective: {objective}"
            )
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
                        f"{process.id}  PID {process.process.pid}  {process.status}  {elapsed:.0f}s\n  {process.command}"
                    )
        elif parts[0] == "/session":
            self.notify(f"Session: {self.session_id or 'not started'}")
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
                self.agent.registry.process_manager.output_callback = self.process_output
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
            self.agent.registry.process_manager.output_callback = self.process_output
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
        self.push_screen(CommandsScreen())

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
        self.query_one("#preview", TabbedContent).active = "preview-tab"

    def action_tests(self) -> None:
        self.run_agent("Run the relevant test suite and summarize failures.")

    def action_clear(self) -> None:
        self.query_one("#history", RichLog).clear()
        self.query_one("#response", Static).update("")
        self.query_one("#response").display = False
        self.last_response = ""

    async def on_unmount(self) -> None:
        if self.agent and self.agent.registry.process_manager:
            await self.agent.registry.process_manager.stop_all()

    def action_cancel(self) -> None:
        self.workers.cancel_group(self, "agent")
        self.query_one("#activity", Static).update("Cancelled")
        for item in list(self.activities.values()):
            if item.finished_at is None:
                self._update_activity(
                    item.activity_id, item.label, ActivityStatus.CANCELLED, item.progress
                )


def run_tui(
    root: Path, session_id: str | None = None, workspace_path: Path | None = None
) -> None:
    TheCodeApp(root, session_id, workspace_path).run()
