from pathlib import Path

import httpx
import pytest

from nexus.config.loader import load_settings
from nexus.config.wizard import _set_env_value, fetch_models, save_provider_configuration
from nexus.llm.router import create_provider


def test_project_config_is_loaded(tmp_path: Path) -> None:
    config_dir = tmp_path / ".nexus"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("agent:\n  max_steps: 7\npermissions:\n  mode: auto\n", encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.agent.max_steps == 7
    assert settings.permissions.mode == "auto"


def test_openrouter_free_model_does_not_require_key(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)
    provider = create_provider(settings.llm)
    assert provider.__class__.__name__ == "FailoverProvider"


def test_env_writer_preserves_other_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OTHER=value\nOPENAI_API_KEY=old\n", encoding="utf-8")
    _set_env_value(path, "OPENAI_API_KEY", "new")
    assert path.read_text(encoding="utf-8") == "OTHER=value\nOPENAI_API_KEY=new\n"


def test_provider_rejects_secret_used_as_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "safe-test-value")
    settings = load_settings(tmp_path)
    settings.llm.model = "sk-accidentally-pasted-key"
    try:
        create_provider(settings.llm)
    except ValueError as error:
        assert "model looks like an API key" in str(error)
    else:
        raise AssertionError("Expected a secret-shaped model to be rejected")


def test_openrouter_paid_model_requires_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = load_settings(tmp_path)
    settings.llm.model = "anthropic/claude-sonnet-4"
    try:
        create_provider(settings.llm)
    except ValueError as error:
        assert "OPENROUTER_API_KEY is not configured" in str(error)
    else:
        raise AssertionError("Expected paid OpenRouter models to require a key")


def test_lists_openrouter_models_with_mock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={"data": [{"id": "model/b:free"}, {"id": "model/a"}, {"id": "openrouter/free"}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_models("openrouter", "test-key", client=client) == [
        "openrouter/free",
        "model/b:free",
    ]


def test_lists_openrouter_models_without_key_with_mock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "model/a:free"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_models("openrouter", client=client) == ["openrouter/free", "model/a:free"]


def test_lists_ollama_models_with_mock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen:latest"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_models("ollama", base_url="http://ollama.test", client=client) == [
        "qwen:latest"
    ]


def test_saves_provider_model_key_and_permissions(tmp_path: Path) -> None:
    save_provider_configuration(
        tmp_path, "openrouter", "model/example", "secret-value", "auto"
    )

    assert "OPENROUTER_API_KEY=secret-value" in (tmp_path / ".env").read_text(encoding="utf-8")
    saved = (tmp_path / ".nexus" / "config.yaml").read_text(encoding="utf-8")
    assert "model/example" in saved
    assert "mode: auto" in saved


def test_saves_free_openrouter_model_without_key(tmp_path: Path) -> None:
    save_provider_configuration(tmp_path, "openrouter", "openrouter/free", "", "ask")
    env_path = tmp_path / ".env"
    assert not env_path.exists() or "OPENROUTER_API_KEY" not in env_path.read_text(encoding="utf-8")
    saved = (tmp_path / ".nexus" / "config.yaml").read_text(encoding="utf-8")
    assert "openrouter/free" in saved
