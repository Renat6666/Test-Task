"""Background task: a quote posted as a system comment when a task reaches Done.

The Celery worker is synchronous, so the task body runs its own event loop via
`asyncio.run`. Because of that the engine is created per run and with NullPool:
a connection pool bound to an already finished loop would be unusable in the
next task.
"""

import asyncio
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.logging_config import get_logger, setup_logging
from db.models import Comment, Task
from services.comments.views import get_or_create_system_user
from workers.celery_app import celery_app

setup_logging(get_settings().debug)
logger = get_logger("app.workers.quotes")

MAX_RETRIES = 3


class QuoteUnavailable(Exception):
    """The external service responded but carried no usable payload."""


def _parse_quote(data: Any) -> tuple[str, str]:
    """Supports both DummyJSON ({quote, author}) and ZenQuotes ([{q, a}])."""
    if isinstance(data, list):
        if not data:
            raise QuoteUnavailable("Empty quotes payload")
        data = data[0]
    if not isinstance(data, dict):
        raise QuoteUnavailable(f"Unexpected payload type: {type(data).__name__}")

    text = data.get("quote") or data.get("q") or data.get("content")
    author = data.get("author") or data.get("a") or "Unknown"
    if not text:
        raise QuoteUnavailable("Payload has no quote text")
    return str(text).strip(), str(author).strip()


async def _fetch_quote() -> tuple[str, str]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.quotes_api_timeout_seconds) as client:
        response = await client.get(settings.quotes_api_url)
        response.raise_for_status()
        return _parse_quote(response.json())


async def _store_quote(task_id: int, text: str, author: str) -> int | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            task_exists = await session.scalar(select(Task.id).where(Task.id == task_id))
            if task_exists is None:
                logger.warning("Task %s disappeared before the quote was stored", task_id)
                return None

            system_user = await get_or_create_system_user(session)
            comment = Comment(
                content=f'"{text}" — {author}',
                task_id=task_id,
                author_id=system_user.id,
                is_system=True,
            )
            session.add(comment)
            await session.commit()
            logger.info("System quote comment %s added to task %s", comment.id, task_id)
            return comment.id
    finally:
        await engine.dispose()


async def _run(task_id: int) -> dict[str, Any]:
    text, author = await _fetch_quote()
    comment_id = await _store_quote(task_id, text, author)
    return {"status": "ok", "task_id": task_id, "comment_id": comment_id}


@celery_app.task(
    bind=True,
    name="quotes.fetch_for_task",
    max_retries=MAX_RETRIES,
)
def fetch_quote_for_task(self, task_id: int) -> dict[str, Any]:
    """Fail-safe: an unavailable external API is not a task-API failure —
    once retries are exhausted we log a WARNING and finish successfully.
    """
    try:
        return asyncio.run(_run(task_id))
    except (httpx.HTTPError, QuoteUnavailable, ValueError) as exc:
        if self.request.retries < MAX_RETRIES:
            delay = 2 ** self.request.retries
            logger.warning(
                "Quotes API call failed for task %s (attempt %s/%s): %r; retry in %ss",
                task_id,
                self.request.retries + 1,
                MAX_RETRIES,
                exc,
                delay,
            )
            raise self.retry(exc=exc, countdown=delay)

        logger.warning(
            "Quotes API unavailable after %s attempts; task %s stays without a quote: %r",
            MAX_RETRIES,
            task_id,
            exc,
        )
        return {"status": "skipped", "task_id": task_id, "reason": repr(exc)}
