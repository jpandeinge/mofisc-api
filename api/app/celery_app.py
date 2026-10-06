# api/app/celery_app.py
"""
Celery application for asynchronous task processing.
Handles notification sending, scheduled tasks, and background operations.
"""

from celery import Celery
from celery.schedules import crontab

from .config.settings import settings
from .utils.logging_config import get_logger

logger = get_logger(__name__)

# Initialize Celery with Redis as broker and backend
celery_app = Celery(
    "galahad",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/2",
    include=["api.app.tasks.notification_tasks"],
)

# Configure Celery for production-grade performance
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Task execution
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
    task_soft_time_limit=240,  # 4 minutes soft limit (allows cleanup)
    task_acks_late=True,  # Retry on worker crash
    task_reject_on_worker_lost=True,
    # Performance
    worker_prefetch_multiplier=4,  # How many tasks to prefetch
    worker_max_tasks_per_child=1000,  # Restart worker after N tasks (prevent memory leaks)
    # Result backend
    result_expires=3600,  # Results expire after 1 hour
    result_backend_transport_options={
        "master_name": "mymaster",
        "retry_on_timeout": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
    # Broker connection
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 3600,  # 1 hour
        "retry_on_timeout": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
    # Task routes (future: route different task types to different queues)
    task_routes={
        "api.app.tasks.notification_tasks.*": {"queue": "notifications"},
    },
    # Beat schedule (for periodic tasks)
    beat_schedule={
        # Clean up old notifications every day at 2 AM UTC
        "cleanup-old-notifications": {
            "task": "api.app.tasks.notification_tasks.cleanup_old_notifications",
            "schedule": crontab(hour=2, minute=0),
            "options": {"queue": "notifications"},
        },
        # Subscription reminders - runs daily at 9:00 AM UTC
        # Notifies users of upcoming subscription payments (Revolut-style)
        "check-upcoming-subscriptions": {
            "task": "api.app.tasks.notification_tasks.check_upcoming_subscriptions",
            "schedule": crontab(hour=9, minute=0),
            "options": {"queue": "notifications"},
        },
        # Clean up inactive devices every week on Sunday at 3 AM UTC
        "cleanup-inactive-devices": {
            "task": "api.app.tasks.notification_tasks.cleanup_inactive_devices",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),
            "options": {"queue": "notifications"},
        },
    },
)

logger.info(
    "Celery app initialized",
    extra={
        "broker": f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
        "backend": f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/2",
        "environment": settings.ENVIRONMENT,
    },
)
