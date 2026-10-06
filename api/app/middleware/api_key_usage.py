# api/app/middleware/api_key_usage.py

import asyncio
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..services.api_keys import ApiKeyService
from ..utils.api_scopes import resolve_required_scope, sanitize_path
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class ApiKeyUsageMiddleware(BaseHTTPMiddleware):
    """Track API key usage for analytics and audit."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        status_code = 500
        response: Optional[Response] = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            api_key_id: Optional[str] = getattr(request.state, "api_key_id", None)
            user_id: Optional[str] = getattr(request.state, "api_key_user_id", None)
            if api_key_id and user_id:
                duration_ms = int((time.perf_counter() - start) * 1000)
                path = sanitize_path(request.url.path)
                endpoint_group = None

                scope = resolve_required_scope(request.url.path, request.method)
                if scope:
                    endpoint_group = scope.split(":", 1)[0]

                ip_address = request.client.host if request.client else None
                user_agent = request.headers.get("user-agent")

                service = ApiKeyService()

                async def _log_usage():
                    try:
                        await asyncio.to_thread(
                            service.log_usage,
                            api_key_id=api_key_id,
                            user_id=user_id,
                            request_path=path,
                            request_method=request.method,
                            endpoint_group=endpoint_group,
                            status_code=status_code,
                            response_time_ms=duration_ms,
                            ip_address=ip_address,
                            user_agent=user_agent,
                        )
                    except Exception as exc:
                        logger.warning("Failed to log API key usage")
                        logger.debug(str(exc))

                asyncio.create_task(_log_usage())
