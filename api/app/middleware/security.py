# api/app/middleware/security.py

"""
Security middleware for the Galahad API.

Note: CORS is configured in main.py using settings.ALLOWED_ORIGINS.
This module only handles security headers.
"""

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        if "/api/redoc" in request.url.path:
            # Special CSP for ReDoc - allow blob: for Web Workers
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: https:; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net blob:; "
                "style-src 'self' 'unsafe-inline' https: https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "worker-src blob:; "  # Allow Web Workers from blob: URLs
                "connect-src 'self' https:; "
                "frame-ancestors 'none';"
            )
        elif "/api/docs" in request.url.path:
            # Permissive CSP for Swagger UI
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: https:; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https: https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "connect-src 'self' https:; "
                "frame-ancestors 'none';"
            )
        else:
            # Regular CSP for other pages
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: https:; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "frame-ancestors 'none';"
            )
        return response


def setup_security(app: FastAPI):
    """
    Setup security headers middleware.

    Note: CORS is configured separately in main.py using settings.ALLOWED_ORIGINS
    to allow environment-based configuration.
    """
    app.add_middleware(SecurityHeadersMiddleware)
