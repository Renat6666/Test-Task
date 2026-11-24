"""Pydantic serialization for the task service."""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from db.models import TaskPriority, TaskStatus
from services.user.schemas import UserShort


def _as_utc(value: datetime) -> datetime:
    """Treat a naive datetime as UTC so every comparison stays tz-aware."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _validate_future(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    value = _as_utc(value)
    if value <= datetime.now(timezone.utc):
        raise ValueError("deadline must be in the future")
    return value


# --------------------------------------------------------------------------- #
#                                  Write                                      #
# --------------------------------------------------------------------------- #

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    priority: TaskPriority = TaskPriority.MEDIUM
    deadline: datetime | None = None
    assignee_id: int | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned

    @field_validator("deadline")
    @classmethod
    def deadline_in_future(cls, value: datetime | None) -> datetime | None:
        return _validate_future(value)


class TaskUpdate(BaseModel):
    """Every field is optional; views apply only what was actually sent
    (model_dump(exclude_unset=True)), so `null` and "omitted" are different."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    priority: TaskPriority | None = None
    deadline: datetime | None = None
    assignee_id: int | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned

    @field_validator("deadline")
    @classmethod
    def deadline_in_future(cls, value: datetime | None) -> datetime | None:
        return _validate_future(value)


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


# --------------------------------------------------------------------------- #
#                                   Read                                      #
# --------------------------------------------------------------------------- #

class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    deadline: datetime | None
    creator: UserShort
    assignee: UserShort | None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskRead]
    total: int
    page: int
    page_size: int
    pages: int


class TaskHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: TaskStatus | None
    to_status: TaskStatus
    changed_by: UserShort | None
    created_at: datetime


class SortBy(str, Enum):
    CREATED_AT = "created_at"
    DEADLINE = "deadline"
    PRIORITY = "priority"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class TaskFilterParams(BaseModel):
    """Wired as Annotated[..., Query()]; every field is read from query params."""

    model_config = ConfigDict(extra="forbid")

    search: Annotated[
        str | None,
        Query(max_length=255, description="Search across title and description (ILIKE)"),
    ] = None
    status: Annotated[
        list[TaskStatus] | None, Query(description="May be repeated to match several values")
    ] = None
    priority: Annotated[list[TaskPriority] | None, Query()] = None
    assignee_id: Annotated[int | None, Query(ge=1)] = None
    unassigned: Annotated[bool, Query(description="Only tasks without an assignee")] = False
    deadline_from: Annotated[datetime | None, Query()] = None
    deadline_to: Annotated[datetime | None, Query()] = None

    sort_by: Annotated[
        SortBy | None,
        Query(description="Omitted: priority High->Low, then nearest deadline"),
    ] = None
    sort_order: Annotated[SortOrder, Query()] = SortOrder.DESC

    page: Annotated[int, Query(ge=1)] = 1
    page_size: Annotated[int, Query(ge=1, le=100)] = 20

    @model_validator(mode="after")
    def check_consistency(self) -> "TaskFilterParams":
        if self.deadline_from and self.deadline_to:
            if _as_utc(self.deadline_from) > _as_utc(self.deadline_to):
                raise ValueError("deadline_from must be earlier than deadline_to")
        if self.unassigned and self.assignee_id is not None:
            raise ValueError("unassigned cannot be combined with assignee_id")
        return self

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class AssigneeLoad(BaseModel):
    assignee_id: int
    username: str
    active_tasks: int


class AnalyticsResponse(BaseModel):
    total_tasks: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    overdue_tasks: int
    active_tasks_per_assignee: list[AssigneeLoad]
    avg_completion_hours: float | None
    generated_at: datetime
