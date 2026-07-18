from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def is_free_openrouter_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized == "openrouter/free" or normalized.endswith(":free")


class ProviderFallback(BaseModel):
    provider: Literal["openrouter", "openai", "anthropic", "gemini", "ollama"]
    model: str


class LLMConfig(BaseModel):
    provider: Literal["openrouter", "openai", "anthropic", "gemini", "ollama"] = "openrouter"
    model: str = "openrouter/free"
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=8192, gt=0)
    fallbacks: list[ProviderFallback] = Field(default_factory=list)
    retry_attempts: int = Field(default=2, ge=1, le=5)
    request_timeout: float = Field(default=120, gt=1, le=600)


class AgentConfig(BaseModel):
    max_steps: int = Field(default=30, ge=1, le=200)
    mode: Literal["ask", "plan", "agent", "review"] = "agent"


class PermissionsConfig(BaseModel):
    mode: Literal["ask", "plan", "agent", "auto", "safe"] = "ask"
    allow: list[str] = Field(default_factory=lambda: ["pytest", "git status", "git diff"])
    deny: list[str] = Field(
        default_factory=lambda: ["rm -rf /", "git push --force", "git reset --hard"]
    )


class ContextConfig(BaseModel):
    max_characters: int = Field(default=120_000, gt=1000)
    max_file_size: int = Field(default=100_000, gt=0)
    max_files_per_turn: int = Field(default=20, gt=0)


class ProjectConfig(BaseModel):
    ignore: list[str] = Field(
        default_factory=lambda: ["node_modules", "dist", "build", ".git", "vendor"]
    )


class Settings(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    project_root: Path = Field(default_factory=Path.cwd, exclude=True)
