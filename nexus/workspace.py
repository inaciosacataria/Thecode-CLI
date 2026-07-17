from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _strip_jsonc(source: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        character = source[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if source[index : index + 2] == "//":
            newline = source.find("\n", index)
            index = len(source) if newline == -1 else newline
            continue
        if source[index : index + 2] == "/*":
            end = source.find("*/", index + 2)
            index = len(source) if end == -1 else end + 2
            continue
        output.append(character)
        index += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(output))


@dataclass(frozen=True)
class WorkspaceFolder:
    name: str
    path: Path


@dataclass
class CodeWorkspace:
    path: Path
    folders: list[WorkspaceFolder]
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> CodeWorkspace:
        workspace_path = path.resolve(strict=True)
        if workspace_path.suffix != ".code-workspace":
            raise ValueError("Workspace file must use the .code-workspace extension")
        data: dict[str, Any] = json.loads(_strip_jsonc(workspace_path.read_text(encoding="utf-8")))
        raw_folders = data.get("folders")
        if not isinstance(raw_folders, list) or not raw_folders:
            raise ValueError("Workspace must contain at least one folder")
        folders: list[WorkspaceFolder] = []
        names: set[str] = set()
        for item in raw_folders:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("Each workspace folder must contain a path")
            candidate = Path(item["path"])
            if not candidate.is_absolute():
                candidate = workspace_path.parent / candidate
            resolved = candidate.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError(f"Workspace folder is not a directory: {candidate}")
            name = str(item.get("name") or resolved.name)
            if name in names:
                raise ValueError(f"Duplicate workspace folder name: {name}")
            names.add(name)
            folders.append(WorkspaceFolder(name, resolved))
        return cls(workspace_path, folders, data)

    def add_folder(self, folder: Path, name: str | None = None) -> None:
        resolved = folder.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"Not a directory: {folder}")
        if any(item.path == resolved for item in self.folders):
            return
        folder_name = name or resolved.name
        if any(item.name == folder_name for item in self.folders):
            raise ValueError(f"Duplicate workspace folder name: {folder_name}")
        try:
            stored_path = resolved.relative_to(self.path.parent).as_posix()
        except ValueError:
            stored_path = str(resolved)
        raw = {"path": stored_path}
        if name:
            raw["name"] = name
        self.data.setdefault("folders", []).append(raw)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        self.folders.append(WorkspaceFolder(folder_name, resolved))

    def folder(self, name: str) -> WorkspaceFolder:
        normalized = name.casefold()
        for folder in self.folders:
            if folder.name.casefold() == normalized:
                return folder
        raise ValueError(f"Unknown workspace folder: {name}")


def discover_workspace(directory: Path) -> Path | None:
    matches = sorted(directory.resolve().glob("*.code-workspace"))
    return matches[0] if len(matches) == 1 else None
