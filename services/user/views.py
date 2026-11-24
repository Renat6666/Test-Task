"""All authentication endpoint logic. Routes live in controllers.py."""

from sqlalchemy import or_, select

from core.config import get_settings
from core.dependencies import CurrentUser, DbSession
from core.exceptions import AuthError, ConflictError
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from db.models import User
from services.user.schemas import (
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)


async def register(payload: UserCreate, db: DbSession) -> UserRead:
    existing = await db.scalar(
        select(User).where(
            or_(User.email == payload.email, User.username == payload.username)
        )
    )
    if existing is not None:
        field = "email" if existing.email == payload.email else "username"
        raise ConflictError(
            f"User with this {field} already exists", code="USER_ALREADY_EXISTS"
        )

    user = User(
        username=payload.username,
        email=payload.email,
        password=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserRead.model_validate(user)


async def login(payload: UserLogin, db: DbSession) -> TokenPair:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password):
        raise AuthError("Incorrect email or password", code="INVALID_CREDENTIALS")
    if not user.is_active:
        raise AuthError("User is inactive", code="USER_INACTIVE")

    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


async def refresh_tokens(payload: RefreshRequest, db: DbSession) -> TokenPair:
    try:
        token_payload = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = int(token_payload["sub"])
    except (ValueError, TypeError) as exc:
        raise AuthError(str(exc), code="INVALID_REFRESH_TOKEN") from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise AuthError("User is not available", code="INVALID_REFRESH_TOKEN")

    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


async def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


async def list_users(db: DbSession, _: CurrentUser) -> list[UserRead]:
    """User directory for picking an assignee; the technical System user is hidden."""
    settings = get_settings()
    users = await db.scalars(
        select(User)
        .where(User.email != settings.system_user_email)
        .order_by(User.username)
    )
    return [UserRead.model_validate(user) for user in users]
