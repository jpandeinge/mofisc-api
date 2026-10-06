# api/app/services/user_metrics/calculators/activity.py

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from ....utils.logging_config import get_logger
from ..config import UserMetricsConfig

logger = get_logger(__name__)


def get_user_points_metrics(supabase, user_id: str) -> Dict[str, Any]:
    """Get user points and level metrics from Supabase"""
    try:
        response = (
            supabase.table("user_points").select("*").eq("user_id", user_id).execute()
        )

        if response.data and len(response.data) > 0:
            user_points_data = response.data[0]
            points = user_points_data.get("points", 0)
            level = user_points_data.get("level", 1)
            return {
                "total_points": points,
                "current_level": level,
                "points_to_next_level": calculate_points_to_next_level(points, level),
            }
        else:
            # Return default values that will be updated when metrics are calculated
            return {"total_points": 0, "current_level": 1, "points_to_next_level": 100}

    except Exception as e:
        logger.error(f"Error getting user points metrics: {str(e)}")
        return {"total_points": 0, "current_level": 1, "points_to_next_level": 100}


def calculate_points_to_next_level(current_points: int, current_level: int) -> int:
    """Calculate points needed for next level"""
    # Level calculation: level = floor(sqrt(points / LEVEL_CALCULATION_BASE)) + 1
    # So points for level n = (n-1)^2 * LEVEL_CALCULATION_BASE
    next_level_points = (current_level**2) * UserMetricsConfig.LEVEL_CALCULATION_BASE
    return max(0, next_level_points - current_points)


def calculate_level_from_points(total_points: int) -> int:
    """Calculate level from total points"""
    if total_points <= 0:
        return 1
    level = int(math.sqrt(total_points / UserMetricsConfig.LEVEL_CALCULATION_BASE)) + 1
    return max(1, level)


def calculate_activity_metrics(transactions: List[Dict]) -> Dict[str, Any]:
    """Calculate user activity metrics"""
    if not transactions:
        return {
            "days_active": 0,
            "avg_transactions_per_day": 0,
            "most_active_day": None,
            "recent_activity": False,
        }

    try:
        # Calculate unique active days
        active_dates = set()
        daily_transaction_count = defaultdict(int)

        for t in transactions:
            date_str = t.get("transaction_date", "")
            if not date_str:
                continue

            try:
                date_obj = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).date()
                date_key = date_obj.strftime("%Y-%m-%d")
                active_dates.add(date_key)
                daily_transaction_count[date_key] += 1
            except (AttributeError, TypeError, ValueError):
                continue

        days_active = len(active_dates)
        avg_transactions_per_day = len(transactions) / max(days_active, 1)

        # Find most active day
        most_active_day = max(
            daily_transaction_count.items(), key=lambda x: x[1], default=(None, 0)
        )

        # Check recent activity (last 7 days)
        recent_cutoff = datetime.now().date() - timedelta(days=7)
        recent_activity = any(
            datetime.fromisoformat(
                t.get("transaction_date", "").replace("Z", "+00:00")
            ).date()
            >= recent_cutoff
            for t in transactions
            if t.get("transaction_date")
        )

        return {
            "days_active": days_active,
            "avg_transactions_per_day": round(avg_transactions_per_day, 2),
            "most_active_day": most_active_day[0] if most_active_day[0] else None,
            "most_active_day_count": most_active_day[1],
            "recent_activity": recent_activity,
        }

    except Exception as e:
        logger.error(f"Error calculating activity metrics: {str(e)}")
        return {
            "days_active": 0,
            "avg_transactions_per_day": 0,
            "most_active_day": None,
            "recent_activity": False,
        }
