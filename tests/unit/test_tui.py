from pathlib import Path

import pytest

from nexus.agent.loop import AgentLoop
from nexus.agent.state import AgentStep, StepStatus
from nexus.llm.base import LLMProvider, LLMResponse, Message
from nexus.permissions.manager import PermissionManager
from nexus.tools.registry import ToolRegistry
from nexus.ui.tui import ActivityStatus, CommandsScreen, QuickOpenScreen, TheCodeApp


class FailingProvider(LLMProvider):
    async def chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> LLMResponse:
        raise RuntimeError("provider exploded")


class StreamingProvider(LLMProvider):
    async def chat(
        self, messages: list[Message], tools: list[dict[str, object]], model: str
    ) -> LLMResponse:
        return LLMResponse(content="visible response")


@pytest.mark.asyncio
async def test_tui_uses_aurora_and_full_layout(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "nexus-aurora"
        assert app.query_one("#sidebar").display
        assert app.query_one("#preview").display
        assert len(app.query_one("#project-tree").root.children) == 1
        assert app.query_one("#diff-preview")
        assert app.query_one("#tool-inspector")
        assert not app.query_one("#history").lines
        assert app.query_one("#project-tree").region.height >= 8


@pytest.mark.asyncio
async def test_tool_activity_updates_tasks_and_inspector(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.render_step(
            AgentStep(
                number=1,
                user_request="inspect",
                tool_name="read_file",
                arguments={"path": "app.py"},
            )
        )
        app.render_step(
            AgentStep(
                number=1,
                user_request="inspect",
                tool_name="read_file",
                arguments={"path": "app.py"},
                result="print('ok')",
                status=StepStatus.SUCCEEDED,
                duration_ms=12,
            )
        )
        await pilot.pause()

        assert app.task_items == [("read file", "done")]
        assert app.query_one("#preview").active == "preview-tab"


@pytest.mark.asyncio
async def test_activity_updates_existing_item_without_duplicates(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._update_activity("tool-1", "execute command", ActivityStatus.PENDING, 0)
        app._update_activity("tool-1", "execute command", ActivityStatus.RUNNING, 45)
        app._update_activity("tool-1", "execute command", ActivityStatus.COMPLETED, 100)
        await pilot.pause()

        assert len(app.activities) == 1
        assert len(app.activity_history) == 1
        assert app.activities["tool-1"].progress == 100
        app._retire_activity("tool-1")
        assert "tool-1" not in app.activities
        assert len(app.activity_history) == 1


@pytest.mark.asyncio
async def test_activity_history_is_limited_to_twenty_items(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)):
        for number in range(25):
            app._update_activity(
                f"activity-{number}", "operation", ActivityStatus.COMPLETED, 100
            )

        assert len(app.activity_history) == 20
        assert app.activity_history[0].activity_id == "activity-5"


@pytest.mark.asyncio
async def test_tui_hides_side_panels_on_small_terminal(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert not app.query_one("#sidebar").display
        assert not app.query_one("#preview").display


@pytest.mark.asyncio
async def test_tui_keeps_full_layout_on_ultrawide_terminal(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        assert app.query_one("#sidebar").display
        assert app.query_one("#preview").display
        assert not app.query_one("#model").display


@pytest.mark.asyncio
async def test_tui_opens_first_workspace_folder(tmp_path: Path) -> None:
    first = tmp_path / "frontend"
    second = tmp_path / "backend"
    first.mkdir()
    second.mkdir()
    workspace = tmp_path / "platform.code-workspace"
    workspace.write_text(
        '{"folders": [{"path": "frontend"}, {"path": "backend"}]}', encoding="utf-8"
    )
    app = TheCodeApp(tmp_path, workspace_path=workspace)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.root == first
        assert app.workspace is not None
        assert len(app.workspace.folders) == 2


@pytest.mark.asyncio
async def test_commands_opens_command_center(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.handle_command("/commands")
        await pilot.pause()
        assert isinstance(app.screen, CommandsScreen)


@pytest.mark.asyncio
async def test_ctrl_k_opens_command_center(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandsScreen)


@pytest.mark.asyncio
async def test_slash_input_shows_and_filters_command_suggestions(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt")
        prompt.load_text("/arch")
        await pilot.pause()

        suggestions = app.query_one("#command-suggestions")
        assert suggestions.display
        assert suggestions.option_count == 1

        await pilot.press("tab")
        assert prompt.text == "/architect "
        assert not suggestions.display


def test_file_mentions_attach_project_content(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("VALUE = 42", encoding="utf-8")
    app = TheCodeApp(tmp_path)

    expanded, attached = app._expand_file_mentions("Explain @service.py")

    assert attached == ["service.py"]
    assert "VALUE = 42" in expanded
    assert '<attached_file path="service.py">' in expanded


def test_sensitive_file_mentions_are_blocked(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=secret", encoding="utf-8")
    app = TheCodeApp(tmp_path)

    with pytest.raises(ValueError, match="sensitive files are protected"):
        app._expand_file_mentions("Explain @.env")


def test_quick_open_excludes_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "service.py").write_text("pass", encoding="utf-8")

    screen = QuickOpenScreen({"project": tmp_path})

    assert any(label.endswith("service.py") for label in screen.files)
    assert not any(label.endswith(".env") for label in screen.files)


@pytest.mark.asyncio
async def test_ctrl_p_opens_quick_file_picker(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("pass", encoding="utf-8")
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, QuickOpenScreen)


@pytest.mark.asyncio
async def test_project_tree_excludes_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "app.py").write_text("pass", encoding="utf-8")
    app = TheCodeApp(tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        paths = [node.data for node in app.query_one("#project-tree").root.children]
        assert tmp_path / "app.py" in paths
        assert tmp_path / ".env" not in paths


@pytest.mark.asyncio
async def test_tui_shows_provider_failure_in_main_panel(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.agent = AgentLoop(
            FailingProvider(),
            ToolRegistry.defaults(tmp_path),
            PermissionManager("safe"),
            "mock",
            on_stream_start=app.stream_start,
            on_token=app.stream_token,
            on_stream_end=app.stream_end,
        )
        prompt = app.query_one("#prompt")
        prompt.load_text("hello")
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert "provider exploded" in app.last_response


@pytest.mark.asyncio
async def test_streamed_response_is_visible_before_next_message(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.agent = AgentLoop(
            StreamingProvider(),
            ToolRegistry.defaults(tmp_path),
            PermissionManager("safe"),
            "mock",
            on_stream_start=app.stream_start,
            on_token=app.stream_token,
            on_stream_end=app.stream_end,
        )
        prompt = app.query_one("#prompt")
        prompt.load_text("hello")
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.last_response == "visible response"
        assert app.query_one("#response").display
        assert app.output_tokens_estimate > 0
        assert app.first_token_ms is not None
