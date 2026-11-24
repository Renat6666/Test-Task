"""Registration and login (JWT)."""

from datetime import timedelta

import pytest
from httpx import AsyncClient

from core.security import _create_token, hash_password, verify_password

pytestmark = pytest.mark.asyncio


async def test_register_creates_user(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "username": "newbie",
            "email": "newbie@example.com",
            "password": "StrongPass123",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "newbie"
    assert body["email"] == "newbie@example.com"
    assert body["is_active"] is True
    assert "password" not in body


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {
        "username": "first",
        "email": "dup@example.com",
        "password": "StrongPass123",
    }
    assert (await client.post("/auth/register", json=payload)).status_code == 201

    payload["username"] = "second"
    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_ALREADY_EXISTS"


async def test_register_duplicate_username_conflicts(client: AsyncClient) -> None:
    await client.post(
        "/auth/register",
        json={"username": "twin", "email": "a@example.com", "password": "StrongPass123"},
    )
    response = await client.post(
        "/auth/register",
        json={"username": "twin", "email": "b@example.com", "password": "StrongPass123"},
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "email": "x@example.com", "password": "StrongPass123"},
        {"username": "ok", "email": "not-an-email", "password": "StrongPass123"},
        {"username": "ok", "email": "x@example.com", "password": "short"},
    ],
)
async def test_register_validation_errors(client: AsyncClient, payload: dict) -> None:
    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


async def test_password_is_hashed_not_stored_plain(
    client: AsyncClient, db_session
) -> None:
    from sqlalchemy import select

    from db.models import User

    await client.post(
        "/auth/register",
        json={
            "username": "hashed",
            "email": "hashed@example.com",
            "password": "StrongPass123",
        },
    )
    stored = await db_session.scalar(
        select(User).where(User.email == "hashed@example.com")
    )

    assert stored.password != "StrongPass123"
    assert stored.password.startswith("$2b$")
    assert verify_password("StrongPass123", stored.password)


async def test_login_returns_token_pair(client: AsyncClient, user: dict) -> None:
    response = await client.post(
        "/auth/login", json={"email": user["email"], "password": "StrongPass123"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]


async def test_login_wrong_password_unauthorized(
    client: AsyncClient, user: dict
) -> None:
    response = await client.post(
        "/auth/login", json={"email": user["email"], "password": "WrongPass123"}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


async def test_login_unknown_email_unauthorized(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "StrongPass123"}
    )

    assert response.status_code == 401


async def test_me_requires_token(client: AsyncClient) -> None:
    response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_me_returns_current_user(
    client: AsyncClient, user: dict, auth_headers: dict
) -> None:
    response = await client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["id"] == user["id"]


async def test_malformed_token_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )

    assert response.status_code == 401


async def test_expired_token_rejected(client: AsyncClient, user: dict) -> None:
    expired = _create_token(user["id"], "access", timedelta(minutes=-5))

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401
    assert "expired" in response.json()["detail"]["message"].lower()


async def test_refresh_token_cannot_be_used_as_access(
    client: AsyncClient, user: dict
) -> None:
    tokens = (
        await client.post(
            "/auth/login", json={"email": user["email"], "password": "StrongPass123"}
        )
    ).json()

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )

    assert response.status_code == 401


async def test_refresh_endpoint_issues_new_access_token(
    client: AsyncClient, user: dict
) -> None:
    tokens = (
        await client.post(
            "/auth/login", json={"email": user["email"], "password": "StrongPass123"}
        )
    ).json()

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert response.status_code == 200
    new_access = response.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_tasks_endpoints_require_authentication(client: AsyncClient) -> None:
    for method, url in [
        ("GET", "/tasks"),
        ("POST", "/tasks"),
        ("GET", "/tasks/1"),
        ("PATCH", "/tasks/1/status"),
        ("GET", "/tasks/1/comments"),
        ("POST", "/tasks/1/comments"),
    ]:
        response = await client.request(method, url, json={})
        assert response.status_code == 401, f"{method} {url}"


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_long_password_does_not_break_bcrypt() -> None:
    long_password = "x" * 200
    assert verify_password(long_password, hash_password(long_password))
