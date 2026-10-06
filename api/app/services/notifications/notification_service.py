# api/app/services/notifications/notification_service.py

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from uuid import uuid4

from postgrest.exceptions import APIError

from ...sentry import set_sentry_context
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client
from .base import (
    NotificationChannel,
    NotificationPriority,
    NotificationTemplate,
    NotificationType,
)
from .email_provider import EmailProvider
from .push_provider import PushProvider
from .sms_provider import SMSProvider

logger = get_logger(__name__)


def _is_unique_violation(error: Exception) -> bool:
    code = getattr(error, "code", None)
    message = str(getattr(error, "message", error)).lower()
    details = str(getattr(error, "details", "")).lower()
    return code == "23505" or "duplicate key value violates unique constraint" in (
        message + details
    )


class NotificationService:
    """Main notification service coordinating all delivery channels."""

    def __init__(self):
        self.email_provider = EmailProvider()
        self.sms_provider = SMSProvider()
        self.push_provider = PushProvider()

    async def send_notification(
        self,
        user_id: str,
        notification_type: NotificationType,
        data: Dict[str, Any],
        db=None,  # Kept for backwards compatibility but not used
        priority: NotificationPriority = NotificationPriority.MEDIUM,
        force_channels: Optional[List[NotificationChannel]] = None,
    ) -> Dict[str, bool]:
        """
        Send notification to user via their preferred channels.

        Args:
            user_id: User ID
            notification_type: Type of notification
            data: Notification data
            db: Deprecated - kept for backwards compatibility
            priority: Notification priority
            force_channels: Override user preferences and send via these channels

        Returns:
            Dict mapping channel to success status
        """
        results = {}
        supabase = get_supabase_client(use_service_role=True)

        try:
            notification_data = dict(data)
            notification_id = notification_data.get("notification_id") or str(uuid4())
            notification_data["notification_id"] = notification_id
            self._apply_dedupe_defaults(notification_type, notification_data)
            self._apply_action_target(notification_type, notification_data)
            dedupe_key = notification_data.get("dedupe_key")

            if self._is_duplicate_notification(
                supabase, user_id, notification_type, notification_data
            ):
                logger.info(
                    "Skipping duplicate notification",
                    extra={
                        "user_id": user_id,
                        "notification_type": notification_type.value,
                        "dedupe_key": notification_data.get("dedupe_key"),
                    },
                )
                return {}

            # Prepare notification title and body
            title, body = NotificationTemplate.render_push(
                notification_type, notification_data
            )

            notification_record = {
                "id": notification_id,
                "user_id": user_id,
                "notification_type": notification_type.value,
                "title": title,
                "body": body,
                "data": notification_data,
                "dedupe_key": dedupe_key,
                "delivered_via": {},
                "priority": priority.value,
                "is_read": False,
                "icon": notification_data.get("icon")
                or self._icon_for_notification_type(notification_type),
            }
            if notification_data.get("action_url"):
                notification_record["action_url"] = notification_data.get("action_url")

            # Insert notification first so dedupe is atomic across workers/instances.
            try:
                supabase.table("notifications").insert(notification_record).execute()
            except APIError as insert_err:
                if dedupe_key and _is_unique_violation(insert_err):
                    logger.info(
                        "Skipping duplicate notification (DB constraint)",
                        extra={
                            "user_id": user_id,
                            "notification_type": notification_type.value,
                            "dedupe_key": dedupe_key,
                        },
                    )
                    return {}
                raise

            # Get user profile and preferences from Supabase
            user_response = (
                supabase.table("user_profiles").select("*").eq("id", user_id).execute()
            )
            profile = user_response.data[0] if user_response.data else {}
            preferences = profile.get("preferences", {}) or {}
            notification_prefs = preferences.get("notifications", {}) or {}

            # Determine which channels to use
            if force_channels is not None:
                channels_to_use = force_channels
            else:
                channels_to_use = []
                if notification_prefs.get("in_app", True):
                    channels_to_use.append(NotificationChannel.IN_APP)
                if notification_prefs.get("email", False):
                    channels_to_use.append(NotificationChannel.EMAIL)
                if notification_prefs.get("sms", False):
                    channels_to_use.append(NotificationChannel.SMS)
                if notification_prefs.get("push", False):
                    channels_to_use.append(NotificationChannel.PUSH)

            # Persistence + Supabase Realtime is the in-app delivery path.
            if NotificationChannel.IN_APP in channels_to_use:
                results["in_app"] = True

            # Send via email if enabled
            if NotificationChannel.EMAIL in channels_to_use:
                if self.email_provider.is_configured():
                    subject, content = NotificationTemplate.render_email(
                        notification_type, notification_data
                    )
                    email = profile.get("email")
                    if email:
                        results["email"] = await self.email_provider.send(
                            email, subject, content
                        )
                else:
                    results["email"] = False
                    logger.debug("Email provider not configured")

            # Send via SMS if enabled
            if NotificationChannel.SMS in channels_to_use:
                if self.sms_provider.is_configured():
                    content = NotificationTemplate.render_sms(
                        notification_type, notification_data
                    )
                    phone = profile.get("phone")
                    if phone:
                        results["sms"] = await self.sms_provider.send(
                            phone, "", content
                        )
                    else:
                        results["sms"] = False
                        logger.debug(f"No phone number for user {user_id}")
                else:
                    results["sms"] = False
                    logger.debug("SMS provider not configured")

            # Send via push if enabled
            if NotificationChannel.PUSH in channels_to_use:
                if self.push_provider.is_configured():
                    # Get all active devices for user from Supabase
                    devices_response = (
                        supabase.table("user_devices")
                        .select("device_token")
                        .eq("user_id", user_id)
                        .eq("is_active", True)
                        .execute()
                    )

                    if devices_response.data:
                        device_tokens = [
                            d["device_token"] for d in devices_response.data
                        ]
                        # Send to all devices
                        push_results = await self.push_provider.send_batch(
                            device_tokens, title, body, notification_data
                        )
                        # Consider successful if at least one device received it
                        results["push"] = any(push_results.values())
                        successful_tokens = [
                            token for token, success in push_results.items() if success
                        ]
                        if successful_tokens:
                            self._touch_devices(supabase, successful_tokens)
                    else:
                        results["push"] = False
                        logger.debug(f"No active devices for user {user_id}")
                else:
                    results["push"] = False
                    logger.debug("Push provider not configured")

            # Update delivery status after attempting all channels.
            try:
                supabase.table("notifications").update(
                    {
                        "delivered_via": results,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                ).eq("id", notification_id).execute()
            except Exception as update_err:
                logger.warning(
                    "Failed to update notification delivery status",
                    extra={
                        "notification_id": notification_id,
                        "error": str(update_err),
                    },
                )

            logger.info(
                f"Notification sent to user {user_id}: {notification_type.value}"
            )
            return results

        except Exception as e:
            set_sentry_context(
                "notification_error",
                {
                    "user_id": user_id,
                    "notification_type": notification_type.value,
                    "error": str(e),
                },
            )
            logger.error(f"Failed to send notification to user {user_id}: {e}")
            return {}

    def _is_duplicate_notification(
        self,
        supabase,
        user_id: str,
        notification_type: NotificationType,
        data: Dict[str, Any],
    ) -> bool:
        dedupe_key = data.get("dedupe_key")
        if not dedupe_key:
            return False

        try:
            window_hours = int(data.get("dedupe_window_hours") or 24)
        except (TypeError, ValueError):
            window_hours = 24

        window_start = datetime.utcnow() - timedelta(hours=window_hours)

        response = (
            supabase.table("notifications")
            .select("id, data")
            .eq("user_id", user_id)
            .eq("notification_type", notification_type.value)
            .gte("created_at", window_start.isoformat())
            .limit(100)
            .execute()
        )

        for record in response.data or []:
            record_data = record.get("data") or {}
            if record_data.get("dedupe_key") == dedupe_key:
                return True

        return False

    def _icon_for_notification_type(self, notification_type: NotificationType) -> str:
        icon_map = {
            NotificationType.TRANSACTION_CREATED: "credit-card",
            NotificationType.TRANSACTION_UPDATED: "credit-card",
            NotificationType.BUDGET_EXCEEDED: "alert-triangle",
            NotificationType.BUDGET_WARNING: "zap",
            NotificationType.GOAL_MILESTONE: "target",
            NotificationType.GOAL_COMPLETED: "trending-up",
            NotificationType.PAYMENT_DUE: "credit-card",
            NotificationType.SUBSCRIPTION_DUE_TOMORROW: "credit-card",
            NotificationType.SUBSCRIPTION_DUE_WEEK: "calendar",
            NotificationType.SUBSCRIPTION_CHARGED: "check-circle",
            NotificationType.SECURITY_ALERT: "shield",
            NotificationType.ACCOUNT_VERIFICATION: "shield-check",
            NotificationType.PASSWORD_CHANGED: "shield-check",
            NotificationType.LOGIN_ALERT: "shield",
            NotificationType.SYSTEM_ALERT: "alert-triangle",
            NotificationType.ACHIEVEMENT_UNLOCKED: "award",
            NotificationType.LEVEL_UP: "trending-up",
            NotificationType.STREAK_MILESTONE: "zap",
            NotificationType.BADGE_EARNED: "star",
            NotificationType.CHALLENGE_COMPLETED: "check-circle",
            NotificationType.LEADERBOARD_POSITION: "bar-chart-2",
            NotificationType.DAILY_CHALLENGE_AVAILABLE: "target",
            NotificationType.REWARD_EARNED: "gift",
        }
        return icon_map.get(notification_type, "bell")

    def _apply_action_target(
        self, notification_type: NotificationType, data: Dict[str, Any]
    ) -> None:
        """Attach a canonical internal deep link to a notification."""

        def identifier(key: str) -> Optional[str]:
            value = data.get(key)
            if value is None:
                return None
            normalized = str(value).strip()
            return normalized or None

        def path(base: str, **params: str) -> str:
            return f"{base}?{urlencode(params)}"

        transaction_id = identifier("transaction_id")
        subscription_id = identifier("subscription_id")
        budget_id = identifier("budget_id")
        goal_id = identifier("goal_id")
        statement_id = identifier("statement_id")
        achievement_id = identifier("achievement_id")

        if transaction_id:
            data["action_url"] = path(
                "/app",
                section="transactions",
                transaction_id=transaction_id,
            )
        elif subscription_id:
            data["action_url"] = path(
                "/app",
                section="subscriptions",
                subscription_id=subscription_id,
            )
        elif budget_id:
            data["action_url"] = path(
                "/planning",
                tab="budgets",
                budget_id=budget_id,
            )
        elif goal_id:
            data["action_url"] = path(
                "/planning",
                tab="goals",
                goal_id=goal_id,
            )
        elif statement_id and notification_type in (
            NotificationType.TRANSACTION_CREATED,
            NotificationType.TRANSACTION_UPDATED,
        ):
            data["action_url"] = path(
                "/app",
                section="transactions",
                statement_id=statement_id,
            )
        elif achievement_id:
            data["action_url"] = path(
                "/activity",
                achievement_id=achievement_id,
            )
        else:
            action_url = data.get("action_url")
            if (
                not isinstance(action_url, str)
                or not action_url.startswith("/")
                or action_url.startswith("//")
                or "\\" in action_url
            ):
                data.pop("action_url", None)

    def _apply_dedupe_defaults(
        self, notification_type: NotificationType, data: Dict[str, Any]
    ) -> None:
        if data.get("dedupe_key"):
            return

        today = datetime.utcnow().date().isoformat()
        key_parts = None
        window_hours = None

        if notification_type == NotificationType.TRANSACTION_CREATED:
            statement_id = data.get("statement_id")
            transaction_id = data.get("transaction_id")
            if statement_id:
                key_parts = ["transaction_created", "statement", statement_id, today]
                window_hours = 24
            elif transaction_id:
                key_parts = ["transaction_created", "transaction", transaction_id]
                window_hours = 24
        elif notification_type == NotificationType.TRANSACTION_UPDATED:
            statement_id = data.get("statement_id")
            transaction_id = data.get("transaction_id")
            if transaction_id:
                key_parts = ["transaction_updated", "transaction", transaction_id]
                window_hours = 24
            elif statement_id:
                key_parts = ["transaction_updated", "statement", statement_id, today]
                window_hours = 24
        elif notification_type in (
            NotificationType.GOAL_MILESTONE,
            NotificationType.GOAL_COMPLETED,
        ):
            goal_id = data.get("goal_id") or data.get("goal_name")
            if goal_id:
                key_parts = ["goal", goal_id]
                if notification_type == NotificationType.GOAL_COMPLETED:
                    key_parts.append("completed")
                    window_hours = 168
                else:
                    milestone = (
                        data.get("percentage")
                        or data.get("progress")
                        or data.get("milestone")
                    )
                    if milestone is not None:
                        key_parts.append(str(milestone))
                    key_parts.append(today)
                    window_hours = 72
        elif notification_type == NotificationType.ACHIEVEMENT_UNLOCKED:
            achievement_id = data.get("achievement_id") or data.get("achievement_name")
            if achievement_id:
                key_parts = ["achievement", achievement_id]
                window_hours = 168
        elif notification_type == NotificationType.BUDGET_WARNING:
            budget_id = data.get("budget_id")
            if budget_id:
                key_parts = ["budget_warning", budget_id, today]
                window_hours = 24
        elif notification_type == NotificationType.BUDGET_EXCEEDED:
            budget_id = data.get("budget_id")
            if budget_id:
                key_parts = ["budget_exceeded", budget_id, today]
                window_hours = 24
        elif notification_type == NotificationType.LEVEL_UP:
            level = data.get("level")
            if level is not None:
                key_parts = ["level_up", str(level)]
                window_hours = 168
        elif notification_type == NotificationType.STREAK_MILESTONE:
            streak_type = data.get("streak_type")
            streak_days = data.get("streak_days")
            if streak_type and streak_days is not None:
                key_parts = ["streak", streak_type, str(streak_days)]
                window_hours = 168
        elif notification_type == NotificationType.BADGE_EARNED:
            badge = data.get("badge_name")
            if badge:
                key_parts = ["badge", badge]
                window_hours = 168
        elif notification_type == NotificationType.CHALLENGE_COMPLETED:
            challenge_id = data.get("challenge_id")
            challenge_name = data.get("challenge_name")
            if challenge_id:
                key_parts = ["challenge_completed", challenge_id, today]
                window_hours = 72
            elif challenge_name:
                key_parts = ["challenge_completed", challenge_name, today]
                window_hours = 72
        elif notification_type == NotificationType.LEADERBOARD_POSITION:
            leaderboard = data.get("leaderboard_name")
            position = data.get("position")
            if leaderboard and position is not None:
                key_parts = ["leaderboard", leaderboard, str(position), today]
                window_hours = 24
        elif notification_type == NotificationType.DAILY_CHALLENGE_AVAILABLE:
            challenge = data.get("challenge_name") or data.get("challenge_type")
            key_parts = ["daily_challenge", challenge or "daily", today]
            window_hours = 24
        elif notification_type == NotificationType.REWARD_EARNED:
            reward = data.get("reward_name")
            if reward:
                key_parts = ["reward", reward]
                window_hours = 168
        elif notification_type == NotificationType.SECURITY_ALERT:
            alert_type = data.get("alert_type") or data.get("event")
            location = data.get("location") or "unknown"
            if alert_type:
                key_parts = ["security", alert_type, location, today]
                window_hours = 6
        elif notification_type == NotificationType.PASSWORD_CHANGED:
            key_parts = ["password_changed", today]
            window_hours = 6
        elif notification_type == NotificationType.LOGIN_ALERT:
            location = data.get("location") or "unknown"
            key_parts = ["login_alert", location, today]
            window_hours = 6
        elif notification_type == NotificationType.ACCOUNT_VERIFICATION:
            key_parts = ["account_verification", today]
            window_hours = 24
        elif notification_type == NotificationType.SYSTEM_ALERT:
            statement_id = data.get("statement_id")
            description = data.get("description") or data.get("error") or "system"
            if statement_id:
                key_parts = ["system", "statement", statement_id, today]
            else:
                key_parts = ["system", description[:64], today]
            window_hours = 6
        elif notification_type == NotificationType.PAYMENT_DUE:
            payment_id = data.get("payment_id") or data.get("subscription_id")
            if payment_id:
                key_parts = ["payment_due", payment_id, today]
                window_hours = 24
        elif notification_type == NotificationType.SUBSCRIPTION_DUE_TOMORROW:
            subscription_id = data.get("subscription_id")
            if subscription_id:
                key_parts = ["subscription_due_tomorrow", subscription_id, today]
                window_hours = 30
        elif notification_type == NotificationType.SUBSCRIPTION_DUE_WEEK:
            key_parts = ["subscription_due_week", today]
            window_hours = 168
        elif notification_type == NotificationType.SUBSCRIPTION_CHARGED:
            subscription_id = data.get("subscription_id")
            if subscription_id:
                key_parts = ["subscription_charged", subscription_id, today]
                window_hours = 24

        if key_parts:
            dedupe_key = ":".join(str(part) for part in key_parts if part)
            data["dedupe_key"] = dedupe_key
            data.setdefault("dedupe_window_hours", window_hours or 24)

    async def send_bulk_notification(
        self,
        user_ids: List[str],
        notification_type: NotificationType,
        data_fn: callable,
        priority: NotificationPriority = NotificationPriority.MEDIUM,
    ) -> Dict[str, Dict[str, bool]]:
        """
        Send notifications to multiple users.

        Args:
            user_ids: List of user IDs
            notification_type: Type of notification
            data_fn: Function that takes user_id and returns notification data
            db: Database session
            priority: Notification priority

        Returns:
            Dict mapping user_id to channel results
        """
        results = {}

        for user_id in user_ids:
            data = data_fn(user_id)
            results[user_id] = await self.send_notification(
                user_id=user_id,
                notification_type=notification_type,
                data=data,
                priority=priority,
            )

        return results

    def _touch_devices(self, supabase, tokens: List[str]) -> None:
        timestamp = datetime.utcnow().isoformat()
        for token in tokens:
            supabase.table("user_devices").update({"last_used": timestamp}).eq(
                "device_token", token
            ).execute()

    async def send_transaction_notification(
        self, user_id: str, transaction_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send transaction created notification.

        Args:
            user_id: User ID
            transaction_data: Transaction details
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.TRANSACTION_CREATED,
            data=transaction_data,
            priority=NotificationPriority.LOW,
        )

    async def send_budget_alert(
        self, user_id: str, budget_data: Dict[str, Any], exceeded: bool
    ) -> Dict[str, bool]:
        """
        Send budget warning or exceeded notification.

        Args:
            user_id: User ID
            budget_data: Budget details
            exceeded: True if budget exceeded, False if warning
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        notification_type = (
            NotificationType.BUDGET_EXCEEDED
            if exceeded
            else NotificationType.BUDGET_WARNING
        )
        priority = (
            NotificationPriority.HIGH if exceeded else NotificationPriority.MEDIUM
        )

        return await self.send_notification(
            user_id=user_id,
            notification_type=notification_type,
            data=budget_data,
            priority=priority,
        )

    async def send_goal_notification(
        self, user_id: str, goal_data: Dict[str, Any], completed: bool
    ) -> Dict[str, bool]:
        """
        Send goal milestone or completion notification.

        Args:
            user_id: User ID
            goal_data: Goal details
            completed: True if goal completed, False if milestone
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        notification_type = (
            NotificationType.GOAL_COMPLETED
            if completed
            else NotificationType.GOAL_MILESTONE
        )

        return await self.send_notification(
            user_id=user_id,
            notification_type=notification_type,
            data=goal_data,
            priority=NotificationPriority.MEDIUM,
        )

    async def send_security_alert(
        self, user_id: str, alert_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send security alert notification.

        Args:
            user_id: User ID
            alert_data: Alert details
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        # Security alerts should be sent via all available channels
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.SECURITY_ALERT,
            data=alert_data,
            priority=NotificationPriority.URGENT,
            force_channels=[
                NotificationChannel.EMAIL,
                NotificationChannel.SMS,
                NotificationChannel.PUSH,
            ],
        )

    async def send_achievement_notification(
        self, user_id: str, achievement_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send achievement unlocked notification.

        Args:
            user_id: User ID
            achievement_data: Achievement details (name, description, points, rarity)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.ACHIEVEMENT_UNLOCKED,
            data=achievement_data,
            priority=NotificationPriority.MEDIUM,
        )

    async def send_level_up_notification(
        self, user_id: str, level_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send level up notification.

        Args:
            user_id: User ID
            level_data: Level details (level, total_xp, xp_to_next_level, total_achievements)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.LEVEL_UP,
            data=level_data,
            priority=NotificationPriority.MEDIUM,
        )

    async def send_streak_milestone_notification(
        self, user_id: str, streak_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send streak milestone notification.

        Args:
            user_id: User ID
            streak_data: Streak details (streak_days, streak_type, bonus_points, start_date)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.STREAK_MILESTONE,
            data=streak_data,
            priority=NotificationPriority.MEDIUM,
        )

    async def send_badge_notification(
        self, user_id: str, badge_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send badge earned notification.

        Args:
            user_id: User ID
            badge_data: Badge details (badge_name, badge_description, category, difficulty)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.BADGE_EARNED,
            data=badge_data,
            priority=NotificationPriority.LOW,
        )

    async def send_challenge_completed_notification(
        self, user_id: str, challenge_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send challenge completed notification.

        Args:
            user_id: User ID
            challenge_data: Challenge details (challenge_name, reward_points, completion_time, difficulty)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.CHALLENGE_COMPLETED,
            data=challenge_data,
            priority=NotificationPriority.MEDIUM,
        )

    async def send_leaderboard_notification(
        self, user_id: str, leaderboard_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send leaderboard position update notification.

        Args:
            user_id: User ID
            leaderboard_data: Leaderboard details (position, leaderboard_name, score, previous_position, top_score, position_change_message)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.LEADERBOARD_POSITION,
            data=leaderboard_data,
            priority=NotificationPriority.LOW,
        )

    async def send_daily_challenge_notification(
        self, user_id: str, challenge_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send daily challenge available notification.

        Args:
            user_id: User ID
            challenge_data: Challenge details (challenge_name, challenge_description, reward_points, expires_at)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.DAILY_CHALLENGE_AVAILABLE,
            data=challenge_data,
            priority=NotificationPriority.LOW,
        )

    async def send_reward_notification(
        self, user_id: str, reward_data: Dict[str, Any]
    ) -> Dict[str, bool]:
        """
        Send reward earned notification.

        Args:
            user_id: User ID
            reward_data: Reward details (reward_name, reward_description, reward_type, reward_value)
            db: Database session

        Returns:
            Dict mapping channel to success status
        """
        return await self.send_notification(
            user_id=user_id,
            notification_type=NotificationType.REWARD_EARNED,
            data=reward_data,
            priority=NotificationPriority.MEDIUM,
        )


# Global notification service instance
notification_service = NotificationService()
