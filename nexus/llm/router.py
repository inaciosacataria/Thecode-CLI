import os

from nexus.config.models import LLMConfig, is_free_openrouter_model
from nexus.llm.anthropic_provider import AnthropicProvider
from nexus.llm.base import LLMProvider
from nexus.llm.failover import FailoverProvider, ProviderCandidate
from nexus.llm.ollama_provider import OllamaProvider
from nexus.llm.openai_provider import OpenAIProvider, OpenRouterProvider
from nexus.security.secrets import looks_like_secret


def create_provider(config: LLMConfig) -> LLMProvider:
    if looks_like_secret(config.model):
        raise ValueError(
            "The configured model looks like an API key. For security it will not be used. "
            "Run 'thecode config --setup' and enter a model identifier in the Model field."
        )
    candidates = [
        ProviderCandidate(_create_single(config.provider, config.model), config.model, config.provider)
    ]
    for fallback in config.fallbacks:
        try:
            provider = _create_single(fallback.provider, fallback.model)
        except ValueError:
            continue
        candidates.append(ProviderCandidate(provider, fallback.model, fallback.provider))
    return FailoverProvider(
        candidates, attempts=config.retry_attempts, timeout=config.request_timeout
    )


def _create_single(provider: str, model: str) -> LLMProvider:
    if provider == "openai":
        return OpenAIProvider(_required_key("OPENAI_API_KEY"))
    if provider == "anthropic":
        return AnthropicProvider(_required_key("ANTHROPIC_API_KEY"))
    if provider == "gemini":
        return OpenAIProvider(
            _required_key("GEMINI_API_KEY"),
            "https://generativelanguage.googleapis.com/v1beta/openai",
        )
    if provider == "ollama":
        return OllamaProvider(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key and not is_free_openrouter_model(model):
        raise ValueError(
            "OPENROUTER_API_KEY is not configured for this model. "
            "Use a free OpenRouter model or add a key in the config setup."
        )
    return OpenRouterProvider(api_key)


def _required_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"{name} is not configured. Add it to the environment or the project .env file."
        )
    return value
