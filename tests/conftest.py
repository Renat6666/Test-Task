"""Test fixtures.

Async test database: DATABASE_URL is replaced with TEST_DATABASE_URL BEFORE the
application is imported, so both the app and the tests use the same test database.
"""

import os
from typing import AsyncIterator, Callable

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/tasks_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["DEBUG"] = "false"
# -----------------------------------------------------------------------------

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from db.database import SessionLocal, engine  # noqa: E402
from db.models import Base  # noqa: E402
from main import app  # noqa: E402

TABLES = ["task_status_history", "comments", "tasks", "users"]


@pytest.fixture(scope="session", autouse=True)
async def prepare_database() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_state() -> AsyncIterator[None]:
    """Clean database and clean Redis before every test."""
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )
    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()
    yield


@pytest.fixture(autouse=True)
def no_celery_broker(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The Celery task never reaches a real broker; calls are recorded instead."""
    from workers.quotes import fetch_quote_for_task

    enqueued: list[int] = []
    monkeypatch.setattr(
        fetch_quote_for_task, "delay", lambda task_id: enqueued.append(task_id)
    )
    return enqueued


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
        await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


DEFAULT_PASSWORD = "StrongPass123"


@pytest.fixture
def register_user(client: AsyncClient) -> Callable:
    async def _register(username: str, email: str | None = None) -> dict:
        response = await client.post(
            "/auth/register",
            json={
                "username": username,
                "email": email or f"{username}@example.com",
                "password": DEFAULT_PASSWORD,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _register


@pytest.fixture
def login_user(client: AsyncClient) -> Callable:
    async def _login(email: str, password: str = DEFAULT_PASSWORD) -> str:
        response = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    return _login


@pytest.fixture
async def user(register_user: Callable) -> dict:
    return await register_user("alice")


@pytest.fixture
async def other_user(register_user: Callable) -> dict:
    return await register_user("bob")


@pytest.fixture
async def auth_headers(user: dict, login_user: Callable) -> dict[str, str]:
    token = await login_user(user["email"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def other_auth_headers(other_user: dict, login_user: Callable) -> dict[str, str]:
    token = await login_user(other_user["email"])
    return {"Authorization": f"Bearer {token}"}
