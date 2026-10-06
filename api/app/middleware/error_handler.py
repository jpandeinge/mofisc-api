# api/app/middleware/error_handler.py

import logging

import sentry_sdk
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..config.settings import settings
from ..sentry import SentryTransaction, set_sentry_context
from .request_id import get_request_id

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, debug: bool = False):
        super().__init__(app)
        self.debug = debug

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = get_request_id() or "unknown"
        request_path = request.url.path

        try:
            with SentryTransaction(
                name=f"HTTP {request.method} {request_path}", op="http.server"
            ):
                # Add request context
                set_sentry_context(
                    "request",
                    {
                        "method": request.method,
                        "path": request_path,
                        "request_id": request_id,
                    },
                )

                response = await call_next(request)

                # Add response context for non-success responses
                if response.status_code >= 400:
                    set_sentry_context(
                        "response",
                        {"status_code": response.status_code, "request_id": request_id},
                    )

                return response

        except RequestValidationError as e:
            # Don't log validation details in production
            if settings.ENVIRONMENT == "production":
                logger.warning(
                    "Validation error occurred", extra={"request_id": request_id}
                )
            else:
                logger.warning(
                    f"Validation error: {str(e)}", extra={"request_id": request_id}
                )

            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "error": "Validation Error",
                    "detail": (
                        "Invalid request data"
                        if settings.ENVIRONMENT == "production"
                        else str(e)
                    ),
                    "request_id": request_id,
                },
            )

        except SQLAlchemyError as e:
            # Log error type and sanitized info even in production for debugging
            error_type = type(e).__name__
            error_msg = (
                str(e).split("\n")[0][:200] if str(e) else "Unknown error"
            )  # First line, max 200 chars

            logger.error(
                f"Database error [{error_type}]: {error_msg}",
                extra={
                    "request_id": request_id,
                    "error_type": error_type,
                    "path": request.url.path,
                },
            )

            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "Database Error", "request_id": request_id},
            )

        except Exception as e:
            # Log error type and message for debugging
            error_type = type(e).__name__
            error_msg = str(e)[:500] if str(e) else "No error message"
            logger.error(
                f"Unexpected error [{error_type}]: {error_msg}",
                extra={
                    "request_id": request_id,
                    "error_type": error_type,
                    "path": request.url.path,
                },
                exc_info=True,  # Include full traceback
            )

            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "Internal Server Error", "request_id": request_id},
            )
