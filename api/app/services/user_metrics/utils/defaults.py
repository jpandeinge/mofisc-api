# api/app/services/user_metrics/utils/defaults.py

from datetime import datetime
from typing import Any, Dict


def get_default_metrics() -> Dict[str, Any]:
    """Return default metrics when no data is available"""
    return {
        **get_default_financial_metrics(),
        "real_achievements": {
            "transaction_milestones": 0,
            "savings_achievements": 0,
            "streak_achievements": 0,
            "balance_achievements": 0,
        },
        "total_real_achievements": 0,
        "completed_achievements": 0,
        "achievement_progress": {"completed": 0, "in_progress": 0, "total": 0},
        "current_savings_streak": 0,
        "streaks": {},
        "longest_streak": 0,
        "total_points": 0,
        "current_level": 1,
        "points_to_next_level": 100,
        "days_active": 0,
        "avg_transactions_per_day": 0,
        "most_active_day": None,
        "recent_activity": False,
        "last_calculated": datetime.utcnow().isoformat(),
        "calculation_source": "backend_supabase",
        "backend_verification": "BACKEND_DATA_ACTIVE_2025",
    }


def get_default_financial_metrics() -> Dict[str, Any]:
    """Return default financial metrics"""
    return {
        "savings_streak": 0,
        "total_savings": 0,
        "total_income": 0,
        "total_expenses": 0,
        "avg_daily_spending": 0,
        "savings_rate": 0,
        "spending_trend": "stable",
        "financial_score": 50,
        "transaction_count": 0,
    }
