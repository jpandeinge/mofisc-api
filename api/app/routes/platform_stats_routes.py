# api/app/routes/platform_stats_routes.py

"""
Platform statistics endpoint with security hardening.

Security measures:
- Rate limiting (10 req/min per IP)
- No sensitive data exposure
- Cached responses (10 minutes)
- Sanitized error messages
- Security headers
- Request logging for monitoring
"""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ..config.settings import settings
from ..middleware.rate_limiter import limiter
from ..services.platform_stats.platform_stats_service import PlatformStatsService
from ..utils.auth import get_api_key_user
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


async def authorize_platform_stats(request: Request):
    """
    Authorize platform stats access.

    Priority:
    1) Public token for anonymized landing-page usage (if configured)
    2) Regular API key auth (scoped + plan-validated)
    """
    public_token = request.headers.get("X-Public-Token")
    if settings.PUBLIC_API_TOKEN and public_token:
        if hmac.compare_digest(public_token, settings.PUBLIC_API_TOKEN):
            return {"auth_type": "public_token"}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid public token",
        )

    return await get_api_key_user(request)


@router.get("/platform-stats")
@limiter.limit("10/minute")  # Aggressive rate limiting for public endpoint
async def get_platform_stats(
    request: Request, _authorized=Depends(authorize_platform_stats)
):
    """
    Get anonymized platform statistics.

    **Auth:** Requires a valid API key (Enterprise)
    **Rate Limit:** 10 requests per minute per IP
    **Cache:** Results cached server-side for 10 minutes

    **Security Features:**
    - Only aggregated, anonymized data (no PII)
    - No financial amounts or transaction details exposed
    - No user identifiers or personal data
    - Server-side caching prevents database DoS
    - Request logging for security monitoring
    - Sanitized error messages

    **Returns:**
    Anonymized platform metrics:
    - `total_users`: Count of registered users
    - `total_transactions`: Count of processed transactions
    - `average_user_rating`: Calculated engagement score (4.5-4.9)
    - `active_users_30d`: Count of active users (last 30 days)
    - `uptime_percentage`: System availability percentage
    - `total_merchants`: Count of unique merchants
    - `total_categories`: Count of transaction categories
    - `new_users_this_month`: Count of new users this month
    - `last_updated`: ISO timestamp

    **Example Response:**
    ```json
    {
        "total_users": 1234,
        "total_transactions": 45678,
        "average_user_rating": 4.7,
        "active_users_30d": 890,
        "uptime_percentage": 99.9,
        "total_merchants": 567,
        "total_categories": 12,
        "new_users_this_month": 89,
        "last_updated": "2024-01-30T12:34:56.789Z"
    }
    ```

    **Privacy & Security:**
    This endpoint is designed with Revolut-level security standards:
    - ✓ No individual user data exposed
    - ✓ No transaction amounts or financial details
    - ✓ No merchant names or category names returned
    - ✓ All data is aggregated and anonymized
    - ✓ Rate limited to prevent abuse
    - ✓ Cached to prevent database load
    """
    try:
        # Log request for security monitoring
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        logger.info(
            "Platform stats request",
            extra={"ip": client_ip, "user_agent": user_agent[:100]},  # Limit UA length
        )

        # Get stats from service (with built-in caching)
        stats_service = PlatformStatsService()
        stats = await stats_service.get_platform_statistics()

        # Create response with security headers
        response = JSONResponse(content=stats)

        # Security headers (Revolut-style)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "public, max-age=600"  # 10 min cache
        response.headers["Vary"] = "Accept-Encoding"

        return response

    except Exception as e:
        # SECURITY: Never expose internal error details to public
        logger.error(
            "Platform stats endpoint error",
            exc_info=True,
            extra={
                "ip": request.client.host if request.client else "unknown",
                "error_type": type(e).__name__,
            },
        )

        # Return generic error message (no internal details)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Service temporarily unavailable",
                "message": "Unable to retrieve statistics. Please try again later.",
            },
        )
