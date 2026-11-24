"""All task endpoint logic: business rules, status machine, search/filters/
sorting, analytics and export. Routes live in controllers.py.
"""

import asyncio
import csv
import io
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator, Sequence

from fastapi import Depends, Query
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import Select, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import get_settings
from core.dependencies import CurrentUser, DbSession
from core.exceptions import ConflictError, NotFoundError
from core.logging_config import get_logger
from core.redis_client import get_redis
from db.database import get_session
from db.models import Task, TaskPriority, TaskStatus, TaskStatusHistory, User
from services.tasks.schemas import (
    AnalyticsResponse,
    AssigneeLoad,
    SortBy,
    SortOrder,
    TaskCreate,
    TaskFilterParams,
    TaskHistoryRead,
    TaskListResponse,
    TaskRead,
    TaskStatusUpdate,
    TaskUpdate,
)

logger = get_logger("app.tasks")

RedisDep = Annotated[Redis, Depends(get_redis)]
FiltersDep = Annotated[TaskFilterParams, Query()]

ANALYTICS_CACHE_KEY = "analytics:v1"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.BACKLOG: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.DONE},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}

ACTIVE_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.BACKLOG, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW}
)

FINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.DONE, TaskStatus.CANCELLED}
)

ASSIGNEE_LOCKED_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.REVIEW, TaskStatus.DONE}
)

UNDELETABLE_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.IN_PROGRESS, TaskStatus.REVIEW}
)

PRIORITY_RANK = case(
    (Task.priority == TaskPriority.HIGH, 3),
    (Task.priority == TaskPriority.MEDIUM, 2),
    (Task.priority == TaskPriority.LOW, 1),
    else_=0,
)


def _with_relations(stmt: Select) -> Select:
    return stmt.options(selectinload(Task.creator), selectinload(Task.assignee))


async def _get_task(db: AsyncSession, task_id: int) -> Task:
    task = await db.scalar(_with_relations(select(Task).where(Task.id == task_id)))
    if task is None:
        raise NotFoundError(f"Task {task_id} not found", code="TASK_NOT_FOUND")
    return task


async def _reload_task(db: AsyncSession, task_id: int) -> Task:
    """Reload the task together with its relations: without this, touching
    task.creator in an async session triggers a lazy load and MissingGreenlet."""
    await db.flush()
    db.expire_all()
    return await _get_task(db, task_id)


async def _lock_user(db: AsyncSession, user_id: int) -> User:
    """Lock the user row until the end of the transaction.

    Counting active tasks with FOR UPDATE is impossible (Postgres forbids
    locking together with aggregates), so concurrent assignments are serialized
    by locking the assignee row itself.
    """
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError(f"User {user_id} not found", code="ASSIGNEE_NOT_FOUND")
    return user


async def _assert_active_limit(
    db: AsyncSession, assignee_id: int, *, exclude_task_id: int | None = None
) -> None:
    """Rule 2: no more than N active tasks per assignee."""
    limit = get_settings().max_active_tasks_per_user
    await _lock_user(db, assignee_id)

    stmt = (
        select(func.count())
        .select_from(Task)
        .where(Task.assignee_id == assignee_id, Task.status.in_(ACTIVE_STATUSES))
    )
    if exclude_task_id is not None:
        stmt = stmt.where(Task.id != exclude_task_id)

    active = await db.scalar(stmt) or 0
    if active >= limit:
        raise ConflictError(
            f"User {assignee_id} already has {active} active tasks "
            f"(limit is {limit})",
            code="ACTIVE_TASK_LIMIT_EXCEEDED",
        )


def _assert_editable(task: Task) -> None:
    """Rule 3: tasks in Done / Cancelled are final."""
    if task.status in FINAL_STATUSES:
        raise ConflictError(
            f"Task {task.id} is in final status '{task.status.value}' "
            f"and cannot be modified",
            code="TASK_IS_FINAL",
        )


def _record_history(
    db: AsyncSession,
    task: Task,
    from_status: TaskStatus | None,
    to_status: TaskStatus,
    user_id: int,
) -> None:
    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=from_status,
            to_status=to_status,
            changed_by_id=user_id,
        )
    )


async def _invalidate_analytics(redis: Redis) -> None:
    try:
        await redis.delete(ANALYTICS_CACHE_KEY)
    except Exception as exc:
        logger.warning("Failed to invalidate analytics cache: %r", exc)


# --------------------------------------------------------------------------- #
#                                   CRUD                                      #
# --------------------------------------------------------------------------- #

async def create_task(
    payload: TaskCreate, db: DbSession, current_user: CurrentUser
) -> TaskRead:
    if payload.assignee_id is not None:
        await _assert_active_limit(db, payload.assignee_id)

    task = Task(
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        deadline=payload.deadline,
        assignee_id=payload.assignee_id,
        creator_id=current_user.id,
        status=TaskStatus.BACKLOG,
    )
    db.add(task)
    await db.flush()
    _record_history(db, task, None, TaskStatus.BACKLOG, current_user.id)

    return TaskRead.model_validate(await _reload_task(db, task.id))


async def get_task(task_id: int, db: DbSession, _: CurrentUser) -> TaskRead:
    return TaskRead.model_validate(await _get_task(db, task_id))


async def update_task(
    task_id: int, payload: TaskUpdate, db: DbSession, _: CurrentUser
) -> TaskRead:
    task = await _get_task(db, task_id)
    _assert_editable(task)

    data = payload.model_dump(exclude_unset=True)
    if not data:
        return TaskRead.model_validate(task)

    if "assignee_id" in data and data["assignee_id"] != task.assignee_id:
        if task.status in ASSIGNEE_LOCKED_STATUSES:
            raise ConflictError(
                f"Assignee cannot be changed once the task reached "
                f"'{task.status.value}'",
                code="ASSIGNEE_CHANGE_LOCKED",
            )
        if data["assignee_id"] is not None:
            await _assert_active_limit(
                db, data["assignee_id"], exclude_task_id=task.id
            )

    for field, value in data.items():
        setattr(task, field, value)

    return TaskRead.model_validate(await _reload_task(db, task.id))


async def delete_task(task_id: int, db: DbSession, _: CurrentUser) -> None:
    task = await _get_task(db, task_id)
    if task.status in UNDELETABLE_STATUSES:
        raise ConflictError(
            f"Task in status '{task.status.value}' cannot be deleted; "
            f"complete it (done) or cancel it first",
            code="TASK_DELETE_FORBIDDEN",
        )
    await db.execute(delete(Task).where(Task.id == task_id))


async def change_task_status(
    task_id: int,
    payload: TaskStatusUpdate,
    db: DbSession,
    current_user: CurrentUser,
    redis: RedisDep,
) -> TaskRead:
    task = await _get_task(db, task_id)
    new_status = payload.status
    old_status = task.status

    allowed = ALLOWED_TRANSITIONS[old_status]
    if new_status not in allowed:
        allowed_text = ", ".join(sorted(s.value for s in allowed)) or "nothing"
        raise ConflictError(
            f"Transition '{old_status.value}' -> '{new_status.value}' is not "
            f"allowed (allowed from '{old_status.value}': {allowed_text})",
            code="INVALID_STATUS_TRANSITION",
        )

    if new_status is TaskStatus.DONE:
        if task.assignee_id is None:
            raise ConflictError(
                "Task cannot be completed without an assignee",
                code="DONE_REQUIRES_ASSIGNEE",
            )
        if task.deadline is not None and task.deadline < datetime.now(timezone.utc):
            raise ConflictError(
                "Task deadline has already passed; it cannot be completed",
                code="DONE_DEADLINE_PASSED",
            )

    task.status = new_status
    _record_history(db, task, old_status, new_status, current_user.id)
    result = TaskRead.model_validate(await _reload_task(db, task.id))

    await db.commit()
    await _invalidate_analytics(redis)

    if new_status is TaskStatus.DONE:
        await _enqueue_quote(task.id)

    return result


async def _enqueue_quote(task_id: int) -> None:
    """Enqueue the Celery task without blocking the event loop; never fails the request."""
    from workers.quotes import fetch_quote_for_task

    try:
        await asyncio.to_thread(fetch_quote_for_task.delay, task_id)
        logger.info("Quote task enqueued for task %s", task_id)
    except Exception as exc:
        logger.warning("Failed to enqueue quote task for task %s: %r", task_id, exc)


async def get_task_history(
    task_id: int, db: DbSession, _: CurrentUser
) -> list[TaskHistoryRead]:
    await _get_task(db, task_id)
    rows = await db.scalars(
        select(TaskStatusHistory)
        .where(TaskStatusHistory.task_id == task_id)
        .options(selectinload(TaskStatusHistory.changed_by))
        .order_by(TaskStatusHistory.created_at, TaskStatusHistory.id)
    )
    return [TaskHistoryRead.model_validate(row) for row in rows]


def _apply_filters(stmt: Select, filters: TaskFilterParams) -> Select:
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        stmt = stmt.where(
            or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        )
    if filters.status:
        stmt = stmt.where(Task.status.in_(filters.status))
    if filters.priority:
        stmt = stmt.where(Task.priority.in_(filters.priority))
    if filters.assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == filters.assignee_id)
    if filters.unassigned:
        stmt = stmt.where(Task.assignee_id.is_(None))
    if filters.deadline_from is not None:
        stmt = stmt.where(Task.deadline >= filters.deadline_from)
    if filters.deadline_to is not None:
        stmt = stmt.where(Task.deadline <= filters.deadline_to)
    return stmt


def _apply_sorting(stmt: Select, filters: TaskFilterParams) -> Select:
    descending = filters.sort_order is SortOrder.DESC

    if filters.sort_by is None:
        return stmt.order_by(
            PRIORITY_RANK.desc(), Task.deadline.asc().nulls_last(), Task.id.desc()
        )

    column = {
        SortBy.CREATED_AT: Task.created_at,
        SortBy.DEADLINE: Task.deadline,
        SortBy.PRIORITY: PRIORITY_RANK,
    }[filters.sort_by]

    ordering = column.desc().nulls_last() if descending else column.asc().nulls_last()
    return stmt.order_by(ordering, Task.id.desc())


async def list_tasks(
    filters: FiltersDep, db: DbSession, _: CurrentUser
) -> TaskListResponse:
    base = _apply_filters(select(Task), filters)

    total = await db.scalar(
        _apply_filters(select(func.count()).select_from(Task), filters)
    ) or 0

    stmt = _apply_sorting(_with_relations(base), filters)
    stmt = stmt.limit(filters.page_size).offset(filters.offset)
    tasks: Sequence[Task] = (await db.scalars(stmt)).all()

    pages = (total + filters.page_size - 1) // filters.page_size
    return TaskListResponse(
        items=[TaskRead.model_validate(task) for task in tasks],
        total=total,
        page=filters.page,
        page_size=filters.page_size,
        pages=pages,
    )


async def get_analytics(
    db: DbSession, _: CurrentUser, redis: RedisDep
) -> AnalyticsResponse:
    settings = get_settings()

    try:
        cached = await redis.get(ANALYTICS_CACHE_KEY)
        if cached:
            return AnalyticsResponse.model_validate_json(cached)
    except Exception as exc:
        logger.warning("Analytics cache read failed: %r", exc)

    now = datetime.now(timezone.utc)

    by_status_rows = (
        await db.execute(select(Task.status, func.count()).group_by(Task.status))
    ).all()
    by_priority_rows = (
        await db.execute(select(Task.priority, func.count()).group_by(Task.priority))
    ).all()

    overdue = await db.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.deadline.is_not(None),
            Task.deadline < now,
            Task.status.not_in(FINAL_STATUSES),
        )
    )

    load_rows = (
        await db.execute(
            select(User.id, User.username, func.count(Task.id))
            .join(Task, Task.assignee_id == User.id)
            .where(Task.status.in_(ACTIVE_STATUSES))
            .group_by(User.id, User.username)
            .order_by(func.count(Task.id).desc())
        )
    ).all()

    avg_interval = await db.scalar(
        select(func.avg(TaskStatusHistory.created_at - Task.created_at))
        .select_from(TaskStatusHistory)
        .join(Task, Task.id == TaskStatusHistory.task_id)
        .where(TaskStatusHistory.to_status == TaskStatus.DONE)
    )

    response = AnalyticsResponse(
        total_tasks=sum(count for _, count in by_status_rows),
        by_status={status.value: count for status, count in by_status_rows},
        by_priority={priority.value: count for priority, count in by_priority_rows},
        overdue_tasks=overdue or 0,
        active_tasks_per_assignee=[
            AssigneeLoad(assignee_id=user_id, username=username, active_tasks=count)
            for user_id, username, count in load_rows
        ],
        avg_completion_hours=(
            round(avg_interval.total_seconds() / 3600, 2) if avg_interval else None
        ),
        generated_at=now,
    )

    try:
        await redis.set(
            ANALYTICS_CACHE_KEY,
            response.model_dump_json(),
            ex=settings.analytics_cache_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analytics cache write failed: %r", exc)

    return response


CSV_COLUMNS = [
    "id",
    "title",
    "description",
    "status",
    "priority",
    "deadline",
    "creator",
    "assignee",
    "created_at",
    "updated_at",
]


async def export_tasks(filters: FiltersDep, _: CurrentUser) -> StreamingResponse:
    """CSV export using the same filters as GET /tasks.

    Uses its own session instead of the request dependency: FastAPI closes
    yield-dependencies before the StreamingResponse body is fully consumed.
    """

    async def rows() -> AsyncIterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        def flush() -> str:
            value = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            return value

        writer.writerow(CSV_COLUMNS)
        yield flush()

        async with get_session() as session:
            stmt = _apply_sorting(
                _with_relations(_apply_filters(select(Task), filters)), filters
            )
            result = await session.stream_scalars(stmt.execution_options(yield_per=200))
            async for task in result:
                writer.writerow(
                    [
                        task.id,
                        task.title,
                        task.description or "",
                        task.status.value,
                        task.priority.value,
                        task.deadline.isoformat() if task.deadline else "",
                        task.creator.username if task.creator else "",
                        task.assignee.username if task.assignee else "",
                        task.created_at.isoformat(),
                        task.updated_at.isoformat(),
                    ]
                )
                yield flush()

    filename = f"tasks-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.csv"
    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
