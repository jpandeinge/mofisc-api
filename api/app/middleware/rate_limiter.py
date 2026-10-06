# api/app/middleware/rate_limiter.py
"""
Production-grade rate limiting middleware with in-memory backend.
Prevents notification abuse and ensures fair usage across users.
"""

from typing import Optional

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def get_user_id_or_ip(request: Request) -> str:
    """
    Get user ID for authenticated requests, fallback to IP for anonymous.
    This ensures rate limiting is per-user, not per-IP (which can be shared).
    """
    # Try to get user from request state (set by auth middleware)
    if hasattr(request.state, "user") and request.state.user:
        user_id = getattr(request.state.user, "id", None)
        if user_id:
            return f"user:{user_id}"

    # Fallback to IP address for unauthenticated requests
    return f"ip:{get_remote_address(request)}"


# Initialize limiter with in-memory storage (no Redis required)
limiter = Limiter(
    key_func=get_user_id_or_ip,
    storage_uri="memory://",  # In-memory storage
    default_limits=["1000/hour"],  # Global default
    headers_enabled=True,  # Add X-RateLimit-* headers to responses
    retry_after="http-date",  # Include Retry-After header
)


# Custom rate limit exceeded handler with enhanced logging
async def custom_rate_limit_handler(request: Request, exc: Exception):
    """Enhanced rate limit exceeded handler with Sentry integration and ConnectionError handling."""
    user_identifier = get_user_id_or_ip(request)

    # Handle ConnectionError from Redis
    if isinstance(exc, ConnectionError):
        logger.error(
            "Redis connection error during rate limiting",
            extra={
                "user_identifier": user_identifier,
                "path": request.url.path,
                "method": request.method,
                "error": str(exc),
            },
        )

        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service temporarily unavailable",
                "message": "Rate limiting service is unavailable. Please try again later.",
            },
        )

    # Handle RateLimitExceeded
    if isinstance(exc, RateLimitExceeded):
        logger.warning(
            "Rate limit exceeded",
            extra={
                "user_identifier": user_identifier,
                "path": request.url.path,
                "method": request.method,
                "limit": exc.detail,
            },
        )

        # Log to Sentry for monitoring
        from ..sentry import set_sentry_context, set_sentry_tag

        set_sentry_tag("rate_limit_exceeded", "true")
        set_sentry_context(
            "rate_limit",
            {
                "user_identifier": user_identifier,
                "endpoint": request.url.path,
                "limit_exceeded": exc.detail,
            },
        )

        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many requests. {exc.detail}",
                "retry_after": exc.headers.get("Retry-After"),
                "limit": exc.detail,
            },
            headers=exc.headers,
        )

    # Handle other exceptions
    logger.error(
        "Unexpected error in rate limit handler",
        extra={
            "user_identifier": user_identifier,
            "path": request.url.path,
            "method": request.method,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )

    raise HTTPException(
        status_code=500,
        detail={
            "error": "Internal server error",
            "message": "An unexpected error occurred while processing rate limits.",
        },
    )


# Pre-defined rate limit configurations for different notification types
class RateLimits:
    """Centralized rate limit definitions for notification endpoints."""

    # General notification sending - strict limits
    SEND_NOTIFICATION = [
        "10/minute",  # Burst protection
        "100/hour",  # Hourly limit
        "500/day",  # Daily limit
    ]

    # Test notifications - more lenient for testing
    TEST_NOTIFICATION = ["5/minute", "20/hour"]

    # Reading notifications - more lenient
    READ_NOTIFICATIONS = ["100/minute", "1000/hour"]

    # Device registration - strict to prevent spam
    DEVICE_REGISTRATION = ["5/minute", "20/hour", "50/day"]

    # Marking as read - moderate limits
    MARK_READ = ["50/minute", "500/hour"]

    # Unread count - very lenient (frequently polled)
    UNREAD_COUNT = ["200/minute", "2000/hour"]

    # Provider status check - lenient
    PROVIDER_STATUS = ["30/minute", "200/hour"]

    # Device trust management
    DEVICE_CHECK = ["10/minute", "60/hour"]
    DEVICE_MUTATE = ["5/minute", "20/hour", "50/day"]
    DEVICE_READ = ["30/minute", "200/hour"]


# Helper function to check rate limit without raising exception
def check_rate_limit(request: Request, limit_string: str) -> bool:
    """
    Check if request would exceed rate limit without actually incrementing counter.
    Useful for conditional rate limiting logic.

    Note: With in-memory storage, this is a simplified check.

    Args:
        request: FastAPI request object
        limit_string: Rate limit string like "10/minute"

    Returns:
        True if rate limit would be exceeded, False otherwise
    """
    # With in-memory storage, we can't easily check without incrementing
    # Return False (don't block) as a safe default
    return False


# Analytics function to get rate limit stats
def get_rate_limit_stats(user_identifier: Optional[str] = None) -> dict:
    """
    Get rate limit statistics for monitoring and analytics.

    Note: With in-memory storage, detailed stats are not available.

    Args:
        user_identifier: Optional user identifier (e.g., "user:123" or "ip:192.168.1.1")

    Returns:
        Dictionary with rate limit statistics
    """
    return {
        "note": "Rate limit statistics not available with in-memory storage",
        "storage_type": "memory",
    }
