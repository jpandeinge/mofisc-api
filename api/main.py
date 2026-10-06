# api/main.py

import asyncio
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi.errors import RateLimitExceeded

from .app.config.settings import settings
from .app.errors.exceptions import GalahadError
from .app.middleware.api_key_usage import ApiKeyUsageMiddleware
from .app.middleware.error_handler import ErrorHandlerMiddleware
from .app.middleware.rate_limiter import custom_rate_limit_handler, limiter
from .app.middleware.request_id import RequestIDMiddleware
from .app.middleware.security import setup_security
from .app.routes.achievement_routes import router as achievement_router
from .app.routes.api_key_routes import router as api_key_router
from .app.routes.budget_routes import router as budget_router
from .app.routes.calculator_routes import router as calculator_router
from .app.routes.device_routes import router as device_router
from .app.routes.goal_routes import router as goal_router
from .app.routes.notification_routes import router as notification_router
from .app.routes.organisation_routes import router as organisation_router
from .app.routes.platform_stats_routes import router as platform_stats_router
from .app.routes.subscription_management_routes import (
    router as subscription_management_router,
)
from .app.routes.subscription_routes import router as subscription_router
from .app.routes.transaction_edit_endpoints import router as transaction_edit_router
from .app.routes.transaction_routes import router as transaction_router
from .app.routes.user_achievement_metrics_routes import (
    router as user_achievement_metrics_router,
)
from .app.routes.user_metrics_routes import router as user_metrics_router
from .app.routes.user_routes import router as user_router
from .app.sentry import (
    SentryTransaction,
    init_sentry,
    set_sentry_context,
    set_sentry_tag,
)
from .app.utils.logging_config import configure_logging, get_logger
from .app.utils.transaction_model_endpoint import (
    require_transaction_model,
    transaction_model_required,
    transaction_model_status,
)

# Configure logging
configure_logging()
logger = get_logger(__name__)

# Initialize Sentry with reduced logging
init_sentry()
# Reduce logging verbosity for security and performance
logging.getLogger("sentry_sdk").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("fastapi").setLevel(logging.WARNING)

# Background task control
_retry_task: asyncio.Task | None = None
_subscription_reminder_task: asyncio.Task | None = None
RETRY_INTERVAL_SECONDS = 15 * 60  # 15 minutes
SUBSCRIPTION_CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours (once daily)


async def retry_failed_classifications_task():
    """
    Background task that periodically retries failed transaction classifications.

    Runs every 15 minutes and:
    1. Queries for transactions with "Processing...", "Classification Failed", "Uncategorized"
    2. Groups them by user_id and statement_id
    3. Triggers background classification for each group
    4. Sends wake-up request to HF endpoint before classification

    This runs as a long-lived asyncio task started on app startup.
    """
    from .app.utils.async_classification import classify_transactions_background
    from .app.utils.supabase_utils import get_supabase_client

    logger.info("Starting background retry task for failed classifications")

    # Wait 60 seconds before first run to let app fully start
    await asyncio.sleep(60)

    while True:
        try:
            logger.info("Running periodic classification retry check...")
            set_sentry_tag("periodic_retry", "true")

            # Get all failed transactions across all users
            supabase = get_supabase_client(use_service_role=True)

            # Use try-except for the query specifically to catch Supabase errors
            try:
                response = (
                    supabase.table(settings.SUPABASE_TABLE_NAME)
                    .select("id, user_id, statement_id, category, created_at")
                    .in_(
                        "category",
                        ["Processing...", "Classification Failed", "Uncategorized"],
                    )
                    .order("created_at", desc=False)
                    .limit(500)  # Limit to avoid timeout on large result sets
                    .execute()
                )
            except Exception as query_error:
                # Log specific query error and continue to next iteration
                logger.warning(
                    f"Periodic retry query failed (will retry next interval): {query_error}"
                )
                await asyncio.sleep(RETRY_INTERVAL_SECONDS)
                continue

            if not response.data:
                logger.info("No failed transactions found - all clear!")
            else:
                # Group by (user_id, statement_id)
                statements_to_retry = {}
                for transaction in response.data:
                    key = (transaction["user_id"], transaction["statement_id"])
                    if key not in statements_to_retry:
                        statements_to_retry[key] = []
                    statements_to_retry[key].append(transaction["id"])

                transaction_ids = [t["id"] for t in response.data]
                logger.info(
                    f"Found {len(transaction_ids)} failed transactions across "
                    f"{len(statements_to_retry)} statements - scheduling retry"
                )

                # Reset all transactions to "Processing..." before retry
                supabase.table(settings.SUPABASE_TABLE_NAME).update(
                    {"category": "Processing..."}
                ).in_("id", transaction_ids).execute()

                # Run classification for each statement (in sequence to avoid overload)
                for (user_id, statement_id), txn_ids in statements_to_retry.items():
                    try:
                        logger.info(
                            f"Retrying classification for user {user_id[:8]}..., "
                            f"statement {statement_id[:16]}... ({len(txn_ids)} transactions)"
                        )
                        # Run in thread pool to not block the event loop
                        await asyncio.get_event_loop().run_in_executor(
                            None,
                            classify_transactions_background,
                            user_id,
                            statement_id,
                            txn_ids,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to retry statement {statement_id[:16]}...: {e}"
                        )
                        sentry_sdk.capture_exception(e)

                logger.info(
                    f"Periodic retry completed: processed {len(transaction_ids)} transactions"
                )

                set_sentry_context(
                    "periodic_retry_stats",
                    {
                        "transactions_retried": len(transaction_ids),
                        "statements_processed": len(statements_to_retry),
                        "users_affected": len(
                            set(k[0] for k in statements_to_retry.keys())
                        ),
                    },
                )

        except Exception as e:
            logger.error(f"Periodic retry task error: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)

        # Wait for next interval
        logger.info(f"Next retry check in {RETRY_INTERVAL_SECONDS // 60} minutes")
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)


async def subscription_reminder_task():
    """
    Background task that periodically checks for upcoming subscription payments.

    Runs daily at startup then every 24 hours and:
    1. Queries all users' active subscriptions
    2. Calculates which subscriptions are due within 1 day (high priority) or 7 days
    3. Sends appropriate notifications via the notification service

    This runs as a long-lived asyncio task started on app startup.
    No Redis/Celery required - uses FastAPI's native async patterns.
    """
    from .app.services.subscriptions import subscription_reminder_service

    logger.info("Starting subscription reminder background task")

    # Wait 2 minutes before first run to let app fully start
    await asyncio.sleep(120)

    while True:
        try:
            logger.info("Running subscription reminder check...")
            set_sentry_tag("subscription_reminder", "true")

            # Run the subscription check
            result = await subscription_reminder_service.check_upcoming_subscriptions()

            logger.info(
                "Subscription reminder check completed",
                extra={
                    "users_notified": result.get("users_notified", 0),
                    "notifications_sent": result.get("total_notifications_sent", 0),
                    "subscriptions_checked": result.get("subscriptions_checked", 0),
                },
            )

            set_sentry_context(
                "subscription_reminder_stats",
                {
                    "users_notified": result.get("users_notified", 0),
                    "notifications_sent": result.get("total_notifications_sent", 0),
                    "subscriptions_checked": result.get("subscriptions_checked", 0),
                },
            )

        except Exception as e:
            logger.error(f"Subscription reminder task error: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)

        # Wait for next interval (24 hours)
        logger.info(
            f"Next subscription reminder check in {SUBSCRIPTION_CHECK_INTERVAL_SECONDS // 3600} hours"
        )
        await asyncio.sleep(SUBSCRIPTION_CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan event handler for startup and shutdown.

    On startup: Starts background tasks for:
    - Periodic retry of failed classifications
    - Subscription payment reminders (Revolut-style)

    On shutdown: Cancels all background tasks gracefully
    """
    global _retry_task, _subscription_reminder_task

    # Startup
    logger.info("Starting application lifespan - initializing background tasks")

    classification_status = (
        require_transaction_model()
        if transaction_model_required()
        else transaction_model_status()
    )
    app.state.transaction_classification = classification_status
    logger.info(
        "Transaction classification backend initialized",
        extra=classification_status,
    )

    _retry_task = asyncio.create_task(retry_failed_classifications_task())
    logger.info("Background retry task started")

    _subscription_reminder_task = asyncio.create_task(subscription_reminder_task())
    logger.info("Subscription reminder task started")

    yield  # App is running

    # Shutdown
    logger.info("Shutting down application lifespan - stopping background tasks")

    if _retry_task:
        _retry_task.cancel()
        try:
            await _retry_task
        except asyncio.CancelledError:
            logger.info("Background retry task cancelled successfully")

    if _subscription_reminder_task:
        _subscription_reminder_task.cancel()
        try:
            await _subscription_reminder_task
        except asyncio.CancelledError:
            logger.info("Subscription reminder task cancelled successfully")

    logger.info("Application shutdown complete")


app = FastAPI(
    title="Mofisc API",
    description="Financial analysis and transaction management API",
    version="1.0.0",
    lifespan=lifespan,  # Enables background retry task on startup
    docs_url="/api/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/api/redoc" if settings.ENVIRONMENT == "development" else None,
    openapi_url="/api/openapi.json" if settings.ENVIRONMENT == "development" else None,
    swagger_ui_parameters={
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": False,
        "defaultModelsExpandDepth": 3,
        "defaultModelExpandDepth": 3,
        "defaultModelRendering": "model",
        "displayRequestDuration": True,
        "docExpansion": "list",
        "filter": True,
        "showExtensions": True,
        "showCommonExtensions": True,
        "syntaxHighlight.theme": "monokai",
    },
)


# Rate limiting — bind the slowapi limiter and its exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, custom_rate_limit_handler)


# Exception Handlers
@app.exception_handler(GalahadError)
async def galahad_error_handler(request: Request, exc: GalahadError):
    """Handle custom application errors"""
    if settings.ENVIRONMENT == "development":
        set_sentry_context(
            "error_details",
            {
                "error_code": exc.error_code,
                "status_code": exc.status_code,
                "details": exc.details if hasattr(exc, "details") else None,
            },
        )
    else:
        # In production, only log error code and status
        set_sentry_context("error_code", exc.error_code)
        set_sentry_tag("status_code", exc.status_code)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": (
                exc.details
                if hasattr(exc, "details") and settings.ENVIRONMENT == "development"
                else None
            ),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors"""
    if settings.ENVIRONMENT == "development":
        set_sentry_context("validation_error", {"errors": str(exc.errors())})

    return JSONResponse(
        status_code=422,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": exc.errors() if settings.ENVIRONMENT == "development" else None,
        },
    )


# Middleware Setup (order matters: last added = first executed)
# 1. Request ID tracking (must be first to ensure ID available for all subsequent middleware)
app.add_middleware(RequestIDMiddleware)

# 2. API key usage tracking (after request ID, before error handling)
app.add_middleware(ApiKeyUsageMiddleware)

# 3. Error handling (uses request ID from previous middleware)
app.add_middleware(ErrorHandlerMiddleware, debug=settings.ENVIRONMENT == "development")

# Security middleware
setup_security(app)

# Trusted hosts middleware
if settings.ENVIRONMENT == "production":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health Check Endpoint
@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint for monitoring"""
    with SentryTransaction(name="health_check", op="monitoring"):
        return {
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
            "version": settings.APP_VERSION,
            "transaction_classification": getattr(
                request.app.state,
                "transaction_classification",
                {"ready": False, "backend": "uninitialized"},
            ),
        }


# Metrics Endpoint for Prometheus
@app.get("/metrics")
async def metrics(request: Request):
    """
    Prometheus metrics endpoint.

    Exposes application and notification metrics in Prometheus format.
    This endpoint should be scraped by Prometheus server.

    In production, consider restricting access to this endpoint.
    """
    if settings.ENVIRONMENT == "production":
        token = request.headers.get("X-Metrics-Token")
        if not settings.METRICS_TOKEN or token != settings.METRICS_TOKEN:
            return Response(status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Mount Routes
app.include_router(transaction_router, prefix="/api/v1", tags=["transactions"])
app.include_router(api_key_router, prefix="/api/v1/api-keys", tags=["api-keys"])
app.include_router(
    transaction_edit_router, prefix="/api/v1", tags=["transaction-edits"]
)
app.include_router(user_router, prefix="/api/v1/users", tags=["users"])
app.include_router(goal_router, prefix="/api/v1/goals", tags=["goals"])
app.include_router(budget_router, prefix="/api/v1/budgets", tags=["budgets"])
app.include_router(
    calculator_router, prefix="/api/v1/calculators", tags=["calculators"]
)
app.include_router(
    achievement_router, prefix="/api/v1/achievements", tags=["achievements"]
)
app.include_router(user_metrics_router, prefix="/api/v1", tags=["metrics"])
app.include_router(
    user_achievement_metrics_router, prefix="/api/v1", tags=["user-metrics"]
)
app.include_router(
    notification_router, prefix="/api/v1/notifications", tags=["notifications"]
)
app.include_router(
    organisation_router, prefix="/api/v1/organisations", tags=["organisations"]
)
app.include_router(
    subscription_router, prefix="/api/v1/subscriptions", tags=["subscriptions"]
)
app.include_router(
    subscription_management_router, prefix="/api/v1", tags=["subscription-management"]
)
app.include_router(
    platform_stats_router, prefix="/api/v1/public", tags=["platform-stats"]
)
app.include_router(device_router, prefix="/api/v1/security", tags=["security"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",  # nosec B104 - Intentionally binding to all interfaces for containerized deployment
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development",
        workers=1 if settings.ENVIRONMENT == "development" else None,
    )
