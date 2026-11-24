"""Routes of the comment service. All logic lives in views.py."""

from fastapi import APIRouter, Depends, status

from core.dependencies import get_current_user
from core.rate_limit import comment_rate_limiter
from services.comments import views
from services.comments.schemas import CommentListResponse, CommentRead

router = APIRouter(
    prefix="/tasks/{task_id}/comments",
    tags=["comments"],
    dependencies=[Depends(get_current_user)],
)

router.add_api_route(
    "",
    views.list_comments,
    methods=["GET"],
    response_model=CommentListResponse,
    summary="Comments of a task",
)
router.add_api_route(
    "",
    views.create_comment,
    methods=["POST"],
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(comment_rate_limiter())],
    summary="Create a comment (rate limited)",
)
