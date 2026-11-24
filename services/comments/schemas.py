"""Pydantic serialization for the comment service."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.user.schemas import UserShort


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    task_id: int
    author: UserShort
    is_system: bool
    created_at: datetime


class CommentListResponse(BaseModel):
    items: list[CommentRead]
    total: int
    page: int
    page_size: int
    pages: int
