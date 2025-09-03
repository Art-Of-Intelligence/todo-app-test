from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------- Enums ----------

class CalendarProvider(str, Enum):
    google = "google"


class TaskListFilter(str, Enum):
    today = "today"
    upcoming = "upcoming"
    done = "done"


# ---------- Shared Query Models (for /tasks listing) ----------

class TaskListQuery(BaseModel):
    """Query params for GET /api/v1/tasks"""
    model_config = ConfigDict(extra="forbid")

    list: Optional[TaskListFilter] = None
    q: Optional[str] = Field(default=None, description="Free-text search")
    due_from: Optional[datetime] = None
    due_to: Optional[datetime] = None

    @model_validator(mode="after")
    def _check_range(self):
        if self.due_from and self.due_to and self.due_from > self.due_to:
            raise ValueError("due_from must be <= due_to")
        return self


# ---------- Calendar Event ----------

class CalendarEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: CalendarProvider
    calendar_id: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1, description="Usually same as task title")
    start_datetime: datetime
    end_datetime: datetime
    description: Optional[str] = None
    timezone: str = Field(..., description="IANA TZ, e.g. 'Asia/Colombo'")
    reminder_minutes_before: Optional[int] = Field(
        default=None, ge=0, description="Minutes before start to remind"
    )

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"Invalid IANA timezone: {v}") from e
        return v

    @model_validator(mode="after")
    def _validate_time_order(self):
        if self.end_datetime <= self.start_datetime:
            raise ValueError("end_datetime must be after start_datetime")
        return self


class CalendarEventCreate(CalendarEventBase):
    """Use when creating an event with a provider (no provider event_id yet)."""
    pass


class CalendarEvent(CalendarEventBase):
    """Saved/external event reference (after provider created it)."""
    event_id: str = Field(..., min_length=1)


# ---------- SubTask ----------

class SubTaskBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    due_datetime: Optional[datetime] = None
    completed: bool = False

    # Calendar sync knobs for a subtask
    add_to_calendar: bool = False
    event_duration_minutes: int = Field(default=60, ge=1, le=24 * 60)

    # Optional external calendar linking (may remain None if not synced)
    calendar_id: Optional[str] = None
    calendar_event_id: Optional[str] = None


class SubTaskCreate(SubTaskBase):
    """POST /api/v1/tasks/{task_id}/subtasks"""
    pass


class SubTaskUpdate(BaseModel):
    """PATCH /api/v1/tasks/{task_id}/subtasks/{subtask_id}"""
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1)
    due_datetime: Optional[datetime] = None
    completed: Optional[bool] = None
    add_to_calendar: Optional[bool] = None
    event_duration_minutes: Optional[int] = Field(default=None, ge=1, le=24 * 60)
    calendar_id: Optional[str] = None
    calendar_event_id: Optional[str] = None


class SubTask(SubTaskBase):
    """In-memory saved representation of a subtask."""
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------- Task ----------

class TaskBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    note: Optional[str] = None
    due_datetime: Optional[datetime] = None
    completed: bool = False


class TaskCreate(TaskBase):
    """POST /api/v1/tasks — optionally include a calendar event to create."""
    calendar_event: Optional[CalendarEventCreate] = None


class TaskUpdate(BaseModel):
    """PATCH /api/v1/tasks/{task_id}"""
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1)
    note: Optional[str] = None
    due_datetime: Optional[datetime] = None
    completed: Optional[bool] = None
    # Replace or set calendar event (service layer decides create/update/delete)
    calendar_event: Optional[CalendarEventCreate | CalendarEvent] = None


class Task(TaskBase):
    """In-memory saved representation of a task."""
    id: UUID = Field(default_factory=uuid4)
    # One task → optional linked calendar event
    calendar_event: Optional[CalendarEvent] = None
    # Task owns a list of subtasks
    subtasks: List[SubTask] = Field(default_factory=list)

    # Helpful computed properties can be added via @property in service layer
    # (e.g., is_overdue, progress, etc.)


# ---------- Response DTOs (optional convenience) ----------

class TaskDetail(Task):
    """Useful for GET /api/v1/tasks/{task_id} responses."""
    pass


class TaskListItem(BaseModel):
    """Lightweight listing item for GET /api/v1/tasks"""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    title: str
    due_datetime: Optional[datetime] = None
    completed: bool = False
