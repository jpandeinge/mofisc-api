# api/app/services/notifications/base.py

from abc import ABC, abstractmethod
from enum import Enum
from html import escape
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NotificationChannel(str, Enum):
    """Notification delivery channels."""

    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class NotificationType(str, Enum):
    """Types of notifications."""

    # Financial Notifications
    TRANSACTION_CREATED = "transaction_created"
    TRANSACTION_UPDATED = "transaction_updated"  # Classification complete
    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_WARNING = "budget_warning"
    GOAL_MILESTONE = "goal_milestone"
    GOAL_COMPLETED = "goal_completed"
    PAYMENT_DUE = "payment_due"

    # Subscription Reminders (Revolut-style proactive notifications)
    SUBSCRIPTION_DUE_TOMORROW = "subscription_due_tomorrow"
    SUBSCRIPTION_DUE_WEEK = "subscription_due_week"
    SUBSCRIPTION_CHARGED = "subscription_charged"

    # Security Notifications
    SECURITY_ALERT = "security_alert"
    ACCOUNT_VERIFICATION = "account_verification"
    PASSWORD_CHANGED = "password_changed"
    LOGIN_ALERT = "login_alert"

    # System Notifications
    SYSTEM_ALERT = "system_alert"  # System-level alerts (failures, maintenance)

    # Gamification & Achievement Notifications
    ACHIEVEMENT_UNLOCKED = "achievement_unlocked"
    LEVEL_UP = "level_up"
    STREAK_MILESTONE = "streak_milestone"
    BADGE_EARNED = "badge_earned"
    CHALLENGE_COMPLETED = "challenge_completed"
    LEADERBOARD_POSITION = "leaderboard_position"
    DAILY_CHALLENGE_AVAILABLE = "daily_challenge_available"
    REWARD_EARNED = "reward_earned"


class NotificationPriority(str, Enum):
    """Notification priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class NotificationData(BaseModel):
    """Notification data model."""

    user_id: str
    notification_type: NotificationType
    priority: NotificationPriority = NotificationPriority.MEDIUM
    data: Dict[str, Any]
    channels: Optional[List[NotificationChannel]] = None


class NotificationProvider(ABC):
    """Base class for notification providers."""

    @abstractmethod
    async def send(
        self,
        recipient: str,
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send notification via provider.

        Args:
            recipient: Recipient identifier (email, phone, device token)
            subject: Notification subject/title
            content: Notification content/body
            data: Additional data for the notification

        Returns:
            bool: True if sent successfully
        """
        pass

    @abstractmethod
    async def send_batch(
        self,
        recipients: List[str],
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        """
        Send notifications to multiple recipients.

        Args:
            recipients: List of recipient identifiers
            subject: Notification subject/title
            content: Notification content/body
            data: Additional data for the notification

        Returns:
            Dict mapping recipient to success status
        """
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        pass


class NotificationTemplate:
    """Base class for notification templates."""

    @staticmethod
    def render_email(
        notification_type: NotificationType, data: Dict[str, Any]
    ) -> tuple[str, str]:
        """
        Render email notification.

        Returns:
            tuple: (subject, html_content)
        """
        if notification_type == NotificationType.TRANSACTION_UPDATED and data.get(
            "transaction_id"
        ):
            description = escape(
                str(data.get("description") or "Your transaction was updated.")
            )
            return (
                "Transaction updated",
                f"<h2>Transaction Updated</h2><p>{description}</p>",
            )

        templates = {
            NotificationType.TRANSACTION_CREATED: (
                "New Transaction: {amount}",
                """
                <h2>Transaction Recorded</h2>
                <p>A new transaction has been recorded in your account.</p>
                <ul>
                    <li><strong>Amount:</strong> {amount}</li>
                    <li><strong>Category:</strong> {category}</li>
                    <li><strong>Date:</strong> {date}</li>
                    <li><strong>Description:</strong> {description}</li>
                </ul>
                """,
            ),
            NotificationType.TRANSACTION_UPDATED: (
                "Transactions Classified",
                """
                <h2>Transaction Classification Complete</h2>
                <p>Your transactions have been automatically categorised.</p>
                <ul>
                    <li><strong>Transactions Classified:</strong> {classified_count}</li>
                    <li><strong>Total Transactions:</strong> {total_count}</li>
                </ul>
                <p>{description}</p>
                """,
            ),
            NotificationType.SYSTEM_ALERT: (
                "System Alert: {error}",
                """
                <h2>System Alert</h2>
                <p>An issue occurred while processing your request.</p>
                <p><strong>{description}</strong></p>
                <p>Our team has been notified. Please try again later.</p>
                """,
            ),
            NotificationType.BUDGET_EXCEEDED: (
                "Budget Alert: {category} budget exceeded",
                """
                <h2>Budget Exceeded</h2>
                <p>You have exceeded your budget for {category}.</p>
                <ul>
                    <li><strong>Budget:</strong> {budget_amount}</li>
                    <li><strong>Spent:</strong> {spent_amount}</li>
                    <li><strong>Over by:</strong> {over_amount}</li>
                </ul>
                <p>Consider reviewing your spending in this category.</p>
                """,
            ),
            NotificationType.BUDGET_WARNING: (
                "Budget Warning: {category} at {percentage}%",
                """
                <h2>Budget Warning</h2>
                <p>You have used {percentage}% of your {category} budget.</p>
                <ul>
                    <li><strong>Budget:</strong> {budget_amount}</li>
                    <li><strong>Spent:</strong> {spent_amount}</li>
                    <li><strong>Remaining:</strong> {remaining_amount}</li>
                </ul>
                """,
            ),
            NotificationType.GOAL_MILESTONE: (
                "Goal Progress: {goal_name} at {percentage}%",
                """
                <h2>Goal Milestone Reached!</h2>
                <p>Congratulations! You've reached a milestone for your goal: {goal_name}</p>
                <ul>
                    <li><strong>Target:</strong> {target_amount}</li>
                    <li><strong>Current:</strong> {current_amount}</li>
                    <li><strong>Progress:</strong> {percentage}%</li>
                </ul>
                """,
            ),
            NotificationType.GOAL_COMPLETED: (
                "Goal Achieved: {goal_name}",
                """
                <h2>Congratulations! Goal Achieved!</h2>
                <p>You have successfully completed your goal: {goal_name}</p>
                <ul>
                    <li><strong>Target:</strong> {target_amount}</li>
                    <li><strong>Final Amount:</strong> {final_amount}</li>
                    <li><strong>Time Taken:</strong> {duration}</li>
                </ul>
                <p>Great job on reaching your financial goal!</p>
                """,
            ),
            NotificationType.SECURITY_ALERT: (
                "Security Alert: {alert_type}",
                """
                <h2>Security Alert</h2>
                <p>A security event has been detected on your account.</p>
                <ul>
                    <li><strong>Event:</strong> {alert_type}</li>
                    <li><strong>Time:</strong> {timestamp}</li>
                    <li><strong>Location:</strong> {location}</li>
                </ul>
                <p>If this wasn't you, please secure your account immediately.</p>
                """,
            ),
            NotificationType.PASSWORD_CHANGED: (
                "Password Changed Successfully",
                """
                <h2>Password Changed</h2>
                <p>Your password has been changed successfully.</p>
                <ul>
                    <li><strong>Time:</strong> {timestamp}</li>
                    <li><strong>IP Address:</strong> {ip_address}</li>
                </ul>
                <p>If you didn't make this change, please contact support immediately.</p>
                """,
            ),
            NotificationType.ACHIEVEMENT_UNLOCKED: (
                "Achievement Unlocked: {achievement_name}",
                """
                <h2>Congratulations! Achievement Unlocked</h2>
                <p>You have earned a new achievement: <strong>{achievement_name}</strong></p>
                <p><em>{achievement_description}</em></p>
                <ul>
                    <li><strong>Points Earned:</strong> {points}</li>
                    <li><strong>Rarity:</strong> {rarity}</li>
                </ul>
                <p>Keep up the great work managing your finances.</p>
                """,
            ),
            NotificationType.LEVEL_UP: (
                "Level Up: You are now Level {level}",
                """
                <h2>Level Up!</h2>
                <p>Congratulations! You have reached Level {level}.</p>
                <ul>
                    <li><strong>Total XP:</strong> {total_xp}</li>
                    <li><strong>XP to Next Level:</strong> {xp_to_next_level}</li>
                    <li><strong>Total Achievements:</strong> {total_achievements}</li>
                </ul>
                <p>New challenges and rewards are now available.</p>
                """,
            ),
            NotificationType.STREAK_MILESTONE: (
                "Streak Milestone: {streak_days} Days",
                """
                <h2>Streak Milestone Reached</h2>
                <p>You have maintained a {streak_days} day streak.</p>
                <p><strong>{streak_type}</strong></p>
                <ul>
                    <li><strong>Bonus Points:</strong> {bonus_points}</li>
                    <li><strong>Started:</strong> {start_date}</li>
                </ul>
                <p>Continue your streak to earn more rewards.</p>
                """,
            ),
            NotificationType.BADGE_EARNED: (
                "New Badge Earned: {badge_name}",
                """
                <h2>Badge Earned</h2>
                <p>You have earned the <strong>{badge_name}</strong> badge.</p>
                <p>{badge_description}</p>
                <ul>
                    <li><strong>Category:</strong> {category}</li>
                    <li><strong>Difficulty:</strong> {difficulty}</li>
                </ul>
                """,
            ),
            NotificationType.CHALLENGE_COMPLETED: (
                "Challenge Completed: {challenge_name}",
                """
                <h2>Challenge Completed</h2>
                <p>Congratulations! You have completed: <strong>{challenge_name}</strong></p>
                <ul>
                    <li><strong>Reward:</strong> {reward_points} points</li>
                    <li><strong>Completion Time:</strong> {completion_time}</li>
                    <li><strong>Difficulty:</strong> {difficulty}</li>
                </ul>
                <p>Check out new challenges in your Activity dashboard.</p>
                """,
            ),
            NotificationType.LEADERBOARD_POSITION: (
                "Leaderboard Update: Position {position}",
                """
                <h2>Leaderboard Position Update</h2>
                <p>You are now ranked <strong>#{position}</strong> on the {leaderboard_name} leaderboard.</p>
                <ul>
                    <li><strong>Score:</strong> {score}</li>
                    <li><strong>Previous Position:</strong> #{previous_position}</li>
                    <li><strong>Top Score:</strong> {top_score} points</li>
                </ul>
                <p>{position_change_message}</p>
                """,
            ),
            NotificationType.DAILY_CHALLENGE_AVAILABLE: (
                "New Daily Challenge Available",
                """
                <h2>Daily Challenge</h2>
                <p>A new daily challenge is waiting for you.</p>
                <p><strong>{challenge_name}</strong></p>
                <p>{challenge_description}</p>
                <ul>
                    <li><strong>Potential Reward:</strong> {reward_points} points</li>
                    <li><strong>Expires:</strong> {expires_at}</li>
                </ul>
                <p>Complete it today to maintain your streak.</p>
                """,
            ),
            NotificationType.REWARD_EARNED: (
                "Reward Earned: {reward_name}",
                """
                <h2>Reward Unlocked</h2>
                <p>You have earned: <strong>{reward_name}</strong></p>
                <p>{reward_description}</p>
                <ul>
                    <li><strong>Type:</strong> {reward_type}</li>
                    <li><strong>Value:</strong> {reward_value}</li>
                </ul>
                <p>Your reward is now available in your account.</p>
                """,
            ),
            NotificationType.SUBSCRIPTION_DUE_TOMORROW: (
                "{subscription_name} {amount} due tomorrow",
                """
                <h2>Subscription Payment Due Tomorrow</h2>
                <p>Your <strong>{subscription_name}</strong> subscription payment of <strong>{amount}</strong> is due tomorrow.</p>
                <p>Make sure you have sufficient funds in your account.</p>
                <ul>
                    <li><strong>Subscription:</strong> {subscription_name}</li>
                    <li><strong>Amount:</strong> {amount}</li>
                    <li><strong>Due Date:</strong> {due_date}</li>
                    <li><strong>Category:</strong> {category}</li>
                </ul>
                <p>Manage your subscriptions in the FISC app.</p>
                """,
            ),
            NotificationType.SUBSCRIPTION_DUE_WEEK: (
                "{subscription_count} subscriptions due this week - {total_amount}",
                """
                <h2>Upcoming Subscription Payments</h2>
                <p>You have <strong>{subscription_count} subscriptions</strong> totaling <strong>{total_amount}</strong> due this week.</p>
                <h3>Upcoming Payments:</h3>
                <ul>
                    {subscription_list}
                </ul>
                <p>Make sure you have sufficient funds available.</p>
                """,
            ),
            NotificationType.SUBSCRIPTION_CHARGED: (
                "{subscription_name} {amount} charged",
                """
                <h2>Subscription Payment Processed</h2>
                <p>Your <strong>{subscription_name}</strong> subscription payment of <strong>{amount}</strong> has been charged.</p>
                <ul>
                    <li><strong>Subscription:</strong> {subscription_name}</li>
                    <li><strong>Amount:</strong> {amount}</li>
                    <li><strong>Date:</strong> {charge_date}</li>
                </ul>
                """,
            ),
        }

        template = templates.get(
            notification_type, ("Notification from Galahad", "<p>{message}</p>")
        )

        subject = template[0].format(**data)
        content = template[1].format(**data)

        return subject, content

    @staticmethod
    def render_sms(notification_type: NotificationType, data: Dict[str, Any]) -> str:
        """
        Render SMS notification (max 160 characters).

        Returns:
            str: SMS content
        """
        if notification_type == NotificationType.TRANSACTION_UPDATED and data.get(
            "transaction_id"
        ):
            return (
                "Galahad: "
                f"{data.get('description') or 'Your transaction was updated.'}"
            )

        templates = {
            NotificationType.TRANSACTION_CREATED: (
                "Galahad: Transaction {amount} for {category} recorded on {date}."
            ),
            NotificationType.TRANSACTION_UPDATED: (
                "Galahad: {classified_count} transactions classified. {description}"
            ),
            NotificationType.SYSTEM_ALERT: ("Galahad Alert: {description}"),
            NotificationType.BUDGET_EXCEEDED: (
                "Galahad Alert: {category} budget exceeded. "
                "Spent {spent_amount} of {budget_amount}."
            ),
            NotificationType.BUDGET_WARNING: (
                "Galahad: {category} budget at {percentage}%. "
                "{remaining_amount} remaining."
            ),
            NotificationType.GOAL_MILESTONE: (
                "Galahad: {goal_name} reached {percentage}%! "
                "{current_amount} of {target_amount}."
            ),
            NotificationType.GOAL_COMPLETED: (
                "Galahad: Congratulations! Goal '{goal_name}' completed!"
            ),
            NotificationType.SECURITY_ALERT: (
                "Galahad Security Alert: {alert_type} detected at {timestamp}. "
                "If this wasn't you, secure your account now."
            ),
            NotificationType.PASSWORD_CHANGED: (
                "Galahad: Your password was changed. "
                "If this wasn't you, contact support immediately."
            ),
            NotificationType.ACHIEVEMENT_UNLOCKED: (
                "Galahad: Achievement unlocked - {achievement_name}! "
                "+{points} points earned."
            ),
            NotificationType.LEVEL_UP: (
                "Galahad: Level up! You are now Level {level}. " "{total_xp} XP earned."
            ),
            NotificationType.STREAK_MILESTONE: (
                "Galahad: {streak_days} day streak! " "+{bonus_points} bonus points."
            ),
            NotificationType.BADGE_EARNED: (
                "Galahad: New badge earned - {badge_name}."
            ),
            NotificationType.CHALLENGE_COMPLETED: (
                "Galahad: Challenge '{challenge_name}' completed! "
                "+{reward_points} points."
            ),
            NotificationType.LEADERBOARD_POSITION: (
                "Galahad: You are ranked #{position} on {leaderboard_name}. "
                "Score: {score}."
            ),
            NotificationType.DAILY_CHALLENGE_AVAILABLE: (
                "Galahad: New daily challenge available. "
                "Earn up to {reward_points} points today."
            ),
            NotificationType.REWARD_EARNED: (
                "Galahad: Reward unlocked - {reward_name}. " "Check your account."
            ),
            NotificationType.SUBSCRIPTION_DUE_TOMORROW: (
                "FISC: {subscription_name} {amount} due tomorrow. Ensure funds available."
            ),
            NotificationType.SUBSCRIPTION_DUE_WEEK: (
                "FISC: {subscription_count} subscriptions ({total_amount}) due this week."
            ),
            NotificationType.SUBSCRIPTION_CHARGED: (
                "FISC: {subscription_name} {amount} charged successfully."
            ),
        }

        template = templates.get(notification_type, "Galahad: {message}")

        return template.format(**data)

    @staticmethod
    def render_push(
        notification_type: NotificationType, data: Dict[str, Any]
    ) -> tuple[str, str]:
        """
        Render push notification.

        Returns:
            tuple: (title, body)
        """
        if notification_type == NotificationType.TRANSACTION_UPDATED and data.get(
            "transaction_id"
        ):
            return (
                "Transaction updated",
                str(data.get("description") or "Your transaction was updated."),
            )

        templates = {
            NotificationType.TRANSACTION_CREATED: (
                "New Transaction",
                "{amount} spent on {category}",
            ),
            NotificationType.TRANSACTION_UPDATED: (
                "Transactions Classified",
                "{classified_count} of {total_count} transactions categorized",
            ),
            NotificationType.SYSTEM_ALERT: (
                "System Alert",
                "{description}",
            ),
            NotificationType.BUDGET_EXCEEDED: (
                "Budget Exceeded",
                "{category}: Spent {spent_amount} of {budget_amount}",
            ),
            NotificationType.BUDGET_WARNING: (
                "Budget Warning",
                "{category} at {percentage}%",
            ),
            NotificationType.GOAL_MILESTONE: (
                "Goal Milestone",
                "{goal_name} reached {percentage}%!",
            ),
            NotificationType.GOAL_COMPLETED: (
                "Goal Achieved!",
                "Congratulations! {goal_name} completed",
            ),
            NotificationType.SECURITY_ALERT: (
                "Security Alert",
                "{alert_type} detected",
            ),
            NotificationType.PASSWORD_CHANGED: (
                "Password Changed",
                "Your password was updated",
            ),
            NotificationType.ACHIEVEMENT_UNLOCKED: (
                "Achievement Unlocked",
                "{achievement_name} - {points} points",
            ),
            NotificationType.LEVEL_UP: ("Level Up!", "You are now Level {level}"),
            NotificationType.STREAK_MILESTONE: (
                "Streak Milestone",
                "{streak_days} days - {bonus_points} bonus points",
            ),
            NotificationType.BADGE_EARNED: ("Badge Earned", "{badge_name}"),
            NotificationType.CHALLENGE_COMPLETED: (
                "Challenge Completed",
                "{challenge_name} - {reward_points} points",
            ),
            NotificationType.LEADERBOARD_POSITION: (
                "Leaderboard Update",
                "#{position} on {leaderboard_name}",
            ),
            NotificationType.DAILY_CHALLENGE_AVAILABLE: (
                "Daily Challenge",
                "New challenge available - {reward_points} points",
            ),
            NotificationType.REWARD_EARNED: ("Reward Earned", "{reward_name}"),
            NotificationType.SUBSCRIPTION_DUE_TOMORROW: (
                "Payment Due Tomorrow",
                "{subscription_name} - {amount}",
            ),
            NotificationType.SUBSCRIPTION_DUE_WEEK: (
                "Upcoming Subscriptions",
                "{subscription_count} payments totaling {total_amount} this week",
            ),
            NotificationType.SUBSCRIPTION_CHARGED: (
                "Subscription Charged",
                "{subscription_name} - {amount}",
            ),
        }

        template = templates.get(notification_type, ("Notification", "{message}"))

        title = template[0]
        body = template[1].format(**data)

        return title, body
