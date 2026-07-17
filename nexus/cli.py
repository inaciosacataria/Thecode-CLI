from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sys
from pathlib import Path

import httpx
import typer
import yaml

from nexus.agent.planner import planning_request
from nexus.app import build_agent
from nexus.config.loader import load_settings
from nexus.config.wizard import configure_provider
from nexus.repository.git import current_branch
from nexus.repository.scanner import project_summary
from nexus.security.secrets import looks_like_secret
from nexus.sessions.database import SessionDatabase
from nexus.sessions.manager import SessionManager
from nexus.ui.console import console
from nexus.ui.prompts import confirm_prompt, user_input
from nexus.ui.renderer import banner, render_chat_hint, render_help, render_notice, sessions_table
from nexus.ui.themes import THEME_NAMES
from nexus.ui.tui import run_tui
from nexus.workspace import CodeWorkspace, discover_workspace

cli = typer.Typer(no_args_is_help=False, help="TheCode — secure AI coding agent")
workspace_cli = typer.Typer(help="Manage VS Code/Cursor compatible workspaces.")
cli.add_typer(workspace_cli, name="workspace")


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _execute(prompt: str, root: Path, read_only: bool = False) -> None:
    try:
        agent = build_agent(root, read_only=read_only)
        await agent.run(prompt)
    except (ValueError, httpx.HTTPError) as error:
        render_notice(str(error), kind="error", title="Provider unavailable")


def _print_config(root: Path) -> None:
    settings = load_settings(root)
    data = settings.model_dump(mode="json", exclude={"project_root"})
    if looks_like_secret(str(data["llm"]["model"])):
        data["llm"]["model"] = "[REDACTED: looks like an API key]"
    console.print(yaml.safe_dump(data, sort_keys=False))


async def _chat(root: Path, session_id: str | None = None) -> None:
    settings = load_settings(root)
    branch = await current_branch(root)
    banner(root, branch, settings.llm.provider, settings.llm.model, settings.permissions.mode)
    render_chat_hint()
    try:
        agent = build_agent(root, session_id=session_id)
    except ValueError as error:
        agent = None
        console.print(f"[yellow]Provider unavailable:[/yellow] {error}")
        if confirm_prompt("Configure a provider now?", default=True):
            try:
                provider, model = configure_provider(root)
                console.print(f"[green]Configured {provider}/{model}.[/green] Restarting agent.")
                settings = load_settings(root)
                agent = build_agent(root, session_id=session_id)
            except ValueError as setup_error:
                console.print(f"[red]Configuration failed:[/red] {setup_error}")
    while True:
        try:
            value = user_input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nSession ended.")
            return
        if not value:
            continue
        if value in {"/exit", "/quit"}:
            return
        if value == "/help":
            render_help()
            continue
        if value == "/clear":
            console.clear()
            banner(root, branch, settings.llm.provider, settings.llm.model, settings.permissions.mode)
            render_chat_hint()
            continue
        if value == "/status":
            await _execute("Use git_status and summarize it.", root, True)
            continue
        if value == "/diff":
            await _execute("Use git_diff and summarize the changes.", root, True)
            continue
        if value == "/config":
            _print_config(root)
            continue
        if value == "/model":
            console.print(settings.llm.model)
            continue
        if value == "/provider":
            console.print(f"Current provider: {settings.llm.provider}")
            if confirm_prompt("Change provider?", default=False):
                try:
                    provider, model = configure_provider(root)
                    settings = load_settings(root)
                    agent = build_agent(root, session_id=session_id)
                    console.print(f"[green]Active provider: {provider}/{model}[/green]")
                except ValueError as error:
                    console.print(f"[red]Configuration failed:[/red] {error}")
            continue
        if value == "/permissions":
            console.print(settings.permissions.model_dump())
            continue
        if value == "/context":
            console.print(settings.context.model_dump())
            continue
        if value == "/session":
            console.print(f"Session: {agent.session_id if agent else 'not started'}")
            continue
        if value == "/compact":
            console.print("Context is rebuilt selectively on every request.")
            continue
        if value == "/undo":
            console.print("Use [command]thecode undo <session-id>[/command] for a persisted operation.")
            continue
        if value.startswith("/"):
            render_notice(
                f"Unknown command: {value.split(maxsplit=1)[0]}\nUse /help to see available commands.",
                kind="warning",
                title="Unknown command",
            )
            continue
        if agent is None:
            console.print(
                "[yellow]Configure the provider credentials, then restart the chat.[/yellow]"
            )
            continue
        try:
            await agent.run(value)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                console.print(
                    "[red]Provider authentication failed (401).[/red] Revoke any exposed key, "
                    "then run [command]thecode config --setup[/command] with a new API key."
                )
            else:
                console.print(f"[red]Provider request failed:[/red] {error}")
        except httpx.RequestError as error:
            console.print(f"[red]Provider connection failed:[/red] {error}")


@cli.callback(invoke_without_command=True)
def entry(ctx: typer.Context) -> None:
    """Start an interactive TheCode session when no command is provided."""
    if ctx.invoked_subcommand is None:
        root = Path.cwd().resolve()
        run_tui(root, workspace_path=discover_workspace(root))


@cli.command()
def chat(
    classic: bool = typer.Option(False, "--classic", help="Use the classic Rich interface."),
) -> None:
    """Start an interactive coding session."""
    if classic:
        _run(_chat(Path.cwd().resolve()))
    else:
        root = Path.cwd().resolve()
        run_tui(root, workspace_path=discover_workspace(root))


@workspace_cli.command("open")
def workspace_open(path: Path) -> None:
    """Open a .code-workspace file in the TheCode TUI."""
    workspace = CodeWorkspace.load(path)
    run_tui(workspace.folders[0].path, workspace_path=workspace.path)


@workspace_cli.command("list")
def workspace_list(path: Path | None = None) -> None:
    """List folders in a workspace."""
    workspace_path = path or discover_workspace(Path.cwd())
    if workspace_path is None:
        raise typer.BadParameter("No unique .code-workspace file found")
    workspace = CodeWorkspace.load(workspace_path)
    for folder in workspace.folders:
        console.print(f"[brand]◆[/brand] {folder.name}  [muted]{folder.path}[/muted]")


@workspace_cli.command("add")
def workspace_add(folder: Path, path: Path | None = None, name: str | None = None) -> None:
    """Add a folder to a workspace."""
    workspace_path = path or discover_workspace(Path.cwd())
    if workspace_path is None:
        raise typer.BadParameter("No unique .code-workspace file found; pass --path")
    workspace = CodeWorkspace.load(workspace_path)
    workspace.add_folder(folder, name)
    console.print(f"[green]Added {folder.resolve()} to {workspace.path.name}.[/green]")


@cli.command()
def theme(name: str | None = None) -> None:
    """Show or set the terminal theme."""
    root = Path.cwd().resolve()
    path = root / ".nexus" / "theme"
    if name is None:
        active = path.read_text(encoding="utf-8").strip() if path.exists() else "nexus-aurora"
        console.print(f"Active theme: [brand]{active}[/brand]\nAvailable: {', '.join(THEME_NAMES)}")
        return
    if name not in THEME_NAMES:
        raise typer.BadParameter(f"Unknown theme. Choose: {', '.join(THEME_NAMES)}")
    path.parent.mkdir(exist_ok=True)
    path.write_text(name, encoding="utf-8")
    console.print(f"[green]Theme set to {name}.[/green]")


@cli.command("run")
def run_task(prompt: str) -> None:
    """Execute an agent task with permission checks."""
    _run(_execute(prompt, Path.cwd().resolve()))


@cli.command()
def ask(prompt: str) -> None:
    """Answer a repository question without modifying it."""
    _run(_execute(prompt, Path.cwd().resolve(), True))


@cli.command()
def plan(prompt: str) -> None:
    """Analyze the repository and produce an implementation plan."""
    _run(_execute(planning_request(prompt), Path.cwd().resolve(), True))


@cli.command()
def review() -> None:
    """Review current Git changes for defects and risks."""
    prompt = "Review the current git diff. Identify bugs, security issues, missing tests, breaking changes, dead code, and performance problems. Do not modify files."
    _run(_execute(prompt, Path.cwd().resolve(), True))


@cli.command()
def config(
    setup: bool = typer.Option(False, "--setup", help="Choose a provider, model, and credential."),
) -> None:
    """Show effective configuration, excluding secrets."""
    root = Path.cwd().resolve()
    if setup:
        provider, model = configure_provider(root)
        console.print(f"[green]Configured {provider}/{model}.[/green]")
        return
    _print_config(root)


@cli.command()
def models() -> None:
    """Show the active provider and model."""
    settings = load_settings()
    console.print(f"Provider: {settings.llm.provider}\nModel: {settings.llm.model}")


@cli.command("init")
def initialize() -> None:
    """Initialize TheCode configuration and project instructions."""
    root = Path.cwd().resolve()
    nexus_dir = root / ".nexus"
    config_path = nexus_dir / "config.yaml"
    instructions = root / "NEXUS.md"
    if config_path.exists() or instructions.exists():
        console.print("[yellow]Existing TheCode files were not overwritten.[/yellow]")
        raise typer.Exit(1)
    summary = project_summary(root)
    nexus_dir.mkdir(exist_ok=True)
    config_path.write_text(yaml.safe_dump({"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}, "agent": {"max_steps": 30}, "permissions": {"mode": "ask"}, "context": {"max_characters": 120000}, "project": {"ignore": ["node_modules", "dist", "build", ".git", "vendor"]}}, sort_keys=False), encoding="utf-8")
    instructions.write_text(f"# TheCode instructions\n\nProject: {root.name}\n\n## Detected project\n\n```yaml\n{yaml.safe_dump(summary, sort_keys=False)}```\n\n## Conventions\n\nDocument architecture, coding conventions, test commands, and restrictions here.\n", encoding="utf-8")
    console.print("[green]Created .nexus/config.yaml and NEXUS.md[/green]")


@cli.command()
def doctor() -> None:
    """Validate the local TheCode environment."""
    root = Path.cwd().resolve()
    settings = load_settings(root)
    checks = [
        (sys.version_info >= (3, 12), f"Python {platform.python_version()}"),
        (shutil.which("git") is not None, "Git available"),
        (shutil.which("rg") is not None, "Ripgrep available"),
        (os.access(root, os.R_OK | os.W_OK), "Project directory accessible"),
        (
            (root / ".nexus" / "config.yaml").exists(),
            "Project configuration found"
            if (root / ".nexus" / "config.yaml").exists()
            else "No project-level configuration found",
        ),
        (settings.llm.provider == "ollama" or bool(os.getenv({"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(settings.llm.provider, ""))), f"{settings.llm.provider} credentials configured"),
    ]
    try:
        SessionDatabase().connection.execute("SELECT 1")
        checks.append((True, "Session database writable"))
    except OSError as error:
        checks.append((False, f"Session database: {error}"))
    console.print("[brand]TheCode Doctor[/brand]\n")
    for passed, message in checks:
        marker = "[success]✓[/success]" if passed else "[warning]![/warning]"
        console.print(f"{marker} {message}")


@cli.command()
def sessions() -> None:
    """List saved sessions."""
    console.print(sessions_table(SessionDatabase().list_sessions()))


@cli.command()
def resume(session_id: str) -> None:
    """Resume a saved session after validating its project."""
    session = SessionDatabase().get_session(session_id)
    if not session:
        raise typer.BadParameter("Unknown session")
    root = Path(session.project_dir)
    if not root.is_dir():
        raise typer.BadParameter("Session project no longer exists")
    if str(root.resolve()) != str(Path.cwd().resolve()):
        console.print(f"[yellow]Session belongs to {root}; change to that directory first.[/yellow]")
        raise typer.Exit(1)
    run_tui(root, session_id)


@cli.command("delete-session")
def delete_session(session_id: str) -> None:
    """Delete local history for a session."""
    if not SessionDatabase().delete_session(session_id):
        raise typer.BadParameter("Unknown session")
    console.print("[green]Session deleted.[/green]")


@cli.command()
def undo(session_id: str) -> None:
    """Undo the last recorded agent change in a session."""
    manager = SessionManager(SessionDatabase(), load_settings())
    console.print(manager.undo(session_id))


def main() -> None:
    cli()
