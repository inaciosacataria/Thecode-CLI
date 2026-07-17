from __future__ import annotations

import asyncio
from pathlib import Path


async def run_git(root: Path, *args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(root),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def current_branch(root: Path) -> str:
    code, output, _ = await run_git(root, "branch", "--show-current")
    if code != 0:
        return "not a git repository"
    return output.strip() or "git repository (no commits)"
