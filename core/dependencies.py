"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AuthError
from core.security import decode_token
from db.database import get_db
from db.models import User

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthError("Authorization header is missing")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        user_id = int(payload["sub"])
    except (ValueError, TypeError) as exc:
        raise AuthError(str(exc)) from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise AuthError("User no longer exists")
    if not user.is_active:
        raise AuthError("User is inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
