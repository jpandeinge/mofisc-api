# api/app/services/budgets/budget_alert_service.py
"""
Budget Alert Service - proactive budget threshold notifications.

Runs without Celery/Redis. Intended to be triggered by:
- Statement upload pipeline
- Transaction classification completion
- Manual transaction edits
"""

import asyncio
from datetime import datetime
from typing import Any, Dict

from ...services.notifications.base import (
    NotificationPriority,
    NotificationType,
)
from ...services.notifications.notification_service import notification_service
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)


class BudgetAlertService:
    @staticmethod
    def _format_currency(amount: float, currency: str = "NAD") -> str:
        return f"{currency} {amount:,.2f}"

    async def check_and_notify_for_user(self, user_id: str) -> Dict[str, Any]:
        """
        Check budget thresholds for a user and send notifications if needed.

        Notifications are only sent when:
        - A budget crosses its alert threshold (warning)
        - A budget reaches/exceeds 100% (exceeded)

        Returns a summary dictionary.
        """
        try:
            supabase = get_supabase_client(use_service_role=True)

            budgets_response = (
                supabase.table("budgets")
                .select("id, category, amount, period, alert_threshold")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )

            if not budgets_response.data:
                return {"budgets_checked": 0, "notifications_sent": 0}

            budgets_by_id = {b["id"]: b for b in budgets_response.data}

            try:
                status_response = supabase.rpc(
                    "check_budget_alerts", {"p_user_id": user_id}
                ).execute()
                status_rows = status_response.data or []
            except Exception as rpc_error:
                logger.warning(
                    "Budget alert RPC failed, using manual fallback",
                    extra={"user_id": user_id, "error": str(rpc_error)},
                )
                from .budget_service import BudgetService

                status_rows = BudgetService._get_budget_status_fallback(  # noqa: SLF001
                    supabase, user_id
                )

            if not status_rows:
                return {
                    "budgets_checked": len(budgets_response.data),
                    "notifications_sent": 0,
                }

            states_response = (
                supabase.table("budget_alert_states")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            states_by_budget = {s["budget_id"]: s for s in (states_response.data or [])}

            notifications_sent = 0

            for status in status_rows:
                budget_id = status.get("budget_id")
                budget_meta = budgets_by_id.get(budget_id)
                if not budget_meta:
                    continue

                threshold = budget_meta.get("alert_threshold") or 80
                percentage = int(status.get("percentage") or 0)
                spent_amount = float(status.get("spent_amount") or 0)
                budget_amount = float(status.get("budget_amount") or 0)
                remaining_amount = budget_amount - spent_amount

                new_status = (
                    "exceeded"
                    if percentage >= 100
                    else "warning" if percentage >= threshold else "ok"
                )

                prev_state = states_by_budget.get(budget_id)
                prev_status = prev_state["last_status"] if prev_state else "ok"
                prev_percentage = prev_state["last_percentage"] if prev_state else 0

                should_notify = False
                exceeded = False

                if new_status == "warning":
                    if prev_status == "ok" and prev_percentage < threshold:
                        should_notify = True
                elif new_status == "exceeded":
                    if prev_status != "exceeded" and prev_percentage < 100:
                        should_notify = True
                        exceeded = True

                if should_notify:
                    action_url = (
                        f"/planning?tab=budgets&budget_id={budget_id}"
                        if budget_id
                        else "/planning?tab=budgets"
                    )
                    dedupe_key = f"budget:{budget_id}:{new_status}:{datetime.utcnow().date().isoformat()}"
                    data = {
                        "category": budget_meta.get("category"),
                        "budget_amount": self._format_currency(budget_amount),
                        "spent_amount": self._format_currency(spent_amount),
                        "remaining_amount": self._format_currency(
                            max(remaining_amount, 0)
                        ),
                        "over_amount": self._format_currency(
                            max(spent_amount - budget_amount, 0)
                        ),
                        "percentage": percentage,
                        "budget_id": budget_id,
                        "period": budget_meta.get("period"),
                        "alert_threshold": threshold,
                        "action_url": action_url,
                        "dedupe_key": dedupe_key,
                        "dedupe_window_hours": 24,
                    }

                    await notification_service.send_notification(
                        user_id=user_id,
                        notification_type=(
                            NotificationType.BUDGET_EXCEEDED
                            if exceeded
                            else NotificationType.BUDGET_WARNING
                        ),
                        data=data,
                        priority=(
                            NotificationPriority.HIGH
                            if exceeded
                            else NotificationPriority.MEDIUM
                        ),
                    )
                    notifications_sent += 1

                last_alerted_at = (
                    prev_state.get("last_alerted_at") if prev_state else None
                )
                if should_notify:
                    last_alerted_at = datetime.utcnow().isoformat()

                supabase.table("budget_alert_states").upsert(
                    {
                        "budget_id": budget_id,
                        "user_id": user_id,
                        "last_percentage": percentage,
                        "last_status": new_status,
                        "last_alert_threshold": threshold,
                        "last_alerted_at": last_alerted_at,
                    },
                    on_conflict="budget_id",
                ).execute()

            return {
                "budgets_checked": len(budgets_response.data),
                "notifications_sent": notifications_sent,
            }

        except Exception as exc:
            logger.error(
                "Failed to check budget alerts",
                extra={"user_id": user_id, "error": str(exc)},
            )
            raise

    def check_and_notify_for_user_sync(self, user_id: str) -> Dict[str, Any]:
        """Sync wrapper for background tasks (no Celery)."""
        return asyncio.run(self.check_and_notify_for_user(user_id))


budget_alert_service = BudgetAlertService()
