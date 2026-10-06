# api/app/metrics/notification_metrics.py
"""
Prometheus metrics for notification system monitoring.
Tracks delivery success/failure, latency, queue size, and provider health.
"""

from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, Info

# ========================
# Counters (monotonically increasing)
# ========================

notifications_sent_total = Counter(
    "notifications_sent_total",
    "Total number of notifications sent",
    ["channel", "type", "status"],
)
"""
Track total notifications sent by channel (email/sms/push), type, and status.
Examples:
- notifications_sent_total{channel="push", type="transaction_created", status="success"}
- notifications_sent_total{channel="email", type="budget_exceeded", status="failed"}
"""

notifications_failed_total = Counter(
    "notifications_failed_total",
    "Total number of notification failures",
    ["channel", "error_type"],
)
"""
Track notification failures by channel and error type.
Examples:
- notifications_failed_total{channel="push", error_type="InvalidToken"}
- notifications_failed_total{channel="email", error_type="SendGridError"}
"""

notification_retries_total = Counter(
    "notification_retries_total",
    "Total number of notification retry attempts",
    ["channel", "notification_type"],
)
"""
Track retry attempts for failed notifications.
"""

celery_tasks_queued_total = Counter(
    "celery_tasks_queued_total", "Total number of Celery tasks queued", ["task_name"]
)
"""
Track Celery task queue operations.
"""

celery_tasks_completed_total = Counter(
    "celery_tasks_completed_total",
    "Total number of Celery tasks completed",
    ["task_name", "status"],
)
"""
Track Celery task completions (success/failed).
"""


# ========================
# Histograms (distribution of values)
# ========================

notification_delivery_duration_seconds = Histogram(
    "notification_delivery_duration_seconds",
    "Time taken to deliver notification (seconds)",
    ["channel"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)
"""
Track notification delivery latency by channel.
Buckets: 100ms, 500ms, 1s, 2s, 5s, 10s, 30s, 1min, 2min
"""

notification_payload_size_bytes = Histogram(
    "notification_payload_size_bytes",
    "Size of notification payload in bytes",
    ["channel"],
    buckets=[100, 500, 1000, 2000, 4000, 8000, 16000],
)
"""
Track notification payload sizes to detect oversized payloads.
FCM limit is 4KB, so we bucket around that.
"""

celery_task_duration_seconds = Histogram(
    "celery_task_duration_seconds",
    "Time taken to execute Celery task (seconds)",
    ["task_name"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)
"""
Track Celery task execution time.
"""


# ========================
# Gauges (current value that can go up/down)
# ========================

notification_queue_size = Gauge(
    "notification_queue_size", "Current size of notification queue"
)
"""
Track current notification queue size.
High values indicate backlog.
"""

fcm_active_tokens_total = Gauge(
    "fcm_active_tokens_total", "Total number of active FCM device tokens"
)
"""
Track registered FCM device tokens.
"""

sendgrid_api_quota_remaining = Gauge(
    "sendgrid_api_quota_remaining", "Remaining SendGrid API quota"
)
"""
Track SendGrid API usage to prevent quota exhaustion.
"""

twilio_sms_balance = Gauge("twilio_sms_balance", "Remaining Twilio SMS balance (USD)")
"""
Track Twilio account balance.
"""

circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["provider"],
)
"""
Track circuit breaker state for each notification provider.
0 = CLOSED (healthy)
1 = OPEN (failing, not sending)
2 = HALF_OPEN (testing recovery)
"""

notification_preferences_count = Gauge(
    "notification_preferences_count",
    "Number of users with custom notification preferences",
    ["preference_type"],
)
"""
Track adoption of notification preferences.
"""


# ========================
# Info (metadata labels)
# ========================

notification_system_info = Info(
    "notification_system", "Notification system configuration and version information"
)
"""
Static information about the notification system.
Example: version, providers enabled, etc.
"""


# ========================
# Helper Functions
# ========================


def record_notification_sent(
    channel: str,
    notification_type: str,
    success: bool,
    duration_seconds: float,
    payload_size_bytes: Optional[int] = None,
    error_type: Optional[str] = None,
):
    """
    Record a notification send attempt with all relevant metrics.

    Args:
        channel: Notification channel (email, sms, push)
        notification_type: Type of notification
        success: Whether notification was sent successfully
        duration_seconds: Time taken to send
        payload_size_bytes: Size of payload in bytes
        error_type: Type of error if failed
    """
    status = "success" if success else "failed"

    # Record counter
    notifications_sent_total.labels(
        channel=channel, type=notification_type, status=status
    ).inc()

    # Record duration
    notification_delivery_duration_seconds.labels(channel=channel).observe(
        duration_seconds
    )

    # Record payload size if provided
    if payload_size_bytes:
        notification_payload_size_bytes.labels(channel=channel).observe(
            payload_size_bytes
        )

    # Record failure details if failed
    if not success and error_type:
        notifications_failed_total.labels(channel=channel, error_type=error_type).inc()


def record_notification_retry(channel: str, notification_type: str):
    """Record a notification retry attempt."""
    notification_retries_total.labels(
        channel=channel, notification_type=notification_type
    ).inc()


def record_celery_task_queued(task_name: str):
    """Record a Celery task being queued."""
    celery_tasks_queued_total.labels(task_name=task_name).inc()


def record_celery_task_completed(
    task_name: str, success: bool, duration_seconds: float
):
    """Record a Celery task completion."""
    status = "success" if success else "failed"
    celery_tasks_completed_total.labels(task_name=task_name, status=status).inc()
    celery_task_duration_seconds.labels(task_name=task_name).observe(duration_seconds)


def update_queue_size(size: int):
    """Update notification queue size gauge."""
    notification_queue_size.set(size)


def update_fcm_token_count(count: int):
    """Update FCM active token count."""
    fcm_active_tokens_total.set(count)


def update_circuit_breaker_state(provider: str, state: int):
    """
    Update circuit breaker state for a provider.

    Args:
        provider: Provider name (fcm, sendgrid, twilio)
        state: 0 = CLOSED, 1 = OPEN, 2 = HALF_OPEN
    """
    circuit_breaker_state.labels(provider=provider).set(state)


def initialize_metrics():
    """Initialize static metrics and info."""
    from ..config.settings import settings

    notification_system_info.info(
        {
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "fcm_enabled": "true",
            "sendgrid_enabled": "true",
            "twilio_enabled": str(bool(settings.TWILIO_ACCOUNT_SID)),
        }
    )


# Initialize on module import
initialize_metrics()
