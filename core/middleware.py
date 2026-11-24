"""Request performance logging middleware."""

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from core.logging_config import PERFORMANCE_LOGGER, get_logger
from core.utils import get_client_ip

logger = get_logger(PERFORMANCE_LOGGER)


class TimingMiddleware(BaseHTTPMiddleware):
    """Measure every request; log the ones slower than threshold_ms as WARNING."""

    def __init__(self, app, threshold_ms: int = 500):
        super().__init__(app)
        self.threshold_ms = threshold_ms

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._log(request, elapsed_ms, status_code=500)
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}"
        self._log(request, elapsed_ms, status_code=response.status_code)
        return response

    def _log(self, request: Request, elapsed_ms: float, status_code: int) -> None:
        message = (
            f"method={request.method} "
            f"url={request.url} "
            f"client_ip={get_client_ip(request)} "
            f"status_code={status_code} "
            f"duration_ms={elapsed_ms:.2f}"
        )
        if elapsed_ms > self.threshold_ms:
            logger.warning("SLOW REQUEST (>%dms): %s", self.threshold_ms, message)
        else:
            logger.info(message)
