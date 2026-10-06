from api.app.services.notifications.base import (
    NotificationChannel,
    NotificationData,
    NotificationPriority,
    NotificationType,
)
from api.app.services.notifications.notification_service import NotificationService


def test_in_app_is_a_first_class_notification_channel():
    assert NotificationChannel.IN_APP.value == "in_app"


def test_notification_data_defaults_remain_portable_between_transports():
    notification = NotificationData(
        user_id="00000000-0000-0000-0000-000000000001",
        notification_type=NotificationType.SYSTEM_ALERT,
        data={"description": "The app will briefly be unavailable."},
    )

    assert notification.priority is NotificationPriority.MEDIUM
    assert notification.data["description"] == (
        "The app will briefly be unavailable."
    )
    assert notification.channels is None


def test_notification_action_target_uses_exact_transaction():
    data = {
        "transaction_id": "transaction 42",
        "action_url": "/app",
    }

    NotificationService()._apply_action_target(
        NotificationType.TRANSACTION_UPDATED,
        data,
    )

    assert data["action_url"] == (
        "/app?section=transactions&transaction_id=transaction+42"
    )


def test_notification_action_target_upgrades_generic_subscription_url():
    data = {
        "subscription_id": "subscription-1",
        "action_url": "/app?section=subscriptions",
    }

    NotificationService()._apply_action_target(
        NotificationType.SUBSCRIPTION_DUE_TOMORROW,
        data,
    )

    assert data["action_url"] == (
        "/app?section=subscriptions&subscription_id=subscription-1"
    )


def test_notification_action_target_rejects_external_url():
    data = {"action_url": "https://malicious.example"}

    NotificationService()._apply_action_target(
        NotificationType.SYSTEM_ALERT,
        data,
    )

    assert "action_url" not in data
