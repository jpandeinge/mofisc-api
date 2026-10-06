# api/app/services/subscriptions/subscription_reminder_service.py
"""
Subscription Reminder Service - Revolut-style proactive payment notifications.

Works without Redis/Celery using FastAPI's native async patterns.
This service checks for upcoming subscription payments and sends notifications
to help users ensure they have sufficient funds.

Key Features:
- Daily scheduled check via asyncio background task
- Individual alerts for payments due tomorrow (high priority)
- Weekly summary for multiple upcoming payments
- Direct notification delivery (no queue dependency)
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dateutil.relativedelta import relativedelta

from ...sentry import set_sentry_context
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client
from ..notifications.base import NotificationPriority, NotificationType
from ..notifications.notification_service import notification_service

logger = get_logger(__name__)


class SubscriptionReminderService:
    """
    Service for checking and sending subscription payment reminders.

    Designed to run as a periodic background task within FastAPI's
    lifespan, without requiring external task queues like Celery.
    """

    @staticmethod
    def calculate_next_payment_date(
        last_seen_str: str, frequency: str
    ) -> Optional[datetime]:
        """
        Calculate the next payment date based on last seen date and frequency.

        Args:
            last_seen_str: ISO format date string of last payment
            frequency: Payment frequency (monthly, yearly, weekly, daily, quarterly)

        Returns:
            Next payment date or None if cannot be calculated
        """
        try:
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

    @staticmethod
    def format_currency(amount: float, currency: str = "NAD") -> str:
        """Format amount as currency string."""
        return f"{currency} {amount:,.2f}"

    async def check_upcoming_subscriptions(self) -> Dict[str, Any]:
        """
        Check all users for upcoming subscription payments and send notifications.

        This is the main entry point called by the scheduled background task.

        Notification Logic:
        - Subscriptions due tomorrow: Individual high-priority notification
        - 2+ subscriptions due this week: Summary notification

        Returns:
            Dictionary with statistics about notifications sent
        """
        try:
            logger.info("Starting subscription reminder check")

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
                    next_payment = self.calculate_next_payment_date(
                        sub["last_seen"], sub["frequency"]
                    )

                    if next_payment:
                        if next_payment == tomorrow:
                            due_tomorrow.append(sub)
                        elif today < next_payment <= week_ahead:
                            due_this_week.append(sub)

                # Send individual notifications for subscriptions due tomorrow
                for sub in due_tomorrow:
                    try:
                        dedupe_key = f"subscription_due_tomorrow:{sub['id']}:{tomorrow.isoformat()}"
                        await notification_service.send_notification(
                            user_id=user_id,
                            notification_type=NotificationType.SUBSCRIPTION_DUE_TOMORROW,
                            data={
                                "subscription_name": sub["name"],
                                "amount": self.format_currency(float(sub["amount"])),
                                "due_date": tomorrow.strftime("%d %B %Y"),
                                "category": sub.get("category", "Subscription"),
                                "subscription_id": sub["id"],
                                "action_url": "/app?section=subscriptions",
                                "dedupe_key": dedupe_key,
                                "dedupe_window_hours": 30,
                            },
                            priority=NotificationPriority.HIGH,
                        )
                        total_notifications_sent += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to send tomorrow notification for {sub['name']}: {e}"
                        )

                # Send weekly summary if there are multiple upcoming subscriptions
                if len(due_this_week) >= 2:
                    try:
                        total_amount = sum(float(s["amount"]) for s in due_this_week)
                        week_token = (
                            f"{today.isocalendar()[0]}-W{today.isocalendar()[1]}"
                        )
                        dedupe_key = f"subscription_due_week:{week_token}"
                        subscription_list = "<br>".join(
                            f"• {s['name']}: {self.format_currency(float(s['amount']))}"
                            for s in due_this_week
                        )

                        await notification_service.send_notification(
                            user_id=user_id,
                            notification_type=NotificationType.SUBSCRIPTION_DUE_WEEK,
                            data={
                                "subscription_count": len(due_this_week),
                                "total_amount": self.format_currency(total_amount),
                                "subscription_list": subscription_list,
                                "action_url": "/app?section=subscriptions",
                                "dedupe_key": dedupe_key,
                                "dedupe_window_hours": 168,
                            },
                            priority=NotificationPriority.MEDIUM,
                        )
                        total_notifications_sent += 1
                    except Exception as e:
                        logger.error(f"Failed to send weekly summary: {e}")

                if due_tomorrow or due_this_week:
                    users_notified += 1

            logger.info(
                "Subscription reminder check completed",
                extra={
                    "users_notified": users_notified,
                    "total_notifications_sent": total_notifications_sent,
                    "subscriptions_checked": len(response.data),
                },
            )

            return {
                "users_notified": users_notified,
                "total_notifications_sent": total_notifications_sent,
                "subscriptions_checked": len(response.data),
            }

        except Exception as exc:
            logger.error(f"Subscription reminder check failed: {exc}", exc_info=True)
            set_sentry_context(
                "subscription_reminder_error",
                {"error": str(exc)},
            )
            raise

    async def get_upcoming_for_user(
        self, user_id: str, days: int = 7
    ) -> Dict[str, Any]:
        """
        Get upcoming subscription payments for a specific user.

        Args:
            user_id: User ID to check
            days: Number of days to look ahead (default: 7)

        Returns:
            Dictionary with upcoming subscriptions and totals
        """
        try:
            supabase = get_supabase_client(use_service_role=True)
            today = datetime.utcnow().date()
            end_date = today + timedelta(days=days)

            # Get user's active subscriptions
            response = (
                supabase.table("user_subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )

            if not response.data:
                return {
                    "subscriptions": [],
                    "total_amount": "NAD 0.00",
                    "count": 0,
                    "period_days": days,
                }

            upcoming = []
            total_amount = 0.0

            for sub in response.data:
                next_payment = self.calculate_next_payment_date(
                    sub["last_seen"], sub["frequency"]
                )

                if next_payment and today < next_payment <= end_date:
                    amount = float(sub["amount"])
                    total_amount += amount

                    days_until = (next_payment - today).days

                    upcoming.append(
                        {
                            "id": sub["id"],
                            "name": sub["name"],
                            "amount": self.format_currency(amount),
                            "amount_raw": amount,
                            "category": sub.get("category", "Subscription"),
                            "due_date": next_payment.isoformat(),
                            "days_until_due": days_until,
                            "frequency": sub["frequency"],
                            "urgency": (
                                "high"
                                if days_until <= 1
                                else "medium" if days_until <= 3 else "low"
                            ),
                        }
                    )

            # Sort by due date
            upcoming.sort(key=lambda x: x["days_until_due"])

            return {
                "subscriptions": upcoming,
                "total_amount": self.format_currency(total_amount),
                "count": len(upcoming),
                "period_days": days,
            }

        except Exception as e:
            logger.error(f"Failed to get upcoming subscriptions: {e}")
            raise


# Singleton instance
subscription_reminder_service = SubscriptionReminderService()
