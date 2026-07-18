from io import StringIO

from rich.console import Console
from rich.theme import Theme
from typer.testing import CliRunner

from nexus.cli import cli
from nexus.ui import renderer


def test_render_help_includes_about_and_donate(monkeypatch) -> None:
    stream = StringIO()
    test_console = Console(
        file=stream,
        force_terminal=True,
        color_system=None,
        width=120,
        theme=Theme({"command": "cyan", "muted": "dim", "brand": "magenta"}),
    )
    monkeypatch.setattr(renderer, "console", test_console)

    renderer.render_help()

    output = stream.getvalue()
    assert "/about" in output
    assert "/donate" in output


def test_about_command_shows_author_and_donate_url(monkeypatch) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["about"], env={"THECODE_DONATE_URL": "https://example.com/support"})

    assert result.exit_code == 0
    assert "Inacio Sacataria" in result.stdout
    assert "https://example.com/support" in result.stdout


def test_donate_command_shows_support_hint_when_unset() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["donate"])

    assert result.exit_code == 0
    assert "THECODE_DONATE_URL" in result.stdout
