import os

from nexus.config.models import LLMConfig
from nexus.llm.anthropic_provider import AnthropicProvider
from nexus.llm.base import LLMProvider
from nexus.llm.ollama_provider import OllamaProvider
from nexus.llm.openai_provider import OpenAIProvider, OpenRouterProvider
from nexus.security.secrets import looks_like_secret


def create_provider(config: LLMConfig) -> LLMProvider:
    if looks_like_secret(config.model):
        raise ValueError(
            "The configured model looks like an API key. For security it will not be used. "
            "Run 'thecode config --setup' and enter a model identifier in the Model field."
        )
    if config.provider == "openai":
        return OpenAIProvider(_required_key("OPENAI_API_KEY"))
    if config.provider == "anthropic":
        return AnthropicProvider(_required_key("ANTHROPIC_API_KEY"))
    if config.provider == "ollama":
        return OllamaProvider(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    return OpenRouterProvider(_required_key("OPENROUTER_API_KEY"))


def _required_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"{name} is not configured. Add it to the environment or the project .env file."
        )
    return value
