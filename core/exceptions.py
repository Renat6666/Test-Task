"""Domain exceptions and their HTTP representation.

The error body format is the same across the whole API:
    {"detail": {"code": "MACHINE_CODE", "message": "human readable text"}}
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base domain exception."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "APP_ERROR"

    def __init__(self, message: str, *, code: str | None = None, headers: dict | None = None):
        self.message = message
        if code:
            self.code = code
        self.headers = headers or {}
        super().__init__(message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(AppError):
    """Business rule violation."""

    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"

    def __init__(self, message: str = "Could not validate credentials", **kwargs):
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMIT_EXCEEDED"

    def __init__(self, message: str, *, retry_after: int):
        super().__init__(message, headers={"Retry-After": str(retry_after)})
        self.retry_after = retry_after


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "errors": [
                        {"field": ".".join(str(part) for part in err["loc"][1:]), "message": err["msg"]}
                        for err in exc.errors()
                    ],
                }
            },
        )
