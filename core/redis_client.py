"""Single Redis connection pool (cache + rate limiting)."""

from typing import AsyncIterator

from redis.asyncio import ConnectionPool, Redis

from core.config import get_settings

_pool: ConnectionPool | None = None


def get_redis_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            get_settings().redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return _pool


def redis_client() -> Redis:
    """New connection from the pool. Used outside FastAPI (worker, scripts)."""
    return Redis(connection_pool=get_redis_pool())


async def get_redis() -> AsyncIterator[Redis]:
    """FastAPI dependency."""
    client = redis_client()
    try:
        yield client
    finally:
        await client.aclose()


async def close_redis_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
