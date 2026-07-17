from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.panel import Panel
from rich.table import Table

from nexus.config.loader import _merge, _read_yaml
from nexus.security.secrets import looks_like_secret
from nexus.ui.console import console
from nexus.ui.prompts import CleanPrompt
from nexus.ui.renderer import content_width

PROVIDER_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
DEFAULT_MODELS = {
    "openrouter": "anthropic/claude-sonnet-4",
    "openai": "gpt-4.1",
    "anthropic": "claude-sonnet-4-20250514",
    "ollama": "qwen2.5-coder:latest",
}
FALLBACK_MODELS = {
    "openrouter": [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4.1",
        "google/gemini-2.5-pro",
        "deepseek/deepseek-chat-v3-0324",
    ],
    "openai": ["gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini"],
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-3-7-sonnet-20250219",
    ],
    "ollama": ["qwen2.5-coder:latest", "deepseek-coder-v2:latest", "codellama:latest"],
}


def fetch_models(
    provider: str,
    credential: str = "",
    base_url: str = "http://localhost:11434",
    client: httpx.Client | None = None,
) -> list[str]:
    http = client or httpx.Client(timeout=15)
    if provider == "ollama":
        response = http.get(f"{base_url.rstrip('/')}/api/tags")
        response.raise_for_status()
        return sorted(item["name"] for item in response.json().get("models", []))
    if provider == "anthropic":
        response = http.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": credential, "anthropic-version": "2023-06-01"},
        )
    else:
        host = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.openai.com/v1"
        response = http.get(
            f"{host}/models",
            headers={"Authorization": f"Bearer {credential}"},
        )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return sorted(item["id"] for item in data.get("data", []) if item.get("id"))


def _choose_model(provider: str, models: list[str]) -> str:
    available = models or FALLBACK_MODELS[provider]
    table = Table(
        "#",
        "Available model",
        header_style="brand",
        border_style="bright_black",
        row_styles=("", "dim"),
        width=content_width(),
    )
    for number, model in enumerate(available, 1):
        table.add_row(str(number), model)
    table.add_row("0", "Enter a model identifier manually")
    console.print(table)
    while True:
        selection = CleanPrompt.ask("[brand]Model[/brand] [muted]›[/muted]", default="1").strip()
        if selection == "0":
            return CleanPrompt.ask(
                "[brand]Model identifier[/brand] [muted]›[/muted]",
                default=DEFAULT_MODELS[provider],
            ).strip()
        if selection.isdigit() and 1 <= int(selection) <= len(available):
            return available[int(selection) - 1]
        console.print("[warning]Choose one of the numbers shown above.[/warning]")


def _set_env_value(path: Path, name: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{name}="
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            updated.append(f"{prefix}{value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{prefix}{value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def save_provider_configuration(
    root: Path,
    provider: str,
    model: str,
    credential: str = "",
    permission_mode: str | None = None,
) -> None:
    if provider not in {*PROVIDER_KEYS, "ollama"}:
        raise ValueError(f"Unsupported provider: {provider}")
    if not model.strip() or looks_like_secret(model):
        raise ValueError("Enter a valid model identifier")
    if provider in PROVIDER_KEYS:
        key_name = PROVIDER_KEYS[provider]
        if not credential.strip() and not os.getenv(key_name):
            raise ValueError("The API key cannot be empty")
        if credential.strip():
            _set_env_value(root / ".env", key_name, credential.strip())
    config_path = root / ".nexus" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_yaml(config_path)
    values: dict[str, Any] = {"llm": {"provider": provider, "model": model.strip()}}
    if permission_mode:
        values["permissions"] = {"mode": permission_mode}
    config_path.write_text(yaml.safe_dump(_merge(existing, values), sort_keys=False), encoding="utf-8")


def configure_provider(root: Path) -> tuple[str, str]:
    console.print(
        Panel(
            "Choose the provider and model used by this project.\n"
            "[muted]Credentials are stored in the local .env file.[/muted]",
            title="[brand] AI configuration [/brand]",
            title_align="left",
            border_style="bright_black",
            width=content_width(),
        )
    )
    provider = CleanPrompt.ask(
        "[brand]Provider[/brand] [muted]›[/muted]",
        choices=["openrouter", "openai", "anthropic", "ollama"],
        default="openrouter",
    )
    credential = ""
    base_url = "http://localhost:11434"
    if provider in PROVIDER_KEYS:
        key_name = PROVIDER_KEYS[provider]
        credential = CleanPrompt.ask(
            f"[brand]{key_name}[/brand] [muted]›[/muted]", password=True
        ).strip()
        if not credential:
            raise ValueError("The API key cannot be empty")
    else:
        base_url = CleanPrompt.ask(
            "[brand]Ollama URL[/brand] [muted]›[/muted]", default=base_url
        ).strip()

    try:
        models = fetch_models(provider, credential, base_url)
    except httpx.HTTPError as error:
        console.print(
            f"[yellow]Could not retrieve the live model list ({error}). Using fallback models.[/yellow]"
        )
        models = FALLBACK_MODELS[provider]
    model = _choose_model(provider, models)
    if looks_like_secret(model):
        raise ValueError("The model identifier cannot be an API key")

    if provider in PROVIDER_KEYS:
        _set_env_value(root / ".env", PROVIDER_KEYS[provider], credential)
    else:
        _set_env_value(root / ".env", "OLLAMA_BASE_URL", base_url)

    config_path = root / ".nexus" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_yaml(config_path)
    updated = _merge(existing, {"llm": {"provider": provider, "model": model}})
    config_path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    return provider, model
