"""Routes of the task service. All logic lives in views.py.

Registration order matters: the static paths (/analytics, /export) must be
declared before /{task_id}, otherwise FastAPI tries to parse "analytics" as an
int and returns 422.
"""

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from core.dependencies import get_current_user
from services.tasks import views
from services.tasks.schemas import (
    AnalyticsResponse,
    TaskHistoryRead,
    TaskListResponse,
    TaskRead,
)

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
)

router.add_api_route(
    "/analytics",
    views.get_analytics,
    methods=["GET"],
    response_model=AnalyticsResponse,
    summary="Aggregated analytics (cached in Redis)",
)
router.add_api_route(
    "/export",
    views.export_tasks,
    methods=["GET"],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV file of tasks"}},
    summary="CSV export using the same filters as GET /tasks",
)

router.add_api_route(
    "",
    views.list_tasks,
    methods=["GET"],
    response_model=TaskListResponse,
    summary="List tasks: search, filters, sorting, pagination",
)
router.add_api_route(
    "",
    views.create_task,
    methods=["POST"],
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
)

router.add_api_route(
    "/{task_id}",
    views.get_task,
    methods=["GET"],
    response_model=TaskRead,
    summary="Task details",
)
router.add_api_route(
    "/{task_id}",
    views.update_task,
    methods=["PATCH"],
    response_model=TaskRead,
    summary="Update a task (status excluded)",
)
router.add_api_route(
    "/{task_id}",
    views.delete_task,
    methods=["DELETE"],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
)

router.add_api_route(
    "/{task_id}/status",
    views.change_task_status,
    methods=["PATCH"],
    response_model=TaskRead,
    summary="Change status (dedicated endpoint)",
)
router.add_api_route(
    "/{task_id}/history",
    views.get_task_history,
    methods=["GET"],
    response_model=list[TaskHistoryRead],
    summary="Status transition history",
)
