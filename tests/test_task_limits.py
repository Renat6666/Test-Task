"""Task business rules: active-task limit, final statuses, assignee locking,
deletion rules and deadlines in the past."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from core.config import get_settings
from db.models import Task

pytestmark = pytest.mark.asyncio

FUTURE = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
LIMIT = get_settings().max_active_tasks_per_user


async def _create(client: AsyncClient, headers: dict, **payload):
    body = {"title": "task", **payload}
    return await client.post("/tasks", json=body, headers=headers)


async def _fill_active_tasks(
    client: AsyncClient, headers: dict, assignee_id: int, count: int
) -> list[int]:
    ids = []
    for index in range(count):
        response = await _create(
            client, headers, title=f"active {index}", assignee_id=assignee_id
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


async def test_ten_active_tasks_allowed(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    ids = await _fill_active_tasks(client, auth_headers, user["id"], LIMIT)

    assert len(ids) == LIMIT


async def test_eleventh_active_task_rejected(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    await _fill_active_tasks(client, auth_headers, user["id"], LIMIT)

    response = await _create(client, auth_headers, title="overflow", assignee_id=user["id"])

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "ACTIVE_TASK_LIMIT_EXCEEDED"


async def test_limit_counts_only_active_statuses(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    ids = await _fill_active_tasks(client, auth_headers, user["id"], LIMIT)

    cancelled = await client.patch(
        f"/tasks/{ids[0]}/status", json={"status": "cancelled"}, headers=auth_headers
    )
    assert cancelled.status_code == 200

    response = await _create(client, auth_headers, title="after free slot", assignee_id=user["id"])

    assert response.status_code == 201, response.text


async def test_done_tasks_do_not_count_towards_limit(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    ids = await _fill_active_tasks(client, auth_headers, user["id"], LIMIT)
    for status in ("in_progress", "review", "done"):
        response = await client.patch(
            f"/tasks/{ids[0]}/status", json={"status": status}, headers=auth_headers
        )
        assert response.status_code == 200, response.text

    response = await _create(client, auth_headers, title="extra", assignee_id=user["id"])

    assert response.status_code == 201


async def test_limit_is_per_user(
    client: AsyncClient, auth_headers: dict, user: dict, other_user: dict
) -> None:
    await _fill_active_tasks(client, auth_headers, user["id"], LIMIT)

    response = await _create(client, auth_headers, title="for bob", assignee_id=other_user["id"])

    assert response.status_code == 201


async def test_reassign_blocked_when_target_is_full(
    client: AsyncClient, auth_headers: dict, user: dict, other_user: dict
) -> None:
    await _fill_active_tasks(client, auth_headers, other_user["id"], LIMIT)
    task = (await _create(client, auth_headers, title="mine", assignee_id=user["id"])).json()

    response = await client.patch(
        f"/tasks/{task['id']}", json={"assignee_id": other_user["id"]}, headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ACTIVE_TASK_LIMIT_EXCEEDED"


async def test_task_at_limit_can_still_be_edited(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    """A task counted towards the limit must not block editing itself."""
    ids = await _fill_active_tasks(client, auth_headers, user["id"], LIMIT)

    response = await client.patch(
        f"/tasks/{ids[-1]}",
        json={"assignee_id": user["id"], "title": "renamed"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "renamed"


async def test_unknown_assignee_returns_404(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await _create(client, auth_headers, title="ghost", assignee_id=999_999)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ASSIGNEE_NOT_FOUND"


async def test_past_deadline_rejected_on_create(
    client: AsyncClient, auth_headers: dict
) -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    response = await _create(client, auth_headers, deadline=past)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


async def test_past_deadline_rejected_on_update(
    client: AsyncClient, auth_headers: dict
) -> None:
    task = (await _create(client, auth_headers, deadline=FUTURE)).json()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    response = await client.patch(
        f"/tasks/{task['id']}", json={"deadline": past}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_future_deadline_accepted(client: AsyncClient, auth_headers: dict) -> None:
    response = await _create(client, auth_headers, deadline=FUTURE)

    assert response.status_code == 201
    assert response.json()["deadline"] is not None


@pytest.mark.parametrize("final_status", ["done", "cancelled"])
async def test_final_task_cannot_be_edited(
    client: AsyncClient, auth_headers: dict, user: dict, final_status: str
) -> None:
    task = (await _create(client, auth_headers, assignee_id=user["id"], deadline=FUTURE)).json()
    path = ["in_progress", "review", "done"] if final_status == "done" else ["cancelled"]
    for status in path:
        assert (
            await client.patch(
                f"/tasks/{task['id']}/status", json={"status": status}, headers=auth_headers
            )
        ).status_code == 200

    response = await client.patch(
        f"/tasks/{task['id']}", json={"title": "new title"}, headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_IS_FINAL"


async def test_assignee_locked_in_review(
    client: AsyncClient, auth_headers: dict, user: dict, other_user: dict
) -> None:
    task = (await _create(client, auth_headers, assignee_id=user["id"])).json()
    for status in ("in_progress", "review"):
        await client.patch(
            f"/tasks/{task['id']}/status", json={"status": status}, headers=auth_headers
        )

    response = await client.patch(
        f"/tasks/{task['id']}", json={"assignee_id": other_user["id"]}, headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ASSIGNEE_CHANGE_LOCKED"


async def test_other_fields_editable_in_review(
    client: AsyncClient, auth_headers: dict, user: dict
) -> None:
    task = (await _create(client, auth_headers, assignee_id=user["id"])).json()
    for status in ("in_progress", "review"):
        await client.patch(
            f"/tasks/{task['id']}/status", json={"status": status}, headers=auth_headers
        )

    response = await client.patch(
        f"/tasks/{task['id']}", json={"title": "still editable"}, headers=auth_headers
    )

    assert response.status_code == 200


async def test_assignee_change_allowed_before_review(
    client: AsyncClient, auth_headers: dict, user: dict, other_user: dict
) -> None:
    task = (await _create(client, auth_headers, assignee_id=user["id"])).json()
    await client.patch(
        f"/tasks/{task['id']}/status", json={"status": "in_progress"}, headers=auth_headers
    )

    response = await client.patch(
        f"/tasks/{task['id']}", json={"assignee_id": other_user["id"]}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["assignee"]["id"] == other_user["id"]


@pytest.mark.parametrize("status", ["in_progress", "review"])
async def test_delete_forbidden_for_in_progress_and_review(
    client: AsyncClient, auth_headers: dict, user: dict, status: str
) -> None:
    task = (await _create(client, auth_headers, assignee_id=user["id"])).json()
    path = ["in_progress"] if status == "in_progress" else ["in_progress", "review"]
    for step in path:
        await client.patch(
            f"/tasks/{task['id']}/status", json={"status": step}, headers=auth_headers
        )

    response = await client.delete(f"/tasks/{task['id']}", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_DELETE_FORBIDDEN"


@pytest.mark.parametrize("status", ["backlog", "cancelled", "done"])
async def test_delete_allowed_for_backlog_and_final(
    client: AsyncClient, auth_headers: dict, user: dict, db_session, status: str
) -> None:
    task = (await _create(client, auth_headers, assignee_id=user["id"])).json()
    path = {
        "backlog": [],
        "cancelled": ["cancelled"],
        "done": ["in_progress", "review", "done"],
    }[status]
    for step in path:
        await client.patch(
            f"/tasks/{task['id']}/status", json={"status": step}, headers=auth_headers
        )

    response = await client.delete(f"/tasks/{task['id']}", headers=auth_headers)

    assert response.status_code == 204
    assert await db_session.scalar(select(Task).where(Task.id == task["id"])) is None


async def test_delete_cascades_comments_and_history(
    client: AsyncClient, auth_headers: dict, db_session
) -> None:
    from db.models import Comment, TaskStatusHistory

    task = (await _create(client, auth_headers)).json()
    await client.post(
        f"/tasks/{task['id']}/comments", json={"content": "bye"}, headers=auth_headers
    )

    assert (await client.delete(f"/tasks/{task['id']}", headers=auth_headers)).status_code == 204

    comments = (
        await db_session.scalars(select(Comment).where(Comment.task_id == task["id"]))
    ).all()
    history = (
        await db_session.scalars(
            select(TaskStatusHistory).where(TaskStatusHistory.task_id == task["id"])
        )
    ).all()
    assert comments == []
    assert history == []


async def test_delete_missing_task_returns_404(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await client.delete("/tasks/999999", headers=auth_headers)

    assert response.status_code == 404
