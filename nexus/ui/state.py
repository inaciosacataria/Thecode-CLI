from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActivityStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ActivityType(StrEnum):
    THINKING = "THINKING"
    READ_FILE = "READ_FILE"
    SEARCH_FILES = "SEARCH_FILES"
    WRITE_FILE = "WRITE_FILE"
    EDIT_FILE = "EDIT_FILE"
    EXECUTE_COMMAND = "EXECUTE_COMMAND"
    RUN_TESTS = "RUN_TESTS"
    GIT_OPERATION = "GIT_OPERATION"
    LLM_REQUEST = "LLM_REQUEST"
    INDEXING = "INDEXING"
    OTHER = "OTHER"


FINAL_ACTIVITY_STATES = {
    ActivityStatus.COMPLETED,
    ActivityStatus.FAILED,
    ActivityStatus.CANCELLED,
}


@dataclass
class Activity:
    id: str
    type: ActivityType
    title: str
    status: ActivityStatus = ActivityStatus.PENDING
    progress: int | None = None
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    duration_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def activity_id(self) -> str:
        return self.id

    @property
    def label(self) -> str:
        return self.title

    @label.setter
    def label(self, value: str) -> None:
        self.title = value

    @property
    def finished_at(self) -> float | None:
        return self.completed_at

    @property
    def updated_at(self) -> float:
        return self.completed_at or self.started_at

    @property
    def timestamp(self) -> str:
        return ""

    def update(
        self,
        *,
        status: ActivityStatus,
        progress: int | None = None,
        title: str | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.progress = None if progress is None else max(0, min(100, progress))
        if title:
            self.title = title
        if details:
            self.details.update(details)
        self.error = error
        if status in FINAL_ACTIVITY_STATES and self.completed_at is None:
            self.completed_at = time.monotonic()
            self.duration_ms = round((self.completed_at - self.started_at) * 1000)


@dataclass
class ConversationMessage:
    role: str
    content: str


@dataclass
class UIState:
    project: str
    branch: str = "not a git repository"
    provider: str = ""
    model: str = ""
    connection_status: str = "initializing"
    indexed_files: int = 0
    session_id: str | None = None
    context_usage: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    current_task: str = ""
    status_message: str = ""
    conversation: list[ConversationMessage] = field(default_factory=list)
    activities: dict[str, Activity] = field(default_factory=dict)
    activity_history: list[Activity] = field(default_factory=list)
    selected_file: str | None = None
    selected_tab: str = "preview-tab"
    terminal_output: list[str] = field(default_factory=list)
    changed_files: dict[str, str] = field(default_factory=dict)
    test_result: str | None = None
    errors: list[str] = field(default_factory=list)
    history_limit: int = 20

    def upsert_activity(
        self,
        activity_id: str,
        title: str,
        status: ActivityStatus,
        progress: int | None = None,
        *,
        activity_type: ActivityType = ActivityType.OTHER,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Activity:
        item = self.activities.get(activity_id)
        if item is None:
            item = Activity(activity_id, activity_type, title)
            self.activities[activity_id] = item
        item.update(status=status, progress=progress, title=title, details=details, error=error)
        if status in FINAL_ACTIVITY_STATES and all(old.id != item.id for old in self.activity_history):
            self.activity_history.append(item)
            del self.activity_history[:-self.history_limit]
        return item

    def retire_activity(self, activity_id: str) -> None:
        self.activities.pop(activity_id, None)
