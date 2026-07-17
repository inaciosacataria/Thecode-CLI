import asyncio
from pathlib import Path

import pytest

from nexus.tools.processes import ProcessManager, detect_progress


def test_detects_percent_and_fraction_progress() -> None:
    assert detect_progress("Building 56%") == 56
    assert detect_progress("Tests 25/100") == 25
    assert detect_progress("server running") is None


@pytest.mark.asyncio
async def test_process_manager_streams_output_and_completion(tmp_path: Path) -> None:
    (tmp_path / "progress.py").write_text(
        "print('Building 25%', flush=True)\nprint('Building 100%', flush=True)\n",
        encoding="utf-8",
    )
    events: list[tuple[str, str, str, float | None]] = []
    manager = ProcessManager(tmp_path)
    manager.output_callback = lambda process_id, stream, text, progress: events.append(
        (process_id, stream, text, progress)
    )

    managed = await manager.start("python progress.py")
    await managed.process.wait()
    await asyncio.gather(*managed.tasks, return_exceptions=True)

    assert managed.status == "completed"
    assert any(event[3] == 25 for event in events)
    assert any(event[3] == 100 for event in events)
    assert any(event[1] == "status" for event in events)
