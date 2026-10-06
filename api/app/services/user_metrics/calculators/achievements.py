# api/app/services/user_metrics/calculators/achievements.py

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from ....utils.logging_config import get_logger

logger = get_logger(__name__)


def calculate_achievement_metrics(
    supabase, user_id: str, transactions: List[Dict]
) -> Dict[str, Any]:
    """Calculate achievement-related metrics from Supabase"""
    try:
        # Pull user progress with achievement metadata (source of truth).
        response = (
            supabase.table("user_achievements")
            .select(
                "is_completed,progress,current_value,achievement:achievements(category,trigger_event)"
            )
            .eq("user_id", user_id)
            .execute()
        )
        user_achievements = response.data or []
        completed = [a for a in user_achievements if a.get("is_completed", False)]

        def _achievement_category(row: Dict[str, Any]) -> str:
            achievement = row.get("achievement") or {}
            return str(achievement.get("category", "")).lower()

        def _achievement_trigger(row: Dict[str, Any]) -> str:
            achievement = row.get("achievement") or {}
            return str(achievement.get("trigger_event", "")).lower()

        real_achievements = {
            # Tracking + transaction-triggered achievements.
            "transaction_milestones": sum(
                1
                for row in completed
                if _achievement_category(row) in {"tracking", "engagement"}
                or _achievement_trigger(row)
                in {"transaction_added", "statement_uploaded", "category_tracked"}
            ),
            "savings_achievements": sum(
                1 for row in completed if _achievement_category(row) == "savings"
            ),
            "streak_achievements": sum(
                1
                for row in completed
                if _achievement_category(row) == "streak"
                or _achievement_trigger(row).endswith("_streak")
            ),
            # Balance/wellness achievements.
            "balance_achievements": sum(
                1
                for row in completed
                if _achievement_category(row) in {"spending", "wellness", "investment"}
            ),
        }

        total_real_achievements = len(completed)
        completed_achievements = len(completed)

        # If user_achievements has not been initialised yet, still expose total available.
        total_available = len(user_achievements)
        if total_available == 0:
            catalog_response = (
                supabase.table("achievements").select("id", count="exact").execute()
            )
            total_available = catalog_response.count or 0

        return {
            "real_achievements": real_achievements,
            "total_real_achievements": total_real_achievements,
            "completed_achievements": completed_achievements,
            "achievement_progress": {
                "completed": completed_achievements,
                "in_progress": len(
                    [
                        a
                        for a in user_achievements
                        if not a.get("is_completed", False) and a.get("progress", 0) > 0
                    ]
                ),
                "total": total_available,
            },
        }

    except Exception as e:
        logger.error(f"Error calculating achievement metrics: {str(e)}")
        return {
            "real_achievements": {
                "transaction_milestones": 0,
                "savings_achievements": 0,
                "streak_achievements": 0,
                "balance_achievements": 0,
            },
            "total_real_achievements": 0,
            "completed_achievements": 0,
            "achievement_progress": {"completed": 0, "in_progress": 0, "total": 0},
        }


def calculate_streak_metrics(
    supabase, user_id: str, transactions: List[Dict]
) -> Dict[str, Any]:
    """Calculate streak metrics from Supabase"""
    try:
        # Get user streaks from Supabase
        response = (
            supabase.table("user_streaks").select("*").eq("user_id", user_id).execute()
        )
        user_streaks = response.data if response.data else []

        # Calculate current savings streak from transactions
        if transactions:
            daily_net_flow = defaultdict(float)
            for t in transactions:
                date_str = t.get("transaction_date", "")
                if not date_str:
                    continue

                try:
                    date_obj = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    ).date()
                    date_key = date_obj.strftime("%Y-%m-%d")
                except (AttributeError, TypeError, ValueError):
                    continue

                amount = float(t.get("transaction_amount", 0))
                transaction_type = t.get("transaction_type", "").lower()

                if transaction_type == "credit":
                    daily_net_flow[date_key] += amount
                elif transaction_type == "debit":
                    daily_net_flow[date_key] -= amount

            current_savings_streak = _calculate_savings_streak_from_daily_flow(
                daily_net_flow
            )
        else:
            current_savings_streak = 0

        # Get streak data from Supabase
        streaks_data = {}
        for streak in user_streaks:
            streak_type = streak.get("streak_type", "")
            streaks_data[streak_type] = {
                "current_count": streak.get("current_count", 0),
                "highest_count": streak.get("highest_count", 0),
                "last_tracked_date": streak.get("last_tracked_date"),
            }

        # Prefer persisted savings streak if present.
        persisted_savings_streak = int(
            streaks_data.get("savings", {}).get("current_count", 0) or 0
        )
        current_savings_streak = max(current_savings_streak, persisted_savings_streak)

        return {
            "current_savings_streak": current_savings_streak,
            "streaks": streaks_data,
            "longest_streak": max(
                [s.get("highest_count", 0) for s in user_streaks], default=0
            ),
        }

    except Exception as e:
        logger.error(f"Error calculating streak metrics: {str(e)}")
        return {"current_savings_streak": 0, "streaks": {}, "longest_streak": 0}


def _calculate_savings_streak_from_daily_flow(daily_net_flow: Dict[str, float]) -> int:
    """Calculate current savings streak from daily net flows.

    For bank statement data, we count consecutive days with positive net flow,
    starting from the most recent transaction date. Days with no transaction data
    are treated as neutral (don't break the streak but also don't add to it).

    A streak is broken when a day has NEGATIVE net flow (spent more than earned).
    """
    from datetime import timedelta

    if not daily_net_flow:
        return 0

    # Find the most recent date with transactions
    sorted_dates = sorted(daily_net_flow.keys(), reverse=True)
    if not sorted_dates:
        return 0

    # Parse the most recent date
    try:
        most_recent_date = datetime.strptime(sorted_dates[0], "%Y-%m-%d").date()
    except ValueError:
        return 0

    streak = 0
    days_without_data = 0
    MAX_GAP_DAYS = (
        3  # Allow up to 3 days gap without breaking streak (weekends, holidays)
    )
    SAVINGS_STREAK_MAX_DAYS = 90  # Look back up to 90 days

    for i in range(SAVINGS_STREAK_MAX_DAYS):
        check_date = most_recent_date - timedelta(days=i)
        date_key = check_date.strftime("%Y-%m-%d")
        net_flow = daily_net_flow.get(date_key, None)

        if net_flow is None:
            # No transaction data for this day
            days_without_data += 1
            if days_without_data > MAX_GAP_DAYS:
                # Too many consecutive days without data, stop counting
                break
            continue

        # Reset the gap counter when we find data
        days_without_data = 0

        if net_flow > 0:
            streak += 1
        elif net_flow < 0:
            break  # Streak broken - spent more than earned
        # net_flow == 0 means income equals expenses - neutral, continue

    return streak
