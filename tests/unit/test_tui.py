from pathlib import Path

import pytest

from nexus.agent.loop import AgentLoop
from nexus.agent.state import AgentStep, StepStatus
from nexus.llm.base import LLMProvider, LLMResponse, Message
from nexus.permissions.manager import PermissionManager
from nexus.tools.registry import ToolRegistry
from nexus.ui.tui import (
    ActivityStatus,
    CommandsScreen,
    PermissionScreen,
    QuickOpenScreen,
    TheCodeApp,
)


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
        assert not app.query_one("#preview").display
        assert len(app.query_one("#project-tree").root.children) == 1
        assert app.query_one("#diff-preview")
        assert app.query_one("#tool-inspector")
        assert not app.query_one("#history").lines
        assert app.query_one("#empty-state").display
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
@pytest.mark.parametrize(
    ("size", "sidebar", "preview"),
    [((80, 24), False, False), ((100, 30), True, False), ((120, 40), True, False), ((160, 50), True, True)],
)
async def test_tui_responsive_breakpoints(
    tmp_path: Path, size: tuple[int, int], sidebar: bool, preview: bool
) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.query_one("#sidebar").display is sidebar
        assert app.query_one("#preview").display is preview
        assert app.query_one("#prompt").display
        assert app.query_one("#statusbar").display


@pytest.mark.asyncio
async def test_prompt_history_and_multiline_input(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt")
        app.prompt_history = ["first request", "second request"]
        app.prompt_history_index = len(app.prompt_history)
        prompt.load_text("draft")
        await pilot.press("up")
        assert prompt.text == "second request"
        await pilot.press("down")
        assert prompt.text == "draft"
        prompt.load_text("line one")
        await pilot.press("shift+enter")
        assert "\n" in prompt.text


def test_required_tui_bindings_are_registered(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    keys = {
        binding.key if hasattr(binding, "key") else binding[0]
        for binding in app.BINDINGS
    }
    assert {"ctrl+r", "ctrl+t", "ctrl+l", "ctrl+k", "ctrl+p", "ctrl+c", "escape"} <= keys


@pytest.mark.asyncio
async def test_tui_compact_layout_hides_side_panels_and_uses_shortcuts(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(90, 28)) as pilot:
        await pilot.pause()
        assert not app.query_one("#sidebar").display
        assert not app.query_one("#preview").display
        assert not app.query_one("#activity-log").display
        assert not app.query_one("#progress").display
        assert app.query_one("#prompt").styles.height.value == 4


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
async def test_command_center_filters_fuzzily_without_losing_draft(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt")
        prompt.load_text("unfinished request")
        await pilot.press("ctrl+k")
        query = app.screen.query_one("#command-query")
        query.value = "rntst"
        await pilot.pause()
        options = app.screen.query_one("#command-list")
        labels = [str(options.get_option_at_index(index).prompt) for index in range(options.option_count)]
        assert any("Run tests" in label for label in labels)
        await pilot.press("escape")
        assert prompt.text == "unfinished request"


@pytest.mark.asyncio
async def test_execution_plan_updates_existing_tool_step(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        app.ui_state.current_task = "Create fruit API"
        running = AgentStep(
            number=1,
            user_request="Create fruit API",
            tool_name="write_file",
            arguments={"path": "app.py"},
        )
        app.render_step(running)
        app.render_step(running)
        app.render_step(
            running.model_copy(
                update={"status": StepStatus.SUCCEEDED, "result": "updated", "duration_ms": 10}
            )
        )
        await pilot.pause()
        assert app.task_items == [("write file", "done")]
        assert "CURRENT GOAL" in str(app.query_one("#tasks").render())


@pytest.mark.asyncio
async def test_stale_task_and_session_events_are_ignored(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        app.active_task_id = "new-task"
        app.stream_token("stale", "old-task")
        assert app.stream == ""
        terminal = app.query_one("#terminal-log")
        before = len(terminal.lines)
        app.session_id = "new-session"
        app.process_output("process", "stdout", "stale", None, "old-session")
        await pilot.pause()
        assert len(terminal.lines) == before


@pytest.mark.asyncio
async def test_cancel_marks_activity_and_hides_progress(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        app.active_task_id = "task"
        app._update_activity("tool", "run tests", ActivityStatus.RUNNING, 50)
        progress = app.query_one("#progress")
        progress.display = True
        app.action_cancel()
        await pilot.pause()
        assert app.activities["tool"].status == ActivityStatus.CANCELLED
        assert not progress.display
        assert app.ui_state.status_message == "Cancelled"


@pytest.mark.asyncio
async def test_clear_is_visual_only_and_removes_transient_activity(tmp_path: Path) -> None:
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.session_id = "session-kept"
        app.query_one("#history").write("message")
        app._update_activity("done", "read file", ActivityStatus.COMPLETED, 100)
        app.action_clear()
        await pilot.pause()
        assert app.session_id == "session-kept"
        assert not app.query_one("#history").lines
        assert app.activity_history == []
        assert not app.query_one("#empty-state").display


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
async def test_sensitive_reveal_requires_confirmation(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=secret", encoding="utf-8")
    app = TheCodeApp(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        app._show_protected_preview(path)
        app.action_reveal_sensitive()
        await pilot.pause()
        assert isinstance(app.screen, PermissionScreen)
        assert not app.sensitive_preview_revealed


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
        assert not app.query_one("#empty-state").display
