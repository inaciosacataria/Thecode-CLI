import asyncio
import subprocess
from pathlib import Path

from nexus.repository.git import current_branch
from nexus.tools.git_diff import GitDiffInput, GitDiffTool
from nexus.tools.git_history import GitBranchesInput, GitBranchesTool, GitLogInput, GitLogTool


def test_git_diff(tmp_path: Path) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    path = tmp_path / "file.txt"
    path.write_text("first\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"], check=True, capture_output=True)
    path.write_text("second\n", encoding="utf-8")
    result = asyncio.run(GitDiffTool(tmp_path).execute(GitDiffInput()))
    assert result.success
    assert "+second" in result.output


def test_git_history_and_branches(tmp_path: Path) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "file.txt").write_text("first\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "file.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )

    log = asyncio.run(GitLogTool(tmp_path).execute(GitLogInput()))
    branches = asyncio.run(GitBranchesTool(tmp_path).execute(GitBranchesInput()))

    assert log.success and "initial commit" in log.output
    assert branches.success


def test_current_branch_reports_repository_without_commits(tmp_path: Path) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    branch = asyncio.run(current_branch(tmp_path))

    assert branch != "not a git repository"
