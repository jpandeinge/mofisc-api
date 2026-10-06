# api/app/services/user_metrics/metrics_service.py
"""
Main orchestrator for user metrics calculations.
Coordinates between calculators, storage, and utilities modules.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client
from ..financial.industry_standard_calculator import IndustryStandardFinancialCalculator
from .calculators import (
    calculate_achievement_metrics,
    calculate_activity_metrics,
    calculate_industry_standard_financial_metrics,
    calculate_level_from_points,
    calculate_points_to_next_level,
    calculate_streak_metrics,
    get_user_points_metrics,
)

# Import from refactored modules
from .storage import store_calculated_metrics
from .utils import (
    get_cached_financial_metrics,
    get_default_financial_metrics,
    get_default_metrics,
)

logger = get_logger(__name__)


class UserMetricsSupabaseService:
    """
    Production-ready service for calculating and managing comprehensive user financial and achievement metrics.
    Uses Supabase for data storage and retrieval with industry-standard financial calculations.
    """

    def __init__(self):
        self.financial_calculator = IndustryStandardFinancialCalculator()

    def calculate_comprehensive_user_metrics(
        self,
        user_id: str,
        force_recalculate: bool = False,
        statement_id: Optional[str] = None,
        bank_name: Optional[str] = None,
        max_transactions: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive metrics for a user using Supabase data.

        Args:
            user_id: User identifier
            force_recalculate: Whether to force recalculation regardless of cache
            statement_id: Optional statement ID to calculate metrics for a specific statement.
                         If None, calculates overall metrics across all transactions.
            bank_name: Optional bank name to filter metrics for a specific bank.
                      If None, calculates overall metrics across all banks.

        Returns:
            Dictionary containing comprehensive user metrics

        Raises:
            ValueError: If user_id is invalid
            Exception: If data retrieval or calculation fails
        """
        # Input validation
        if not user_id or not isinstance(user_id, str):
            raise ValueError("Invalid user_id provided")

        try:
            scope_parts = []
            if statement_id:
                scope_parts.append(f"statement {statement_id}")
            if bank_name:
                scope_parts.append(f"bank {bank_name}")
            scope = ", ".join(scope_parts) if scope_parts else "all transactions"
            logger.info(
                f"Calculating comprehensive metrics for user {user_id} ({scope})"
            )
            supabase = get_supabase_client(use_service_role=True)

            # Check cache first if not forcing recalculation
            if not force_recalculate:
                cached_metrics = get_cached_financial_metrics(
                    supabase,
                    user_id,
                    statement_id,
                    calculate_achievement_metrics,
                    calculate_streak_metrics,
                    calculate_activity_metrics,
                    calculate_level_from_points,
                    calculate_points_to_next_level,
                    bank_name=bank_name,
                )
                if cached_metrics:
                    # Ensure cached metrics match the current visibility window.
                    # This prevents paywall bypass when the user changes tier.
                    cached_tx_count = int(
                        cached_metrics.get("transaction_count", 0) or 0
                    )

                    if max_transactions is not None and max_transactions > 0:
                        if cached_tx_count > max_transactions:
                            cached_metrics = None
                    else:
                        # Unlimited visibility: only use cache if it covers the full dataset
                        try:
                            count_response = (
                                supabase.table("transactions")
                                .select("id", count="exact")
                                .eq("user_id", user_id)
                                .execute()
                            )
                            total_transactions = (
                                count_response.count if count_response.count else 0
                            )
                            if cached_tx_count != total_transactions:
                                cached_metrics = None
                        except Exception:
                            cached_metrics = None

                    if cached_metrics:
                        logger.info(
                            f"Using cached metrics for user {user_id} ({scope})"
                        )
                        return cached_metrics

            # 1. Get user's transaction data from Supabase
            try:
                # For paywall enforcement, we cap FIRST (most recent N overall) and then apply filters
                # in-memory. This prevents bypassing the visibility window via bank/statement filters.
                if max_transactions is not None and max_transactions > 0:
                    response = (
                        supabase.table("transactions")
                        .select("*")
                        .eq("user_id", user_id)
                        .order("transaction_date", desc=True)
                        .range(0, max_transactions - 1)
                        .execute()
                    )
                    user_transactions = response.data if response.data else []

                    if statement_id:
                        user_transactions = [
                            t
                            for t in user_transactions
                            if t.get("statement_id") == statement_id
                        ]
                    if bank_name:
                        user_transactions = [
                            t
                            for t in user_transactions
                            if t.get("bank_name") == bank_name
                        ]
                else:
                    query = (
                        supabase.table("transactions")
                        .select("*")
                        .eq("user_id", user_id)
                    )

                    # Filter by statement_id if provided
                    if statement_id:
                        query = query.eq("statement_id", statement_id)

                    # Filter by bank_name if provided
                    if bank_name:
                        query = query.eq("bank_name", bank_name)

                    response = query.order("transaction_date", desc=True).execute()
                    user_transactions = response.data if response.data else []

                if not user_transactions:
                    logger.info(
                        f"No transactions found for user {user_id} ({scope}), returning default metrics"
                    )
                    return get_default_metrics()
            except Exception as e:
                logger.error(
                    f"Error fetching transactions for user {user_id}: {str(e)}"
                )
                raise Exception(f"Failed to fetch user transactions: {str(e)}")

            # 2. Calculate financial metrics using industry-standard calculator
            try:
                logger.info(
                    f"[METRICS-CALC] Calculating financial metrics for user {user_id} ({len(user_transactions)} transactions)"
                )
                financial_metrics = calculate_industry_standard_financial_metrics(
                    user_transactions, self.financial_calculator
                )
                logger.info(
                    f"[METRICS-CALC] Financial metrics complete for user {user_id}"
                )
            except Exception as e:
                logger.error(
                    f"Error calculating financial metrics for user {user_id}: {str(e)}"
                )
                financial_metrics = get_default_financial_metrics()

            # 3. Calculate achievement-related metrics
            try:
                logger.info(
                    f"[METRICS-CALC] Calculating achievement metrics for user {user_id}"
                )
                achievement_metrics = calculate_achievement_metrics(
                    supabase, user_id, user_transactions
                )
                logger.info(
                    f"[METRICS-CALC] Achievement metrics complete for user {user_id}"
                )
            except Exception as e:
                logger.error(
                    f"Error calculating achievement metrics for user {user_id}: {str(e)}"
                )
                achievement_metrics = {
                    "real_achievements": {
                        "transaction_milestones": 0,
                        "savings_achievements": 0,
                        "streak_achievements": 0,
                        "balance_achievements": 0,
                    },
                    "total_real_achievements": 0,
                    "completed_achievements": 0,
                    "achievement_progress": {
                        "completed": 0,
                        "in_progress": 0,
                        "total": 0,
                    },
                }

            # 4. Calculate streak metrics
            try:
                logger.info(
                    f"[METRICS-CALC] Calculating streak metrics for user {user_id}"
                )
                streak_metrics = calculate_streak_metrics(
                    supabase, user_id, user_transactions
                )
                logger.info(
                    f"[METRICS-CALC] Streak metrics complete for user {user_id}"
                )
            except Exception as e:
                logger.error(
                    f"Error calculating streak metrics for user {user_id}: {str(e)}"
                )
                streak_metrics = {
                    "current_savings_streak": 0,
                    "streaks": {},
                    "longest_streak": 0,
                }

            # 5. Calculate user points and level from persisted gamification tables.
            try:
                points_metrics = get_user_points_metrics(supabase, user_id)

                # Backfill from completed records if user_points is missing.
                if int(points_metrics.get("total_points", 0) or 0) == 0:
                    achievements_points_response = (
                        supabase.table("user_achievements")
                        .select("achievement:achievements(points)")
                        .eq("user_id", user_id)
                        .eq("is_completed", True)
                        .execute()
                    )
                    achievements_points = sum(
                        int((row.get("achievement") or {}).get("points", 0) or 0)
                        for row in (achievements_points_response.data or [])
                    )

                    challenge_points_response = (
                        supabase.table("user_challenges")
                        .select("challenge:daily_challenges(points)")
                        .eq("user_id", user_id)
                        .eq("is_completed", True)
                        .execute()
                    )
                    challenge_points = sum(
                        int((row.get("challenge") or {}).get("points", 0) or 0)
                        for row in (challenge_points_response.data or [])
                    )

                    backfilled_total = int(achievements_points + challenge_points)
                    backfilled_level = calculate_level_from_points(backfilled_total)
                    points_metrics = {
                        "total_points": backfilled_total,
                        "current_level": backfilled_level,
                        "points_to_next_level": calculate_points_to_next_level(
                            backfilled_total, backfilled_level
                        ),
                    }
            except Exception as e:
                logger.error(
                    f"Error calculating points metrics for user {user_id}: {str(e)}"
                )
                points_metrics = {
                    "total_points": 0,
                    "current_level": 1,
                    "points_to_next_level": 100,
                }

            # 6. Calculate activity statistics
            try:
                activity_metrics = calculate_activity_metrics(user_transactions)
            except Exception as e:
                logger.error(
                    f"Error calculating activity metrics for user {user_id}: {str(e)}"
                )
                activity_metrics = {
                    "days_active": 0,
                    "avg_transactions_per_day": 0,
                    "most_active_day": None,
                    "recent_activity": False,
                }

            # Combine all metrics
            metrics = {
                **financial_metrics,
                **achievement_metrics,
                **streak_metrics,
                **points_metrics,
                **activity_metrics,
                "last_calculated": datetime.utcnow().isoformat(),
                "calculation_source": "backend_supabase",
                "backend_verification": "BACKEND_DATA_ACTIVE_2025",
            }

            # Store metrics in Supabase for caching
            store_calculated_metrics(
                supabase,
                user_id,
                metrics,
                self.financial_calculator,
                statement_id,
                user_transactions,
            )

            logger.info(
                f"Successfully calculated comprehensive metrics for user {user_id}"
            )
            return metrics

        except ValueError:
            # Re-raise expected exceptions
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error calculating comprehensive user metrics for {user_id}: {str(e)}",
                exc_info=True,
            )
            raise Exception(f"Failed to calculate user metrics: {str(e)}")

    @staticmethod
    def update_user_metrics_on_transaction_change(user_id: str):
        """
        Update user metrics when transactions change.
        This should be called after transaction CRUD operations.
        """
        try:
            logger.info(
                f"Updating user metrics for user {user_id} due to transaction change"
            )
            service = UserMetricsSupabaseService()
            metrics = service.calculate_comprehensive_user_metrics(
                user_id, force_recalculate=True
            )
            return metrics

        except Exception as e:
            logger.error(f"Error updating user metrics on transaction change: {str(e)}")
            raise
