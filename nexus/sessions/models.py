from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Session(BaseModel):
    id: str
    project_dir: str
    branch: str = ""
    provider: str
    model: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "active"
    summary: str = ""


class FileChange(BaseModel):
    id: int | None = None
    session_id: str
    path: str
    previous_content: str | None
    previous_hash: str | None
    new_hash: str
    created: bool = False
    undone: bool = False


class StoredMessage(BaseModel):
    session_id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)

