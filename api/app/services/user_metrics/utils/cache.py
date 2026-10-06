# api/app/services/user_metrics/utils/cache.py

from datetime import datetime
from typing import Any, Dict, Optional

from ....utils.logging_config import get_logger
from ..config import UserMetricsConfig

logger = get_logger(__name__)


def get_cached_financial_metrics(
    supabase,
    user_id: str,
    statement_id: Optional[str],
    calculate_achievement_metrics_func,
    calculate_streak_metrics_func,
    calculate_activity_metrics_func,
    calculate_level_from_points_func,
    calculate_points_to_next_level_func,
    bank_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch cached financial metrics from user_financial_metrics table.
    Returns comprehensive metrics if cache is recent (within threshold), otherwise None.

    Args:
        supabase: Supabase client instance
        user_id: User identifier
        statement_id: Optional statement ID for per-statement metrics
        calculate_achievement_metrics_func: Function to calculate achievement metrics
        calculate_streak_metrics_func: Function to calculate streak metrics
        calculate_activity_metrics_func: Function to calculate activity metrics
        calculate_level_from_points_func: Function to calculate level from points
        calculate_points_to_next_level_func: Function to calculate points to next level
        bank_name: Optional bank name for per-bank metrics

    Returns:
        Cached metrics dictionary or None if cache is stale/missing
    """
    try:
        # Query for current financial metrics
        query = (
            supabase.table("user_financial_metrics")
            .select("*")
            .eq("user_id", user_id)
            .eq("is_current", True)
        )

        # Filter by statement_id presence
        if statement_id:
            query = query.eq("statement_id", statement_id)
        else:
            query = query.is_("statement_id", "null")

        # Filter by bank_name presence
        # Note: For bank-specific metrics, we skip cache for now and always recalculate
        # This ensures accuracy when users switch between banks frequently
        if bank_name:
            logger.debug(
                f"Bank-specific metrics requested for {bank_name}, skipping cache"
            )
            return None

        response = query.order("calculation_date", desc=True).limit(1).execute()

        if not response.data or len(response.data) == 0:
            logger.debug(f"No cached metrics found for user {user_id}")
            return None

        cached_record = response.data[0]

        # Check if cache is recent (within threshold)
        calculation_date = datetime.fromisoformat(
            cached_record["calculation_date"].replace("Z", "+00:00")
        )
        age_minutes = (
            datetime.utcnow() - calculation_date.replace(tzinfo=None)
        ).total_seconds() / 60

        if age_minutes > UserMetricsConfig.METRICS_CACHE_THRESHOLD_MINUTES:
            logger.debug(
                f"Cached metrics for user {user_id} are stale ({age_minutes:.1f} minutes old)"
            )
            return None

        logger.info(
            f"Found fresh cached metrics for user {user_id} (age: {age_minutes:.1f} minutes)"
        )

        # Fetch live achievement, streak, and activity data (these change frequently)
        try:
            # Get live transactions for activity metrics
            trans_query = (
                supabase.table("transactions").select("*").eq("user_id", user_id)
            )
            if statement_id:
                trans_query = trans_query.eq("statement_id", statement_id)
            trans_response = trans_query.order("transaction_date", desc=True).execute()
            user_transactions = trans_response.data if trans_response.data else []

            # Calculate live metrics that change frequently
            achievement_metrics = calculate_achievement_metrics_func(
                supabase, user_id, user_transactions
            )
            streak_metrics = calculate_streak_metrics_func(
                supabase, user_id, user_transactions
            )
            activity_metrics = calculate_activity_metrics_func(user_transactions)

            # Calculate points from cached financial score + live achievements/streaks
            financial_score = float(cached_record["financial_health_score"])
            achievement_points = (
                sum(achievement_metrics.get("real_achievements", {}).values()) * 100
            )
            streak_bonus = streak_metrics.get("current_savings_streak", 0) * 10
            financial_bonus = max(0, financial_score - 50)
            total_points = achievement_points + streak_bonus + financial_bonus
            current_level = calculate_level_from_points_func(total_points)

            points_metrics = {
                "total_points": int(total_points),
                "current_level": current_level,
                "points_to_next_level": calculate_points_to_next_level_func(
                    int(total_points), current_level
                ),
            }

        except Exception as e:
            logger.error(f"Error fetching live metrics for cached data: {str(e)}")
            # Use defaults if live fetch fails
            achievement_metrics = {
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
            streak_metrics = {
                "current_savings_streak": 0,
                "streaks": {},
                "longest_streak": 0,
            }
            activity_metrics = {
                "days_active": 0,
                "avg_transactions_per_day": 0,
                "most_active_day": None,
                "recent_activity": False,
            }
            points_metrics = {
                "total_points": 0,
                "current_level": 1,
                "points_to_next_level": 100,
            }

        # Reconstruct comprehensive metrics from cached financial data + live gamification data
        insights = cached_record.get("insights", {})
        recommendations = cached_record.get("recommendations", {})

        # Build comprehensive metrics dictionary
        metrics = {
            # Cached financial metrics (from user_financial_metrics table)
            "gross_monthly_income": insights.get("avg_monthly_income", 0),
            "monthly_expenses": insights.get("avg_monthly_expenses", 0),
            "essential_expenses": float(cached_record.get("total_expenses", 0))
            * 0.6
            / 12,  # Estimate
            "discretionary_expenses": float(cached_record.get("total_expenses", 0))
            * 0.4
            / 12,  # Estimate
            "monthly_debt_payments": float(cached_record.get("total_debt_payments", 0))
            / 12,
            "monthly_savings": (
                insights.get("avg_monthly_income", 0)
                - insights.get("avg_monthly_expenses", 0)
            ),
            "liquid_savings": float(cached_record.get("latest_balance", 0)),
            # Industry-standard scores (from cache)
            "dti_ratio": insights.get("debt_to_income_ratio", 0) / 100,
            "dti_score": float(cached_record.get("dti_score", 0)),
            "emergency_fund_months": insights.get("emergency_fund_months", 0),
            "emergency_fund_score": 70,  # Estimate if not stored
            "actual_savings_rate": insights.get("savings_rate", 0) / 100,
            "savings_rate_percentage": insights.get("savings_rate", 0),
            "savings_score": float(cached_record.get("savings_score", 0)),
            "financial_score": float(cached_record.get("financial_health_score", 50)),
            "financial_health_category": insights.get(
                "financial_health_category", "unknown"
            ),
            # Legacy metrics for backward compatibility
            "savings_streak": streak_metrics.get("current_savings_streak", 0),
            "total_savings": float(cached_record.get("net_cash_flow", 0)),
            "total_income": float(cached_record.get("total_income", 0)),
            "total_expenses": float(cached_record.get("total_expenses", 0)),
            "avg_daily_spending": insights.get("avg_monthly_expenses", 0) / 30,
            "savings_rate": insights.get("savings_rate", 0) / 100,
            "spending_trend": "stable",  # Could cache this too
            "transaction_count": cached_record.get("transaction_count", 0),
            # Additional insights
            "income_consistency_score": insights.get("income_consistency_score", 0),
            "component_scores": {
                "cash_flow": float(cached_record.get("cash_flow_score", 70)),
                "behavior": float(cached_record.get("behavior_score", 70)),
            },
            "recommendations": recommendations,
            # Live achievement and streak metrics (fetched fresh)
            **achievement_metrics,
            **streak_metrics,
            **points_metrics,
            **activity_metrics,
            # Metadata
            "last_calculated": cached_record["calculation_date"],
            "calculation_source": "cached_with_live_gamification",
            "backend_verification": "BACKEND_DATA_ACTIVE_2025_CACHED",
            "cache_age_minutes": round(age_minutes, 1),
        }

        return metrics

    except Exception as e:
        logger.error(
            f"Error fetching cached financial metrics for user {user_id}: {str(e)}",
            exc_info=True,
        )
        return None
