from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.exceptions import register_exception_handlers
from core.logging_config import get_logger, setup_logging
from core.middleware import TimingMiddleware
from core.redis_client import close_redis_pool, redis_client
from db.database import dispose_engine
from services.comments.controllers import router as comments_router
from services.tasks.controllers import router as tasks_router
from services.user.controllers import auth_router, users_router

settings = get_settings()
setup_logging(settings.debug)
logger = get_logger("app.startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    client = redis_client()
    try:
        await client.ping()
        logger.info("Redis connection established")
    except Exception as exc:
        logger.warning("Redis is unavailable at startup: %r", exc)
    finally:
        await client.aclose()

    yield

    await close_redis_pool()
    await dispose_engine()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=settings.cors_origins_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time"],
)
app.add_middleware(TimingMiddleware, threshold_ms=settings.slow_request_threshold_ms)

register_exception_handlers(app)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(tasks_router)
app.include_router(comments_router)


@app.get("/health", tags=["service"])
async def health_check():
    return {"status": "healthy"}
