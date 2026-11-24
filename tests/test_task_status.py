"""Status transition validation: allowed transitions vs forbidden rollbacks."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from db.models import Task, TaskStatus

pytestmark = pytest.mark.asyncio

FUTURE = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()


async def _create_task(
    client: AsyncClient, headers: dict, *, assignee_id: int | None = None, **extra
) -> dict:
    payload = {"title": "task", "deadline": FUTURE, **extra}
    if assignee_id is not None:
        payload["assignee_id"] = assignee_id
    response = await client.post("/tasks", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _set_status(client: AsyncClient, headers: dict, task_id: int, status: str):
    return await client.patch(
        f"/tasks/{task_id}/status", json={"status": status}, headers=headers
    )


async def _advance(client: AsyncClient, headers: dict, task_id: int, *statuses: str):
    for status in statuses:
        response = await _set_status(client, headers, task_id, status)
        assert response.status_code == 200, response.text
    return response


async def test_new_task_starts_in_backlog(
    client: AsyncClient, auth_headers: dict
) -> None:
    task = await _create_task(client, auth_headers)

    assert task["status"] == "backlog"


@pytest.mark.parametrize(
    "path, target",
    [
        ([], "in_progress"),
        (["in_progress"], "review"),
        ([], "cancelled"),
        (["in_progress"], "cancelled"),
    ],
)
async def test_allowed_transitions(
    client: AsyncClient, auth_headers: dict, user: dict, path: list[str], target: str
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    if path:
        await _advance(client, auth_headers, task["id"], *path)

    response = await _set_status(client, auth_headers, task["id"], target)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == target


async def test_review_to_done_allowed(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    await _advance(client, auth_headers, task["id"], "in_progress", "review")

    response = await _set_status(client, auth_headers, task["id"], "done")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "done"


@pytest.mark.parametrize(
    "path, target",
    [
        (["in_progress", "review"], "in_progress"),
        (["in_progress", "review"], "backlog"),
        (["in_progress"], "backlog"),
        ([], "review"),
        ([], "done"),
        (["in_progress"], "done"),
        (["in_progress", "review"], "cancelled"),
    ],
)
async def test_forbidden_transitions(
    client: AsyncClient, auth_headers: dict, user: dict, path: list[str], target: str
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    if path:
        await _advance(client, auth_headers, task["id"], *path)

    response = await _set_status(client, auth_headers, task["id"], target)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATUS_TRANSITION"


async def test_done_is_terminal(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    await _advance(client, auth_headers, task["id"], "in_progress", "review", "done")

    for target in ("backlog", "in_progress", "review", "cancelled"):
        response = await _set_status(client, auth_headers, task["id"], target)
        assert response.status_code == 409, target


async def test_cancelled_is_terminal(
    client: AsyncClient, auth_headers: dict
) -> None:
    task = await _create_task(client, auth_headers)
    await _advance(client, auth_headers, task["id"], "cancelled")

    response = await _set_status(client, auth_headers, task["id"], "in_progress")

    assert response.status_code == 409


async def test_same_status_transition_rejected(
    client: AsyncClient, auth_headers: dict
) -> None:
    task = await _create_task(client, auth_headers)

    response = await _set_status(client, auth_headers, task["id"], "backlog")

    assert response.status_code == 409


async def test_unknown_status_value_rejected(
    client: AsyncClient, auth_headers: dict
) -> None:
    task = await _create_task(client, auth_headers)

    response = await _set_status(client, auth_headers, task["id"], "archived")

    assert response.status_code == 422


async def test_done_requires_assignee(
    client: AsyncClient, auth_headers: dict
) -> None:
    task = await _create_task(client, auth_headers)
    await _advance(client, auth_headers, task["id"], "in_progress", "review")

    response = await _set_status(client, auth_headers, task["id"], "done")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DONE_REQUIRES_ASSIGNEE"


async def test_done_rejected_when_deadline_passed(
    client: AsyncClient, auth_headers: dict, user: dict, db_session
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    await _advance(client, auth_headers, task["id"], "in_progress", "review")

    await db_session.execute(
        update(Task)
        .where(Task.id == task["id"])
        .values(deadline=datetime.now(timezone.utc) - timedelta(hours=1))
    )
    await db_session.commit()

    response = await _set_status(client, auth_headers, task["id"], "done")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DONE_DEADLINE_PASSED"


async def test_done_without_deadline_allowed(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    response = await client.post(
        "/tasks", json={"title": "no deadline", "assignee_id": user["id"]}, headers=auth_headers
    )
    task = response.json()
    await _advance(client, auth_headers, task["id"], "in_progress", "review")

    result = await _set_status(client, auth_headers, task["id"], "done")

    assert result.status_code == 200


async def test_history_records_every_transition(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    await _advance(client, auth_headers, task["id"], "in_progress", "review", "done")

    history = await client.get(f"/tasks/{task['id']}/history", headers=auth_headers)

    assert history.status_code == 200
    transitions = [(row["from_status"], row["to_status"]) for row in history.json()]
    assert transitions == [
        (None, "backlog"),
        ("backlog", "in_progress"),
        ("in_progress", "review"),
        ("review", "done"),
    ]
    assert all(row["changed_by"]["id"] == user["id"] for row in history.json())


async def test_failed_transition_is_not_persisted(
    client: AsyncClient, auth_headers: dict, db_session
) -> None:
    task = await _create_task(client, auth_headers)
    await _set_status(client, auth_headers, task["id"], "done")  # 409

    stored = await db_session.scalar(select(Task).where(Task.id == task["id"]))

    assert stored.status is TaskStatus.BACKLOG


async def test_quote_task_enqueued_only_on_done(
    client: AsyncClient, auth_headers: dict, user: dict, no_celery_broker: list[int]
) -> None:
    task = await _create_task(client, auth_headers, assignee_id=user["id"])
    await _advance(client, auth_headers, task["id"], "in_progress", "review")

    assert no_celery_broker == []

    await _advance(client, auth_headers, task["id"], "done")

    assert no_celery_broker == [task["id"]]


async def test_status_change_of_missing_task_returns_404(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await _set_status(client, auth_headers, 999_999, "in_progress")

    assert response.status_code == 404
