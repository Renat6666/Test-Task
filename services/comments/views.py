"""All comment endpoint logic. Routes live in controllers.py.

Rate limiting is not called here: it is wired as a route dependency
(see controllers.py) so the limit is enforced before any business logic runs.
"""

import secrets
from typing import Annotated

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import get_settings
from core.dependencies import CurrentUser, DbSession
from core.exceptions import NotFoundError
from core.security import hash_password
from db.models import Comment, Task, User
from services.comments.schemas import (
    CommentCreate,
    CommentListResponse,
    CommentRead,
)


async def _assert_task_exists(db: AsyncSession, task_id: int) -> None:
    exists = await db.scalar(select(Task.id).where(Task.id == task_id))
    if exists is None:
        raise NotFoundError(f"Task {task_id} not found", code="TASK_NOT_FOUND")


async def get_or_create_system_user(db: AsyncSession) -> User:
    """Technical author of system comments (quotes). Idempotent."""
    settings = get_settings()
    user = await db.scalar(
        select(User).where(User.email == settings.system_user_email)
    )
    if user is not None:
        return user

    user = User(
        username=settings.system_user_username,
        email=settings.system_user_email,
        password=hash_password(secrets.token_urlsafe(32)),
        is_active=False,
    )
    db.add(user)
    await db.flush()
    return user


async def list_comments(
    task_id: int,
    db: DbSession,
    _: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CommentListResponse:
    await _assert_task_exists(db, task_id)

    total = await db.scalar(
        select(func.count()).select_from(Comment).where(Comment.task_id == task_id)
    ) or 0

    rows = await db.scalars(
        select(Comment)
        .where(Comment.task_id == task_id)
        .options(selectinload(Comment.author))
        .order_by(Comment.created_at, Comment.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    return CommentListResponse(
        items=[CommentRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


async def create_comment(
    task_id: int,
    payload: CommentCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> CommentRead:
    await _assert_task_exists(db, task_id)

    comment = Comment(
        content=payload.content,
        task_id=task_id,
        author_id=current_user.id,
        is_system=False,
    )
    db.add(comment)
    await db.flush()

    reloaded = await db.scalar(
        select(Comment)
        .where(Comment.id == comment.id)
        .options(selectinload(Comment.author))
    )
    return CommentRead.model_validate(reloaded)
