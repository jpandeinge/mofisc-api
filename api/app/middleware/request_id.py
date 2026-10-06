# api/app/middleware/request_id.py

import logging
import uuid
from contextvars import ContextVar
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

request_id_context: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_request_id() -> Optional[str]:
    """
    Get the current request ID from context.

    Returns:
        Request ID string if available, None otherwise
    """
    return request_id_context.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds a unique request ID to each incoming request.

    The request ID can be:
    1. Provided by client via X-Request-ID header (for request tracing across services)
    2. Auto-generated if not provided

    The request ID is:
    - Added to response headers as X-Request-ID
    - Set in context for access throughout request lifecycle
    - Logged with all log messages for correlation
    - Sent to Sentry for error tracking

    Usage in routes/services:
        from ..middleware.request_id import get_request_id

        request_id = get_request_id()
        logger.info(f"Processing request {request_id}")
    """

    HEADER_NAME = "X-Request-ID"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """
        Process request and inject request ID.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/route handler

        Returns:
            Response with X-Request-ID header
        """
        request_id = request.headers.get(self.HEADER_NAME)

        if not request_id:
            request_id = str(uuid.uuid4())

        request_id_context.set(request_id)

        request.state.request_id = request_id

        logger.debug(
            f"[{request_id}] {request.method} {request.url.path}",
            extra={"request_id": request_id},
        )

        response = await call_next(request)

        response.headers[self.HEADER_NAME] = request_id

        return response


class RequestIDLogFilter(logging.Filter):
    """
    Logging filter that adds request_id to all log records.

    This ensures all logs within a request context include the request ID,
    making it easy to trace all logs related to a specific request.

    Usage in logging configuration:
        import logging
        from ..middleware.request_id import RequestIDLogFilter

        logger = logging.getLogger(__name__)
        logger.addFilter(RequestIDLogFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Add request_id to log record if available.

        Args:
            record: Log record to modify

        Returns:
            Always True (don't filter out any records)
        """
        request_id = get_request_id()
        record.request_id = request_id if request_id else "-"
        return True
