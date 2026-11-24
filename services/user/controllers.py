"""Routes of the user service. All logic lives in views.py."""

from fastapi import APIRouter, status

from services.user import views
from services.user.schemas import TokenPair, UserRead

auth_router = APIRouter(prefix="/auth", tags=["auth"])

auth_router.add_api_route(
    "/register",
    views.register,
    methods=["POST"],
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
auth_router.add_api_route(
    "/login",
    views.login,
    methods=["POST"],
    response_model=TokenPair,
    summary="Log in and issue a JWT pair",
)
auth_router.add_api_route(
    "/refresh",
    views.refresh_tokens,
    methods=["POST"],
    response_model=TokenPair,
    summary="Refresh the token pair",
)
auth_router.add_api_route(
    "/me",
    views.me,
    methods=["GET"],
    response_model=UserRead,
    summary="Current user",
)

users_router = APIRouter(prefix="/users", tags=["users"])

users_router.add_api_route(
    "",
    views.list_users,
    methods=["GET"],
    response_model=list[UserRead],
    summary="User directory",
)
