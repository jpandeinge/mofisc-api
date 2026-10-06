# api/app/tasks/notification_tasks.py
"""
Celery tasks for asynchronous notification processing.
Handles notification sending, retry logic, and cleanup operations.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from celery import Task

from ..celery_app import celery_app
from ..sentry import set_sentry_context, set_sentry_tag
from ..services.notifications.base import (
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)
from ..services.notifications.notification_service import notification_service
from ..utils.logging_config import get_logger
from ..utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)


class CallbackTask(Task):
    """Base task with callbacks for monitoring and error handling."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Handle task failure."""
        logger.error(
            "Notification task failed",
            extra={
                "task_id": task_id,
                "exception": str(exc),
                "args": args,
                "kwargs": kwargs,
                "traceback": str(einfo),
            },
        )

        # Log to Sentry
        set_sentry_tag("celery_task_failed", "true")
        set_sentry_context(
            "celery_task",
            {
                "task_id": task_id,
                "task_name": self.name,
                "exception": str(exc),
                "args": args,
                "kwargs": kwargs,
            },
        )

    def on_success(self, retval, task_id, args, kwargs):
        """Handle task success."""
        logger.info(
            "Notification task succeeded",
            extra={
                "task_id": task_id,
                "result": retval,
                "args": args,
                "kwargs": kwargs,
            },
        )

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Handle task retry."""
        logger.warning(
            "Notification task retrying",
            extra={
                "task_id": task_id,
                "exception": str(exc),
                "retry_count": self.request.retries,
                "max_retries": self.max_retries,
            },
        )


@celery_app.task(
    bind=True,
    base=CallbackTask,
    max_retries=3,
    default_retry_delay=60,  # 1 minute
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,  # 10 minutes max
    retry_jitter=True,
    name="api.app.tasks.notification_tasks.send_notification_task",
)
def send_notification_task(
    self,
    user_id: str,
    notification_type: str,
    data: Dict[str, Any],
    priority: str = "medium",
    force_channels: Optional[List[str]] = None,
) -> Dict[str, bool]:
    """
    Celery task to send notification asynchronously.

    Args:
        user_id: User ID to send notification to
        notification_type: Notification type string (e.g., 'transaction_created')
        data: Notification data dictionary
        priority: Priority level ('low', 'medium', 'high', 'urgent')
        force_channels: Optional list of channels to force delivery to

    Returns:
        Dictionary mapping channel names to success status

    Raises:
        Exception: If notification sending fails after all retries
    """
    try:
        logger.info(
            "Processing notification task",
            extra={
                "user_id": user_id,
                "notification_type": notification_type,
                "priority": priority,
                "task_id": self.request.id,
            },
        )

        # Convert string types to enums
        notif_type = NotificationType(notification_type)
        notif_priority = NotificationPriority(priority)

        # Convert force_channels strings to enums if provided
        channels = None
        if force_channels:
            channel_map = {
                "in_app": NotificationChannel.IN_APP,
                "email": NotificationChannel.EMAIL,
                "sms": NotificationChannel.SMS,
                "push": NotificationChannel.PUSH,
            }
            channels = [channel_map[ch] for ch in force_channels if ch in channel_map]

        # Run async notification service in event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                notification_service.send_notification(
                    user_id=user_id,
                    notification_type=notif_type,
                    data=data,
                    priority=notif_priority,
                    force_channels=channels,
                )
            )
        finally:
            loop.close()

        logger.info(
            "Notification sent successfully",
            extra={
                "user_id": user_id,
                "notification_type": notification_type,
                "result": result,
                "task_id": self.request.id,
            },
        )

        return result

    except Exception as exc:
        logger.error(
            "Notification task failed",
            extra={
                "user_id": user_id,
                "notification_type": notification_type,
                "error": str(exc),
                "retry_count": self.request.retries,
                "task_id": self.request.id,
            },
        )

        # Set Sentry context
        set_sentry_context(
            "notification_task_error",
            {
                "user_id": user_id,
                "notification_type": notification_type,
                "error": str(exc),
                "retry_count": self.request.retries,
            },
        )

        # Retry the task
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    base=CallbackTask,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    name="api.app.tasks.notification_tasks.send_batch_notifications_task",
)
def send_batch_notifications_task(
    self,
    user_ids: List[str],
    notification_type: str,
    data: Dict[str, Any],
    priority: str = "medium",
) -> Dict[str, Dict[str, bool]]:
    """
    Send notifications to multiple users in batch.

    Args:
        user_ids: List of user IDs
        notification_type: Notification type string
        data: Notification data dictionary
        priority: Priority level

    Returns:
        Dictionary mapping user IDs to their notification results
    """
    try:
        logger.info(
            "Processing batch notification task",
            extra={
                "user_count": len(user_ids),
                "notification_type": notification_type,
                "priority": priority,
                "task_id": self.request.id,
            },
        )

        results = {}
        for user_id in user_ids:
            # Send individual notifications asynchronously
            task = send_notification_task.apply_async(
                args=[user_id, notification_type, data, priority],
                countdown=0,  # Send immediately
            )
            results[user_id] = {"task_id": task.id}

        logger.info(
            "Batch notifications queued successfully",
            extra={"user_count": len(user_ids), "task_id": self.request.id},
        )

        return results

    except Exception as exc:
        logger.error(
            "Batch notification task failed",
            extra={
                "user_count": len(user_ids),
                "notification_type": notification_type,
                "error": str(exc),
                "task_id": self.request.id,
            },
        )
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True, name="api.app.tasks.notification_tasks.cleanup_old_notifications"
)
def cleanup_old_notifications(self, days: int = 90):
    """
    Clean up notifications older than specified days.
    This is typically run as a periodic task (e.g., daily).

    Args:
        days: Number of days to keep notifications (default: 90)
    """
    try:
        logger.info(
            "Starting notification cleanup",
            extra={"days": days, "task_id": self.request.id},
        )

        supabase = get_supabase_client(use_service_role=True)
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Delete old read notifications
        response = (
            supabase.table("notifications")
            .delete()
            .eq("is_read", True)
            .lt("created_at", cutoff_date.isoformat())
            .execute()
        )

        deleted_count = len(response.data) if response.data else 0

        logger.info(
            "Notification cleanup completed",
            extra={
                "deleted_count": deleted_count,
                "cutoff_date": cutoff_date.isoformat(),
                "task_id": self.request.id,
            },
        )

        return {"deleted_count": deleted_count, "cutoff_date": cutoff_date.isoformat()}

    except Exception as exc:
        logger.error(
            "Notification cleanup failed",
            extra={"error": str(exc), "task_id": self.request.id},
        )
        raise


@celery_app.task(
    bind=True, name="api.app.tasks.notification_tasks.cleanup_inactive_devices"
)
def cleanup_inactive_devices(self, days: int = 180):
    """
    Clean up device tokens that haven't been used in specified days.

    Args:
        days: Number of days of inactivity before removal (default: 180)
    """
    try:
        logger.info(
            "Starting device cleanup", extra={"days": days, "task_id": self.request.id}
        )

        supabase = get_supabase_client(use_service_role=True)
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Mark inactive devices as inactive
        response = (
            supabase.table("user_devices")
            .update({"is_active": False})
            .eq("is_active", True)
            .lt("last_used", cutoff_date.isoformat())
            .execute()
        )

        deactivated_count = len(response.data) if response.data else 0

        logger.info(
            "Device cleanup completed",
            extra={
                "deactivated_count": deactivated_count,
                "cutoff_date": cutoff_date.isoformat(),
                "task_id": self.request.id,
            },
        )

        return {
            "deactivated_count": deactivated_count,
            "cutoff_date": cutoff_date.isoformat(),
        }

    except Exception as exc:
        logger.error(
            "Device cleanup failed",
            extra={"error": str(exc), "task_id": self.request.id},
        )
        raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="api.app.tasks.notification_tasks.check_upcoming_subscriptions",
)
def check_upcoming_subscriptions(self):
    """
    Daily task to check for upcoming subscription payments and notify users.

    Revolut-style proactive notifications:
    - Notifies users of subscriptions due tomorrow (individual alerts)
    - Notifies users of all subscriptions due within the week (summary)

    Runs daily at 9:00 AM UTC via Celery Beat.
    """
    try:
        logger.info(
            "Starting subscription reminder check",
            extra={"task_id": self.request.id},
        )

        supabase = get_supabase_client(use_service_role=True)
        today = datetime.utcnow().date()
        tomorrow = today + timedelta(days=1)
        week_ahead = today + timedelta(days=7)

        # Get all active subscriptions
        response = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("is_active", True)
            .execute()
        )

        if not response.data:
            logger.info("No active subscriptions found")
            return {"users_notified": 0, "subscriptions_checked": 0}

        # Group subscriptions by user
        user_subscriptions: Dict[str, List[Dict]] = {}
        for sub in response.data:
            user_id = sub["user_id"]
            if user_id not in user_subscriptions:
                user_subscriptions[user_id] = []
            user_subscriptions[user_id].append(sub)

        users_notified = 0
        total_notifications_sent = 0

        for user_id, subscriptions in user_subscriptions.items():
            due_tomorrow = []
            due_this_week = []

            for sub in subscriptions:
                # Calculate next payment date based on last_seen and frequency
                next_payment = calculate_next_payment_date(
                    sub["last_seen"], sub["frequency"]
                )

                if next_payment:
                    if next_payment == tomorrow:
                        due_tomorrow.append(sub)
                    elif today < next_payment <= week_ahead:
                        due_this_week.append(sub)

            # Send individual notifications for subscriptions due tomorrow
            for sub in due_tomorrow:
                send_notification_task.apply_async(
                    args=[
                        user_id,
                        NotificationType.SUBSCRIPTION_DUE_TOMORROW.value,
                        {
                            "subscription_name": sub["name"],
                            "amount": format_currency(float(sub["amount"])),
                            "due_date": tomorrow.strftime("%d %B %Y"),
                            "category": sub.get("category", "Subscription"),
                            "subscription_id": sub["id"],
                        },
                        "high",
                    ],
                    countdown=0,
                )
                total_notifications_sent += 1

            # Send weekly summary if there are multiple upcoming subscriptions
            if len(due_this_week) >= 2:
                total_amount = sum(float(s["amount"]) for s in due_this_week)
                subscription_list = "<br>".join(
                    f"• {s['name']}: {format_currency(float(s['amount']))}"
                    for s in due_this_week
                )

                send_notification_task.apply_async(
                    args=[
                        user_id,
                        NotificationType.SUBSCRIPTION_DUE_WEEK.value,
                        {
                            "subscription_count": len(due_this_week),
                            "total_amount": format_currency(total_amount),
                            "subscription_list": subscription_list,
                        },
                        "medium",
                    ],
                    countdown=300,  # 5 minute delay to avoid notification overload
                )
                total_notifications_sent += 1

            if due_tomorrow or due_this_week:
                users_notified += 1

        logger.info(
            "Subscription reminder check completed",
            extra={
                "users_notified": users_notified,
                "total_notifications_sent": total_notifications_sent,
                "subscriptions_checked": len(response.data),
                "task_id": self.request.id,
            },
        )

        return {
            "users_notified": users_notified,
            "total_notifications_sent": total_notifications_sent,
            "subscriptions_checked": len(response.data),
        }

    except Exception as exc:
        logger.error(
            "Subscription reminder check failed",
            extra={"error": str(exc), "task_id": self.request.id},
        )
        set_sentry_context(
            "subscription_reminder_error",
            {"error": str(exc), "task_id": self.request.id},
        )
        raise


def calculate_next_payment_date(last_seen_str: str, frequency: str):
    """
    Calculate the next payment date based on last seen date and frequency.

    Args:
        last_seen_str: ISO format date string of last payment
        frequency: Payment frequency (monthly, yearly, weekly)

    Returns:
        Next payment date or None if cannot be calculated
    """
    try:
        from dateutil.relativedelta import relativedelta

        # Parse the last_seen date
        if isinstance(last_seen_str, str):
            last_seen = datetime.strptime(
                last_seen_str.split("T")[0], "%Y-%m-%d"
            ).date()
        else:
            last_seen = last_seen_str

        today = datetime.utcnow().date()

        # Calculate next payment based on frequency
        frequency_lower = frequency.lower()

        if frequency_lower == "monthly":
            delta = relativedelta(months=1)
        elif frequency_lower == "yearly":
            delta = relativedelta(years=1)
        elif frequency_lower == "weekly":
            delta = relativedelta(weeks=1)
        elif frequency_lower == "daily":
            delta = relativedelta(days=1)
        elif frequency_lower == "quarterly":
            delta = relativedelta(months=3)
        else:
            # Default to monthly for unknown frequencies
            delta = relativedelta(months=1)

        # Find the next payment date that's in the future
        next_payment = last_seen + delta

        # Keep adding the interval until we find a future date
        while next_payment <= today:
            next_payment = next_payment + delta

        return next_payment

    except Exception as e:
        logger.warning(f"Could not calculate next payment date: {e}")
        return None


def format_currency(amount: float, currency: str = "NAD") -> str:
    """Format amount as currency string."""
    return f"{currency} {amount:,.2f}"
