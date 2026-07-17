from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from nexus.config.models import Settings


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration must be a mapping: {path}")
    return raw


def load_settings(project_root: Path | None = None) -> Settings:
    root = (project_root or Path.cwd()).resolve()
    load_dotenv(root / ".env", override=False)
    data = _merge(_read_yaml(Path.home() / ".nexus" / "config.yaml"), _read_yaml(root / ".nexus" / "config.yaml"))
    env_provider = os.getenv("NEXUS_PROVIDER")
    env_model = os.getenv("NEXUS_MODEL")
    if env_provider or env_model:
        data.setdefault("llm", {})
    if env_provider:
        data["llm"]["provider"] = env_provider
    if env_model:
        data["llm"]["model"] = env_model
    data["project_root"] = root
    return Settings.model_validate(data)

