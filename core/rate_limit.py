"""Redis-backed rate limiting.

Fixed-window algorithm: INCR + EXPIRE on the first increment, atomically in a
single Lua script, so the key can never be left without a TTL if the process
dies between the two commands. Counters live exclusively in Redis; there is no
in-memory fallback by design, because with several uvicorn workers a local
counter would enforce a wrong limit.
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from starlette.requests import Request

from core.config import get_settings
from core.dependencies import get_current_user
from core.exceptions import RateLimitError
from core.logging_config import get_logger
from core.redis_client import get_redis
from core.utils import get_client_ip
from db.models import User

logger = get_logger("app.rate_limit")

_LUA_FIXED_WINDOW = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


class RateLimiter:
    """FastAPI dependency. Wired in controllers.py, never inside views.py."""

    def __init__(self, *, limit: int, period_seconds: int, scope: str):
        self.limit = limit
        self.period_seconds = period_seconds
        self.scope = scope

    def _build_key(self, request: Request, user: User | None) -> str:
        if user is not None:
            return f"rl:{self.scope}:user:{user.id}"
        return f"rl:{self.scope}:ip:{get_client_ip(request)}"

    async def __call__(
        self,
        request: Request,
        redis: Annotated[Redis, Depends(get_redis)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> None:
        key = self._build_key(request, user)
        try:
            current, ttl = await redis.eval(
                _LUA_FIXED_WINDOW, 1, key, self.limit, self.period_seconds
            )
        except RateLimitError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Rate limiter unavailable (%s): %r", key, exc)
            return

        if int(current) > self.limit:
            retry_after = int(ttl) if int(ttl) > 0 else self.period_seconds
            logger.info("Rate limit exceeded for %s (%s/%s)", key, current, self.limit)
            raise RateLimitError(
                f"Rate limit exceeded: max {self.limit} requests per "
                f"{self.period_seconds} seconds",
                retry_after=retry_after,
            )


def comment_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(
        limit=settings.comment_rate_limit,
        period_seconds=settings.comment_rate_period_seconds,
        scope="comments",
    )
