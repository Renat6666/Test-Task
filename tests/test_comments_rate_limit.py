"""Comments and rate limiting (counters in Redis)."""

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from core.config import get_settings
from tests.conftest import TEST_REDIS_URL

pytestmark = pytest.mark.asyncio

LIMIT = get_settings().comment_rate_limit


@pytest.fixture
async def task(client: AsyncClient, auth_headers: dict) -> dict:
    response = await client.post(
        "/tasks", json={"title": "commented task"}, headers=auth_headers
    )
    assert response.status_code == 201
    return response.json()


async def _post_comment(client: AsyncClient, headers: dict, task_id: int, text: str):
    return await client.post(
        f"/tasks/{task_id}/comments", json={"content": text}, headers=headers
    )


# --------------------------------------------------------------------------- #
#                                 Rate limit                                  #
# --------------------------------------------------------------------------- #

async def test_sixth_comment_returns_429(
    client: AsyncClient, auth_headers: dict, task: dict
) -> None:
    for index in range(LIMIT):
        response = await _post_comment(client, auth_headers, task["id"], f"c{index}")
        assert response.status_code == 201, f"comment {index + 1}: {response.text}"

    response = await _post_comment(client, auth_headers, task["id"], "one too many")

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(response.headers["Retry-After"]) > 0


async def test_rate_limit_counter_lives_in_redis(
    client: AsyncClient, auth_headers: dict, task: dict, user: dict
) -> None:
    await _post_comment(client, auth_headers, task["id"], "first")

    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        key = f"rl:comments:user:{user['id']}"
        assert await redis.get(key) == "1"
        assert 0 < await redis.ttl(key) <= get_settings().comment_rate_period_seconds
    finally:
        await redis.aclose()


async def test_limit_is_independent_per_user(
    client: AsyncClient,
    auth_headers: dict,
    other_auth_headers: dict,
    task: dict,
) -> None:
    for index in range(LIMIT):
        await _post_comment(client, auth_headers, task["id"], f"alice {index}")
    assert (
        await _post_comment(client, auth_headers, task["id"], "alice extra")
    ).status_code == 429

    response = await _post_comment(client, other_auth_headers, task["id"], "bob first")

    assert response.status_code == 201


async def test_window_expiry_restores_access(
    client: AsyncClient, auth_headers: dict, task: dict, user: dict
) -> None:
    for index in range(LIMIT):
        await _post_comment(client, auth_headers, task["id"], f"c{index}")
    assert (
        await _post_comment(client, auth_headers, task["id"], "blocked")
    ).status_code == 429

    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await redis.delete(f"rl:comments:user:{user['id']}")
    finally:
        await redis.aclose()

    response = await _post_comment(client, auth_headers, task["id"], "after window")

    assert response.status_code == 201


async def test_rate_limit_applies_before_business_logic(
    client: AsyncClient, auth_headers: dict, task: dict
) -> None:
    """The limit is a route dependency, so 429 wins even for a missing task."""
    for index in range(LIMIT):
        await _post_comment(client, auth_headers, task["id"], f"c{index}")

    response = await _post_comment(client, auth_headers, 999_999, "missing task")

    assert response.status_code == 429


async def test_get_comments_is_not_rate_limited(
    client: AsyncClient, auth_headers: dict, task: dict
) -> None:
    for index in range(LIMIT):
        await _post_comment(client, auth_headers, task["id"], f"c{index}")

    for _ in range(LIMIT + 3):
        response = await client.get(
            f"/tasks/{task['id']}/comments", headers=auth_headers
        )
        assert response.status_code == 200


async def test_create_comment_returns_author(
    client: AsyncClient, auth_headers: dict, task: dict, user: dict
) -> None:
    response = await _post_comment(client, auth_headers, task["id"], "hello")

    assert response.status_code == 201
    body = response.json()
    assert body["content"] == "hello"
    assert body["author"]["id"] == user["id"]
    assert body["is_system"] is False


async def test_comments_listed_chronologically_with_pagination(
    client: AsyncClient, auth_headers: dict, task: dict
) -> None:
    for index in range(LIMIT):
        await _post_comment(client, auth_headers, task["id"], f"c{index}")

    page = await client.get(
        f"/tasks/{task['id']}/comments?page=1&page_size=2", headers=auth_headers
    )

    assert page.status_code == 200
    body = page.json()
    assert body["total"] == LIMIT
    assert body["pages"] == (LIMIT + 1) // 2
    assert [item["content"] for item in body["items"]] == ["c0", "c1"]


async def test_blank_comment_rejected(
    client: AsyncClient, auth_headers: dict, task: dict
) -> None:
    response = await _post_comment(client, auth_headers, task["id"], "   ")

    assert response.status_code == 422


async def test_comments_of_missing_task_return_404(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await client.get("/tasks/999999/comments", headers=auth_headers)

    assert response.status_code == 404


async def test_comments_require_authentication(
    client: AsyncClient, task: dict
) -> None:
    assert (await client.get(f"/tasks/{task['id']}/comments")).status_code == 401
    assert (
        await client.post(f"/tasks/{task['id']}/comments", json={"content": "x"})
    ).status_code == 401
