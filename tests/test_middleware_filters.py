"""Performance logging middleware plus search/filters/sorting/pagination,
analytics, export and the fail-safe quotes integration."""

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.logging_config import PERFORMANCE_LOGGER
from core.middleware import TimingMiddleware

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#                                 Middleware                                  #
# --------------------------------------------------------------------------- #

def _app_with_timing(threshold_ms: int, delay: float) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TimingMiddleware, threshold_ms=threshold_ms)

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(delay)
        return {"ok": True}

    return app


async def test_process_time_header_present(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await client.get("/health")

    assert "X-Process-Time" in response.headers
    assert float(response.headers["X-Process-Time"]) >= 0


@contextmanager
def _propagating_performance_logger():
    """caplog listens on root while application loggers have propagate=False
    (see core/logging_config), so for the test we re-enable propagation along the
    whole app.performance -> app -> root chain."""
    chain = [logging.getLogger(PERFORMANCE_LOGGER), logging.getLogger("app")]
    saved = [logger.propagate for logger in chain]
    for logger in chain:
        logger.propagate = True
    try:
        yield
    finally:
        for logger, value in zip(chain, saved):
            logger.propagate = value


async def test_slow_request_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    app = _app_with_timing(threshold_ms=10, delay=0.05)

    with _propagating_performance_logger():
        with caplog.at_level(logging.WARNING, logger=PERFORMANCE_LOGGER):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as local_client:
                response = await local_client.get(
                    "/slow", headers={"X-Forwarded-For": "203.0.113.7"}
                )

    assert response.status_code == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "SLOW REQUEST" in message
    assert "method=GET" in message
    assert "/slow" in message
    assert "client_ip=203.0.113.7" in message
    assert "duration_ms=" in message
    duration = float(message.split("duration_ms=")[1].split()[0])
    assert duration > 10


async def test_fast_request_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app_with_timing(threshold_ms=5000, delay=0.0)

    with _propagating_performance_logger():
        with caplog.at_level(logging.WARNING, logger=PERFORMANCE_LOGGER):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as local_client:
                await local_client.get("/slow")

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


@pytest.fixture
async def dataset(client: AsyncClient, auth_headers: dict, user: dict, other_user: dict):
    """Six tasks with different priorities, deadlines and assignees."""
    specs = [
        ("Fix login bug", "auth breaks on refresh", "high", 5, user["id"]),
        ("Urgent hotfix", "payment gateway down", "high", 1, user["id"]),
        ("Write docs", "describe deploy process", "low", 10, other_user["id"]),
        ("Refactor parser", "login module cleanup", "medium", 3, other_user["id"]),
        ("Plan sprint", None, "medium", None, None),
        ("Cleanup logs", "rotate nginx logs", "low", 2, None),
    ]
    created = []
    for title, description, priority, days, assignee_id in specs:
        payload = {"title": title, "priority": priority}
        if description:
            payload["description"] = description
        if days:
            payload["deadline"] = (NOW + timedelta(days=days)).isoformat()
        if assignee_id:
            payload["assignee_id"] = assignee_id
        response = await client.post("/tasks", json=payload, headers=auth_headers)
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


async def test_search_matches_title_and_description(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get("/tasks?search=login", headers=auth_headers)

    assert response.status_code == 200
    titles = {item["title"] for item in response.json()["items"]}
    assert titles == {"Fix login bug", "Refactor parser"}


async def test_search_is_case_insensitive(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get("/tasks?search=URGENT", headers=auth_headers)

    assert [item["title"] for item in response.json()["items"]] == ["Urgent hotfix"]


async def test_filter_by_status(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    await client.patch(
        f"/tasks/{dataset[0]['id']}/status",
        json={"status": "in_progress"},
        headers=auth_headers,
    )

    response = await client.get("/tasks?status=in_progress", headers=auth_headers)

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == dataset[0]["id"]


async def test_filter_by_multiple_statuses(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    await client.patch(
        f"/tasks/{dataset[0]['id']}/status",
        json={"status": "cancelled"},
        headers=auth_headers,
    )

    response = await client.get(
        "/tasks?status=backlog&status=cancelled", headers=auth_headers
    )

    assert response.json()["total"] == len(dataset)


async def test_filter_by_priority(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get("/tasks?priority=high", headers=auth_headers)

    body = response.json()
    assert body["total"] == 2
    assert all(item["priority"] == "high" for item in body["items"])


async def test_filter_by_assignee(
    client: AsyncClient, auth_headers: dict, dataset: list, user: dict
) -> None:
    response = await client.get(
        f"/tasks?assignee_id={user['id']}", headers=auth_headers
    )

    body = response.json()
    assert body["total"] == 2
    assert all(item["assignee"]["id"] == user["id"] for item in body["items"])


async def test_filter_unassigned(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get("/tasks?unassigned=true", headers=auth_headers)

    body = response.json()
    assert body["total"] == 2
    assert all(item["assignee"] is None for item in body["items"])


async def test_filter_by_deadline_range(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get(
        "/tasks",
        params={
            "deadline_from": (NOW + timedelta(days=2)).isoformat(),
            "deadline_to": (NOW + timedelta(days=6)).isoformat(),
        },
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    titles = {item["title"] for item in response.json()["items"]}
    assert titles == {"Cleanup logs", "Refactor parser", "Fix login bug"}


async def test_invalid_deadline_range_rejected(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await client.get(
        "/tasks",
        params={
            "deadline_from": (NOW + timedelta(days=5)).isoformat(),
            "deadline_to": (NOW + timedelta(days=1)).isoformat(),
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


async def test_default_sorting_priority_then_nearest_deadline(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get("/tasks?page_size=100", headers=auth_headers)

    items = response.json()["items"]
    priorities = [item["priority"] for item in items]
    assert priorities == ["high", "high", "medium", "medium", "low", "low"]
    assert [item["title"] for item in items[:2]] == ["Urgent hotfix", "Fix login bug"]
    assert [item["title"] for item in items[2:4]] == ["Refactor parser", "Plan sprint"]


async def test_sort_by_deadline_ascending(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get(
        "/tasks?sort_by=deadline&sort_order=asc&page_size=100", headers=auth_headers
    )

    deadlines = [item["deadline"] for item in response.json()["items"] if item["deadline"]]
    assert deadlines == sorted(deadlines)


async def test_sort_by_created_at_desc(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get(
        "/tasks?sort_by=created_at&sort_order=desc&page_size=100", headers=auth_headers
    )

    ids = [item["id"] for item in response.json()["items"]]
    assert ids == sorted(ids, reverse=True)


async def test_pagination_splits_results(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    first = await client.get("/tasks?page=1&page_size=4", headers=auth_headers)
    second = await client.get("/tasks?page=2&page_size=4", headers=auth_headers)

    first_body, second_body = first.json(), second.json()
    assert first_body["total"] == second_body["total"] == len(dataset)
    assert first_body["pages"] == 2
    assert len(first_body["items"]) == 4
    assert len(second_body["items"]) == 2
    assert not {i["id"] for i in first_body["items"]} & {
        i["id"] for i in second_body["items"]
    }


async def test_pagination_bounds_validated(
    client: AsyncClient, auth_headers: dict
) -> None:
    assert (await client.get("/tasks?page=0", headers=auth_headers)).status_code == 422
    assert (
        await client.get("/tasks?page_size=500", headers=auth_headers)
    ).status_code == 422


async def test_unknown_query_param_rejected(
    client: AsyncClient, auth_headers: dict
) -> None:
    response = await client.get("/tasks?stattus=done", headers=auth_headers)

    assert response.status_code == 422


async def test_analytics_aggregates(
    client: AsyncClient, auth_headers: dict, dataset: list, user: dict
) -> None:
    await client.patch(
        f"/tasks/{dataset[0]['id']}/status",
        json={"status": "in_progress"},
        headers=auth_headers,
    )

    response = await client.get("/tasks/analytics", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total_tasks"] == len(dataset)
    assert body["by_status"]["in_progress"] == 1
    assert body["by_priority"]["high"] == 2
    assert body["overdue_tasks"] == 0
    load = {row["assignee_id"]: row["active_tasks"] for row in body["active_tasks_per_assignee"]}
    assert load[user["id"]] == 2


async def test_analytics_cache_invalidated_on_status_change(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    before = (await client.get("/tasks/analytics", headers=auth_headers)).json()
    assert before["by_status"].get("in_progress") is None

    await client.patch(
        f"/tasks/{dataset[1]['id']}/status",
        json={"status": "in_progress"},
        headers=auth_headers,
    )
    after = (await client.get("/tasks/analytics", headers=auth_headers)).json()

    assert after["by_status"]["in_progress"] == 1


async def test_openapi_schema_generates(client: AsyncClient) -> None:
    """Regression: response_class=None on /tasks/export broke /openapi.json and /docs."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200, response.text
    schema = response.json()
    assert "/tasks/export" in schema["paths"]
    assert "text/csv" in schema["paths"]["/tasks/export"]["get"]["responses"]["200"]["content"]
    for path in ("/auth/login", "/tasks", "/tasks/{task_id}/status", "/tasks/analytics"):
        assert path in schema["paths"], path


async def test_docs_page_available(client: AsyncClient) -> None:
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "swagger" in response.text.lower()


async def test_export_returns_csv_with_filters(
    client: AsyncClient, auth_headers: dict, dataset: list
) -> None:
    response = await client.get("/tasks/export?priority=high", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = [line for line in response.text.splitlines() if line.strip()]
    assert lines[0].startswith("id,title,description,status,priority")
    assert len(lines) == 3
    assert "Urgent hotfix" in response.text


async def test_quote_comment_added_from_external_api(
    client: AsyncClient, auth_headers: dict, user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker task runs directly (no broker) and adds a system comment
    authored by System."""
    import workers.quotes as quotes_module

    async def fake_fetch() -> tuple[str, str]:
        return "Done is better than perfect", "Sheryl Sandberg"

    monkeypatch.setattr(quotes_module, "_fetch_quote", fake_fetch)

    task = (
        await client.post(
            "/tasks", json={"title": "quoted", "assignee_id": user["id"]}, headers=auth_headers
        )
    ).json()
    for status in ("in_progress", "review", "done"):
        await client.patch(
            f"/tasks/{task['id']}/status", json={"status": status}, headers=auth_headers
        )

    result = await quotes_module._run(task["id"])

    assert result["status"] == "ok"
    comments = (
        await client.get(f"/tasks/{task['id']}/comments", headers=auth_headers)
    ).json()["items"]
    assert len(comments) == 1
    assert comments[0]["is_system"] is True
    assert comments[0]["author"]["username"] == "System"
    assert "Done is better than perfect" in comments[0]["content"]


async def test_external_api_failure_does_not_break_done_transition(
    client: AsyncClient, auth_headers: dict, user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workers.quotes as quotes_module

    async def failing_fetch() -> tuple[str, str]:
        raise httpx.ConnectTimeout("quotes service is down")

    monkeypatch.setattr(quotes_module, "_fetch_quote", failing_fetch)

    task = (
        await client.post(
            "/tasks", json={"title": "no quote", "assignee_id": user["id"]}, headers=auth_headers
        )
    ).json()
    for status in ("in_progress", "review"):
        await client.patch(
            f"/tasks/{task['id']}/status", json={"status": status}, headers=auth_headers
        )

    response = await client.patch(
        f"/tasks/{task['id']}/status", json={"status": "done"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    with pytest.raises(httpx.ConnectTimeout):
        await quotes_module._run(task["id"])
    comments = (
        await client.get(f"/tasks/{task['id']}/comments", headers=auth_headers)
    ).json()
    assert comments["total"] == 0


async def test_quote_parser_supports_both_api_shapes() -> None:
    from workers.quotes import QuoteUnavailable, _parse_quote

    assert _parse_quote({"quote": "A", "author": "B"}) == ("A", "B")
    assert _parse_quote([{"q": "C", "a": "D"}]) == ("C", "D")
    with pytest.raises(QuoteUnavailable):
        _parse_quote([])
    with pytest.raises(QuoteUnavailable):
        _parse_quote({"author": "no text"})
