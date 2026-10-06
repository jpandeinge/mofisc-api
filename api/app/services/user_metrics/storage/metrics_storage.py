# api/app/services/user_metrics/storage/metrics_storage.py
"""
Storage operations for user metrics.
Handles persisting calculated metrics to various Supabase tables.
"""

import math
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ....utils.logging_config import get_logger
from ..config import UserMetricsConfig

logger = get_logger(__name__)


def calculate_level_from_points_static(points: int) -> int:
    """Calculate user level from total points"""
    return int(math.sqrt(points / UserMetricsConfig.LEVEL_CALCULATION_BASE)) + 1


def store_calculated_metrics(
    supabase,
    user_id: str,
    metrics: Dict[str, Any],
    financial_calculator,
    statement_id: Optional[str] = None,
    transactions: Optional[List[Dict]] = None,
):
    """Store calculated metrics in Supabase tables"""
    try:
        # Store metrics in multiple tables for proper data structure
        update_user_achievements(supabase, user_id, metrics)
        update_user_points(supabase, user_id, metrics)
        update_user_streaks(supabase, user_id, metrics)
        update_user_financial_metrics(
            supabase, user_id, metrics, statement_id, transactions
        )

        # Store category spending analysis and related financial data
        if transactions:
            update_user_category_spending(
                supabase, user_id, transactions, financial_calculator, statement_id
            )
            update_user_cash_flow_data(
                supabase, user_id, transactions, financial_calculator
            )
            update_user_monthly_trends(
                supabase, user_id, transactions, financial_calculator
            )
            update_user_subscriptions(
                supabase, user_id, transactions, financial_calculator
            )
            update_daily_challenges(supabase)
            update_user_challenges(supabase, user_id, transactions, metrics)

        # Also store in cache table for quick access
        metrics_data = {
            "user_id": user_id,
            "metrics": metrics,
            "calculated_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }

        (
            supabase.table("user_metrics_cache")
            .upsert(metrics_data, on_conflict="user_id")
            .execute()
        )
        logger.info(f"Stored calculated metrics for user {user_id} in all tables")

    except Exception as e:
        logger.warning(
            f"Could not store calculated metrics for user {user_id}: {str(e)}"
        )
        # Not critical if caching fails


def reconcile_completed_achievements(supabase, user_id: str) -> int:
    """Finalize legacy achievement rows that already reached 100% progress.

    Completion is automatic rather than a separate claim action. Filtering on
    incomplete rows makes retries and concurrent repairs idempotent and lets
    the database reward trigger run only on the false-to-true transition.
    """
    try:
        response = (
            supabase.table("user_achievements")
            .update(
                {
                    "is_completed": True,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("user_id", user_id)
            .eq("is_completed", False)
            .gte("progress", 100)
            .execute()
        )
        repaired_count = len(response.data or [])
        if repaired_count:
            logger.info(
                "Finalized %s completed achievement records for user %s",
                repaired_count,
                user_id,
            )
        return repaired_count
    except Exception as e:
        logger.warning(
            "Could not reconcile completed achievements for user %s: %s",
            user_id,
            str(e),
        )
        return 0


def update_user_achievements(supabase, user_id: str, _metrics: Dict[str, Any]) -> int:
    """Reconcile completion state after metrics calculation.

    Progress belongs to the event-driven achievement service. Aggregate
    category counts cannot identify which achievement was earned, so this
    storage pass must never manufacture achievement records.
    """
    return reconcile_completed_achievements(supabase, user_id)


def _get_completed_reward_points(supabase, user_id: str) -> Optional[int]:
    """Fetch completed achievement and challenge reward points."""
    try:
        achievements_response = (
            supabase.table("user_achievements")
            .select("achievement:achievements(points)")
            .eq("user_id", user_id)
            .eq("is_completed", True)
            .execute()
        )
        achievement_points = sum(
            int((row.get("achievement") or {}).get("points", 0) or 0)
            for row in (achievements_response.data or [])
        )

        challenges_response = (
            supabase.table("user_challenges")
            .select("challenge:daily_challenges(points)")
            .eq("user_id", user_id)
            .eq("is_completed", True)
            .execute()
        )
        challenge_points = sum(
            int((row.get("challenge") or {}).get("points", 0) or 0)
            for row in (challenges_response.data or [])
        )

        return int(achievement_points + challenge_points)
    except Exception as e:
        logger.warning(f"Could not fetch completed reward points: {str(e)}")
        return None


def update_user_points(supabase, user_id: str, metrics: Dict[str, Any]):
    """Update or create user points record"""
    try:
        # Calculate total points from completed rewards and status bonuses.
        reward_points = _get_completed_reward_points(supabase, user_id)
        if reward_points is None:
            reward_points = 0
            real_achievements = metrics.get("real_achievements", {})
            for achievement_type, count in real_achievements.items():
                reward_points += int(count or 0) * 100

        # Add bonus points for financial health and streaks
        financial_score = metrics.get("financial_score", 0)
        streak_bonus = (
            metrics.get("current_savings_streak", 0) * 10
        )  # 10 points per day streak
        financial_bonus = max(
            0, financial_score - 50
        )  # Bonus points for score above 50

        total_points = int(reward_points + streak_bonus + financial_bonus)
        current_level = calculate_level_from_points_static(total_points)

        # Check if user points record exists
        existing_points = (
            supabase.table("user_points").select("*").eq("user_id", user_id).execute()
        )

        if existing_points.data:
            # Update existing record
            points_data = {
                "points": int(total_points),
                "level": current_level,
                "updated_at": datetime.utcnow().isoformat(),
            }
            (
                supabase.table("user_points")
                .update(points_data)
                .eq("user_id", user_id)
                .execute()
            )
        else:
            # Create new record with UUID
            points_data = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "points": int(total_points),
                "level": current_level,
            }
            supabase.table("user_points").insert(points_data).execute()

    except Exception as e:
        logger.error(f"Error updating user points: {str(e)}")


def update_user_streaks(supabase, user_id: str, metrics: Dict[str, Any]):
    """Update or create user streaks records"""
    try:
        current_savings_streak = metrics.get("current_savings_streak", 0)
        longest_streak = metrics.get("longest_streak", 0)

        # Check if user streaks record exists
        existing_streaks = (
            supabase.table("user_streaks")
            .select("*")
            .eq("user_id", user_id)
            .eq("streak_type", "savings")
            .execute()
        )

        if existing_streaks.data:
            # Update existing record
            streak_data = {
                "current_count": current_savings_streak,
                "highest_count": max(current_savings_streak, longest_streak),
                "last_tracked_date": datetime.utcnow().date().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
            (
                supabase.table("user_streaks")
                .update(streak_data)
                .eq("user_id", user_id)
                .eq("streak_type", "savings")
                .execute()
            )
        else:
            # Create new record with UUID
            streak_data = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "streak_type": "savings",
                "current_count": current_savings_streak,
                "highest_count": max(current_savings_streak, longest_streak),
                "last_tracked_date": datetime.utcnow().date().isoformat(),
            }
            supabase.table("user_streaks").insert(streak_data).execute()

    except Exception as e:
        logger.error(f"Error updating user streaks: {str(e)}")


def update_user_financial_metrics(
    supabase,
    user_id: str,
    metrics: Dict[str, Any],
    statement_id: Optional[str] = None,
    transactions: Optional[List[Dict]] = None,
):
    """
    Update or create user financial metrics records following industry standards.
    Stores comprehensive financial health metrics in the user_financial_metrics table.
    """
    try:
        # First, mark all existing records as not current for this user/statement combination
        try:
            update_query = (
                supabase.table("user_financial_metrics")
                .update({"is_current": False})
                .eq("user_id", user_id)
            )

            # Filter by statement_id presence
            if statement_id:
                update_query = update_query.eq("statement_id", statement_id)
            else:
                update_query = update_query.is_("statement_id", "null")

            update_query.execute()
        except Exception as mark_error:
            logger.warning(
                f"Could not mark old financial metrics as not current: {str(mark_error)}"
            )

        # Calculate period start and end from actual transaction data
        if transactions and len(transactions) > 0:
            # Extract dates from transactions
            transaction_dates = []
            for t in transactions:
                try:
                    date_str = t.get("transaction_date", "")
                    if date_str:
                        date_obj = datetime.fromisoformat(
                            date_str.replace("Z", "+00:00")
                        ).date()
                        transaction_dates.append(date_obj)
                except Exception:
                    logger.warning(f"Could not parse transaction date: {date_str}")
                    continue

            if transaction_dates:
                period_start = min(transaction_dates)
                period_end = max(transaction_dates)
            else:
                # Fallback to default
                period_end = datetime.utcnow().date()
                period_start = (datetime.utcnow() - timedelta(days=365)).date()
        else:
            # No transactions provided, use default
            period_end = datetime.utcnow().date()
            period_start = (datetime.utcnow() - timedelta(days=365)).date()

        # Build comprehensive financial metrics record following industry standards
        financial_metrics_data = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "statement_id": statement_id,
            "calculation_date": datetime.utcnow().isoformat(),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            # Financial Health Score components
            "financial_health_score": float(metrics.get("financial_score", 50)),
            "dti_score": float(metrics.get("dti_score", 0)),
            "savings_score": float(metrics.get("savings_score", 0)),
            "cash_flow_score": float(
                metrics.get("component_scores", {}).get("cash_flow", 70)
            ),
            "behavior_score": float(
                metrics.get("component_scores", {}).get("behavior", 70)
            ),
            # Raw metrics for calculations
            "total_income": float(metrics.get("gross_monthly_income", 0)) * 12,
            "total_expenses": float(metrics.get("monthly_expenses", 0)) * 12,
            "total_debt_payments": float(metrics.get("monthly_debt_payments", 0)) * 12,
            "net_cash_flow": float(
                metrics.get("gross_monthly_income", 0)
                - metrics.get("monthly_expenses", 0)
            )
            * 12,
            "latest_balance": float(metrics.get("liquid_savings", 0)),
            # Transaction counts and patterns
            "transaction_count": metrics.get("transaction_count", 0),
            "overdraft_count": 0,
            "expense_volatility": 0.0,
            # Recommendations and insights
            "recommendations": metrics.get("recommendations", {}),
            "insights": {
                "avg_monthly_income": float(metrics.get("gross_monthly_income", 0)),
                "avg_monthly_expenses": float(metrics.get("monthly_expenses", 0)),
                "debt_to_income_ratio": float(metrics.get("dti_ratio", 0)) * 100,
                "savings_rate": float(metrics.get("savings_rate_percentage", 0)),
                "emergency_fund_months": float(metrics.get("emergency_fund_months", 0)),
                "financial_health_category": metrics.get(
                    "financial_health_category", "unknown"
                ),
                "income_consistency_score": float(
                    metrics.get("income_consistency_score", 0)
                ),
            },
            "score_interpretation": metrics.get(
                "financial_health_category", "Insufficient data"
            ),
            # Metadata
            "is_current": True,
        }

        # Insert new financial metrics record
        response = (
            supabase.table("user_financial_metrics")
            .insert(financial_metrics_data)
            .execute()
        )

        if response.data:
            scope = f"statement {statement_id}" if statement_id else "overall"
            logger.info(
                f"Successfully stored {scope} financial metrics for user {user_id} with score {financial_metrics_data['financial_health_score']}"
            )
        else:
            scope = f"statement {statement_id}" if statement_id else "overall"
            logger.warning(
                f"Financial metrics insert returned no data for user {user_id} ({scope})"
            )

    except Exception as e:
        logger.error(
            f"Error updating user financial metrics for user {user_id}: {str(e)}",
            exc_info=True,
        )


def update_user_category_spending(
    supabase,
    user_id: str,
    transactions: List[Dict],
    financial_calculator,
    statement_id: Optional[str] = None,
):
    """Calculate and store category spending analysis in user_category_spending table."""
    try:
        if not transactions:
            logger.debug(
                f"No transactions provided for category spending analysis for user {user_id}"
            )
            return

        # Calculate period start and end from transactions
        transaction_dates = []
        for t in transactions:
            try:
                date_str = t.get("transaction_date", "")
                if date_str:
                    date_obj = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    ).date()
                    transaction_dates.append(date_obj)
            except Exception:
                continue

        if not transaction_dates:
            logger.warning(
                f"No valid transaction dates found for category spending analysis for user {user_id}"
            )
            return

        period_start = min(transaction_dates)
        period_end = max(transaction_dates)

        # Group transactions by category and calculate spending
        category_data = defaultdict(
            lambda: {
                "total_amount": Decimal("0"),
                "transaction_count": 0,
                "transactions": [],
            }
        )

        total_expenses = Decimal("0")

        for transaction in transactions:
            try:
                # Only analyze debit transactions (expenses)
                transaction_type = transaction.get("transaction_type", "").lower()
                if transaction_type != "debit":
                    continue

                # Categorize transaction
                category = financial_calculator.categorize_transaction(transaction)
                category_name = category.value

                # Get transaction amount
                amount = financial_calculator.safe_decimal(
                    transaction.get("transaction_amount", 0)
                )

                if amount > 0:
                    category_data[category_name]["total_amount"] += amount
                    category_data[category_name]["transaction_count"] += 1
                    category_data[category_name]["transactions"].append(transaction)
                    total_expenses += amount

            except Exception as e:
                logger.warning(
                    f"Error processing transaction for category spending: {str(e)}"
                )
                continue

        if total_expenses == 0:
            logger.info(
                f"No expense transactions found for category spending analysis for user {user_id}"
            )
            return

        # Calculate trends
        category_trends = _calculate_category_trends(
            supabase, user_id, category_data, period_start, period_end
        )

        # Prepare category spending records for insertion
        category_records = []

        for category_name, data in category_data.items():
            total_amount = float(data["total_amount"])
            transaction_count = data["transaction_count"]
            average_amount = (
                total_amount / transaction_count if transaction_count > 0 else 0
            )
            percentage_of_expenses = (
                (float(data["total_amount"]) / float(total_expenses) * 100)
                if total_expenses > 0
                else 0
            )
            # Cap percentage to fit NUMERIC(5,2) constraint (max 999.99)
            percentage_of_expenses = min(999.99, percentage_of_expenses)

            # Get trend data for this category
            trend_info = category_trends.get(category_name, {})

            # Cap trend percentage to fit NUMERIC(5,2) constraint (max 999.99)
            trend_percentage = trend_info.get("percentage", None)
            if trend_percentage is not None:
                trend_percentage = max(-999.99, min(999.99, trend_percentage))

            category_record = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "category": category_name,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "total_amount": total_amount,
                "transaction_count": transaction_count,
                "average_amount": average_amount,
                "percentage_of_expenses": round(percentage_of_expenses, 2),
                "trend_direction": trend_info.get("direction", None),
                "trend_percentage": trend_percentage,
            }

            category_records.append(category_record)

        # Delete existing records for this period and user
        try:
            supabase.table("user_category_spending").delete().eq("user_id", user_id).eq(
                "period_start", period_start.isoformat()
            ).eq("period_end", period_end.isoformat()).execute()
        except Exception as delete_error:
            logger.warning(
                f"Could not delete existing category spending records: {str(delete_error)}"
            )

        # Insert new category spending records
        if category_records:
            response = (
                supabase.table("user_category_spending")
                .insert(category_records)
                .execute()
            )

            if response.data:
                logger.info(
                    f"Successfully stored {len(category_records)} category spending records for user {user_id} (period: {period_start} to {period_end})"
                )
            else:
                logger.warning(
                    f"Category spending insert returned no data for user {user_id}"
                )

    except Exception as e:
        logger.error(
            f"Error updating user category spending for user {user_id}: {str(e)}",
            exc_info=True,
        )


def _calculate_category_trends(
    supabase,
    user_id: str,
    current_category_data: Dict,
    current_period_start: date,
    current_period_end: date,
) -> Dict[str, Dict[str, Any]]:
    """Calculate spending trends by comparing current period to previous period."""
    try:
        # Calculate previous period dates
        period_duration = (current_period_end - current_period_start).days
        previous_period_end = current_period_start - timedelta(days=1)
        previous_period_start = previous_period_end - timedelta(days=period_duration)

        # Fetch previous period category spending
        previous_response = (
            supabase.table("user_category_spending")
            .select("category, total_amount")
            .eq("user_id", user_id)
            .gte("period_start", previous_period_start.isoformat())
            .lte("period_end", previous_period_end.isoformat())
            .execute()
        )

        if not previous_response.data:
            logger.debug(
                f"No previous period data found for trend calculation for user {user_id}"
            )
            return {}

        # Build previous period spending map
        previous_spending = {}
        for record in previous_response.data:
            category = record.get("category")
            amount = float(record.get("total_amount", 0))
            if category:
                previous_spending[category] = amount

        # Calculate trends
        trends = {}

        for category_name, current_data in current_category_data.items():
            current_amount = float(current_data["total_amount"])
            previous_amount = previous_spending.get(category_name, 0)

            if previous_amount > 0:
                change_percentage = (
                    (current_amount - previous_amount) / previous_amount
                ) * 100

                # Determine trend direction
                if change_percentage > 5:
                    direction = "increasing"
                elif change_percentage < -5:
                    direction = "decreasing"
                else:
                    direction = "stable"

                trends[category_name] = {
                    "direction": direction,
                    "percentage": round(change_percentage, 2),
                }
            elif current_amount > 0:
                trends[category_name] = {"direction": "increasing", "percentage": 100.0}

        return trends

    except Exception as e:
        logger.error(f"Error calculating category trends: {str(e)}")
        return {}


def update_user_cash_flow_data(
    supabase, user_id: str, transactions: List[Dict], financial_calculator
):
    """Calculate and store daily cash flow data in user_cash_flow_data table."""
    try:
        if not transactions:
            logger.debug(
                f"No transactions provided for cash flow data calculation for user {user_id}"
            )
            return

        # Group transactions by date and calculate daily metrics
        daily_data = defaultdict(
            lambda: {
                "income": Decimal("0"),
                "expenses": Decimal("0"),
                "count": 0,
                "balance": Decimal("0"),
                "date": None,
            }
        )

        for transaction in transactions:
            try:
                # Extract transaction date
                date_str = transaction.get("transaction_date", "")
                if not date_str:
                    continue

                date_obj = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).date()
                date_key = date_obj.isoformat()

                # Extract transaction details
                transaction_type = transaction.get("transaction_type", "").lower()
                amount = financial_calculator.safe_decimal(
                    transaction.get("transaction_amount", 0)
                )
                balance = financial_calculator.safe_decimal(
                    transaction.get("balance", 0)
                )

                # Update daily data
                daily_data[date_key]["date"] = date_obj
                daily_data[date_key]["count"] += 1
                daily_data[date_key]["balance"] = balance

                if transaction_type == "credit":
                    daily_data[date_key]["income"] += amount
                elif transaction_type == "debit":
                    daily_data[date_key]["expenses"] += amount

            except Exception as e:
                logger.warning(f"Error processing transaction for cash flow: {str(e)}")
                continue

        if not daily_data:
            logger.info(
                f"No valid transactions found for cash flow data calculation for user {user_id}"
            )
            return

        # Prepare cash flow records for insertion
        cash_flow_records = []

        for date_key, data in daily_data.items():
            net_flow = float(data["income"] - data["expenses"])

            cash_flow_record = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "date": date_key,
                "income": float(data["income"]),
                "expenses": float(data["expenses"]),
                "net_flow": net_flow,
                "running_balance": float(data["balance"]),
                "transaction_count": data["count"],
            }

            cash_flow_records.append(cash_flow_record)

        # Delete existing cash flow data for this user
        try:
            supabase.table("user_cash_flow_data").delete().eq(
                "user_id", user_id
            ).execute()
        except Exception as delete_error:
            logger.warning(
                f"Could not delete existing cash flow data: {str(delete_error)}"
            )

        # Insert new cash flow records
        if cash_flow_records:
            cash_flow_records.sort(key=lambda x: x["date"])
            response = (
                supabase.table("user_cash_flow_data")
                .insert(cash_flow_records)
                .execute()
            )

            if response.data:
                logger.info(
                    f"Successfully stored {len(cash_flow_records)} cash flow records for user {user_id}"
                )
            else:
                logger.warning(
                    f"Cash flow data insert returned no data for user {user_id}"
                )

    except Exception as e:
        logger.error(
            f"Error updating user cash flow data for user {user_id}: {str(e)}",
            exc_info=True,
        )


def update_user_monthly_trends(
    supabase, user_id: str, transactions: List[Dict], financial_calculator
):
    """Calculate and store monthly trends comparison in user_monthly_trends table."""
    try:
        if not transactions:
            logger.debug(
                f"No transactions provided for monthly trends calculation for user {user_id}"
            )
            return

        # Group transactions by month
        monthly_data = defaultdict(
            lambda: {
                "income": Decimal("0"),
                "expenses": Decimal("0"),
                "count": 0,
                "balance": Decimal("0"),
                "month": None,
            }
        )

        for transaction in transactions:
            try:
                # Extract transaction date
                date_str = transaction.get("transaction_date", "")
                if not date_str:
                    continue

                date_obj = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).date()
                month_key = date_obj.replace(day=1)

                # Extract transaction details
                transaction_type = transaction.get("transaction_type", "").lower()
                amount = financial_calculator.safe_decimal(
                    transaction.get("transaction_amount", 0)
                )
                balance = financial_calculator.safe_decimal(
                    transaction.get("balance", 0)
                )

                # Update monthly data
                monthly_data[month_key]["month"] = month_key
                monthly_data[month_key]["count"] += 1
                monthly_data[month_key]["balance"] = balance

                if transaction_type == "credit":
                    monthly_data[month_key]["income"] += amount
                elif transaction_type == "debit":
                    monthly_data[month_key]["expenses"] += amount

            except Exception as e:
                logger.warning(
                    f"Error processing transaction for monthly trends: {str(e)}"
                )
                continue

        if len(monthly_data) < 2:
            logger.info(
                f"Insufficient data for monthly trends (need at least 2 months) for user {user_id}"
            )
            return

        # Sort months chronologically
        sorted_months = sorted(monthly_data.keys())

        # Calculate trends for each month
        monthly_trend_records = []

        for i in range(1, len(sorted_months)):
            current_month = sorted_months[i]
            previous_month = sorted_months[i - 1]

            current_data = monthly_data[current_month]
            previous_data = monthly_data[previous_month]

            # Calculate percentage changes
            def calc_change_pct(current: Decimal, previous: Decimal) -> float:
                if previous == 0:
                    return min(999.99, 100.0) if current > 0 else 0.0
                change_pct = float(((current - previous) / previous) * 100)
                return max(-999.99, min(999.99, change_pct))

            income_change = calc_change_pct(
                current_data["income"], previous_data["income"]
            )
            expenses_change = calc_change_pct(
                current_data["expenses"], previous_data["expenses"]
            )
            balance_change = calc_change_pct(
                current_data["balance"], previous_data["balance"]
            )
            count_change = calc_change_pct(
                Decimal(current_data["count"]), Decimal(previous_data["count"])
            )

            monthly_trend_record = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "month": current_month.isoformat(),
                "income_current": float(current_data["income"]),
                "income_previous": float(previous_data["income"]),
                "income_change_pct": round(income_change, 2),
                "expenses_current": float(current_data["expenses"]),
                "expenses_previous": float(previous_data["expenses"]),
                "expenses_change_pct": round(expenses_change, 2),
                "balance_current": float(current_data["balance"]),
                "balance_previous": float(previous_data["balance"]),
                "balance_change_pct": round(balance_change, 2),
                "transaction_count_current": current_data["count"],
                "transaction_count_previous": previous_data["count"],
                "transaction_count_change_pct": round(count_change, 2),
            }

            monthly_trend_records.append(monthly_trend_record)

        # Delete existing monthly trends for this user
        try:
            supabase.table("user_monthly_trends").delete().eq(
                "user_id", user_id
            ).execute()
        except Exception as delete_error:
            logger.warning(
                f"Could not delete existing monthly trends: {str(delete_error)}"
            )

        # Insert new monthly trend records
        if monthly_trend_records:
            response = (
                supabase.table("user_monthly_trends")
                .insert(monthly_trend_records)
                .execute()
            )

            if response.data:
                logger.info(
                    f"Successfully stored {len(monthly_trend_records)} monthly trend records for user {user_id}"
                )
            else:
                logger.warning(
                    f"Monthly trends insert returned no data for user {user_id}"
                )

    except Exception as e:
        logger.error(
            f"Error updating user monthly trends for user {user_id}: {str(e)}",
            exc_info=True,
        )


def update_user_subscriptions(
    supabase, user_id: str, transactions: List[Dict], financial_calculator
):
    """Detect and store recurring subscriptions using category-based intelligent analysis."""
    try:
        if not transactions:
            logger.debug(
                f"No transactions provided for subscription detection for user {user_id}"
            )
            return

        # Define subscription-likely categories
        subscription_categories = {
            "utilities",
            "insurance",
            "subscriptions",
        }

        # Known subscription keywords
        known_subscription_keywords = {
            "netflix",
            "nflx",
            "disney+",
            "disneyplus",
            "prime video",
            "amazon prime",
            "hbo max",
            "hbomax",
            "apple tv+",
            "youtube premium",
            "youtube music",
            "crunchyroll",
            "showmax",
            "dstv",
            "gotv",
            "multichoice",
            "box office",
            "spotify",
            "apple music",
            "tidal",
            "shazam",
            "deezer",
            "soundcloud",
            "audible",
            "scribd",
            "icloud storage",
            "google drive",
            "google storage",
            "google one",
            "dropbox",
            "microsoft 365",
            "office 365",
            "google workspace",
            "gsuite",
            "onedrive",
            "notion",
            "evernote",
            "nordvpn",
            "1password",
            "lastpass",
            "expressvpn",
            "protonvpn",
            "protonmail",
            "github",
            "adobe creative cloud",
            "adobe cc",
            "figma",
            "canva",
            "twitter blue",
            "x premium",
            "discord nitro",
            "linkedin premium",
            "playstation plus",
            "xbox game pass",
            "apple arcade",
            "ea play",
            "strava",
            "peloton",
            "fitbit premium",
            "coursera",
            "udemy",
            "duolingo",
            "datacamp",
            "mtc",
            "tn mobile",
            "airtime",
            "prepaid",
            "mobile data",
            "electricity",
            "e&water",
            "city of windhoek",
            "nored",
            "water elec",
            "g4s",
            "foursg4s",
            "namibia security",
            "security service",
            "medical aid",
            "khomas",
            "psemas",
            "bonlife",
            "sanlam",
            "santam",
            "omlac",
            "legals",
            "holifes",
            "old mutual",
            "virgin active",
            "planet fitness",
            "ironhouse gym",
            "gym membership",
            "openai",
            "chatgpt",
            "midjourney",
            "anthropic",
            "grammarly",
            "patreon",
            "medium",
            "substack",
        }

        # Group transactions by merchant
        merchant_groups = defaultdict(list)

        for transaction in transactions:
            try:
                transaction_type = transaction.get("transaction_type", "").lower()
                if transaction_type != "debit":
                    continue

                description = (
                    transaction.get("transaction_description", "").lower().strip()
                )
                if not description or len(description) < 3:
                    continue

                # Check for known subscription platforms
                matched_platform = None
                for keyword in known_subscription_keywords:
                    if keyword in description:
                        matched_platform = keyword
                        break

                if matched_platform:
                    merchant = matched_platform
                else:
                    # Normalize merchant name
                    merchant = re.sub(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", "", description)
                    merchant = re.sub(r"(?:ref|txn|id|#)[\s:]*[\w\d]+", "", merchant)
                    merchant = re.sub(r"\*{2,}\d+", "", merchant)
                    merchant = re.sub(r"\b(?<![a-z])\d+\b", "", merchant)
                    merchant = re.sub(r"\s+", " ", merchant).strip()

                if len(merchant) < 3:
                    continue

                merchant_groups[merchant].append(transaction)

            except Exception as e:
                logger.warning(
                    f"Error processing transaction for subscription detection: {str(e)}"
                )
                continue

        if not merchant_groups:
            logger.info(f"No potential subscription patterns found for user {user_id}")
            return

        # Analyze each merchant group to identify subscriptions
        subscription_records = []

        for merchant, group_transactions in merchant_groups.items():
            try:
                has_known_keyword = any(
                    keyword in merchant.lower()
                    for keyword in known_subscription_keywords
                )

                # Get the most common category for this merchant
                categories = []
                for t in group_transactions:
                    cat = financial_calculator.categorize_transaction(t)
                    categories.append(cat.value if cat else "uncategorized")
                category_value = (
                    max(set(categories), key=categories.count)
                    if categories
                    else "uncategorized"
                )

                # Different thresholds based on category
                if category_value in subscription_categories or has_known_keyword:
                    min_occurrences = 2
                    is_high_confidence_category = True
                else:
                    min_occurrences = 3
                    is_high_confidence_category = False

                if len(group_transactions) < min_occurrences:
                    continue

                # Extract amounts, dates, and descriptions
                amounts = []
                dates = []
                descriptions = []

                for t in group_transactions:
                    try:
                        amount = financial_calculator.safe_decimal(
                            t.get("transaction_amount", 0)
                        )
                        date_str = t.get("transaction_date", "")
                        desc = t.get("transaction_description", "")

                        if date_str and amount > 0:
                            date_obj = datetime.fromisoformat(
                                date_str.replace("Z", "+00:00")
                            ).date()
                            amounts.append(amount)
                            dates.append(date_obj)
                            descriptions.append(desc)
                    except (AttributeError, TypeError, ValueError):
                        continue

                if len(amounts) < min_occurrences or len(dates) < min_occurrences:
                    continue

                # Filter out payment retries
                filtered_amounts = []
                filtered_dates = []
                filtered_descriptions = []

                sorted_indices = sorted(range(len(dates)), key=lambda i: dates[i])
                last_accepted_date = None
                last_accepted_amount = None

                for idx in sorted_indices:
                    current_date = dates[idx]
                    current_amount = amounts[idx]

                    if last_accepted_date is None:
                        filtered_dates.append(current_date)
                        filtered_amounts.append(current_amount)
                        filtered_descriptions.append(descriptions[idx])
                        last_accepted_date = current_date
                        last_accepted_amount = current_amount
                        continue

                    days_diff = (current_date - last_accepted_date).days
                    amount_diff = (
                        abs(
                            float(current_amount - last_accepted_amount)
                            / float(last_accepted_amount)
                        )
                        if last_accepted_amount > 0
                        else 0
                    )

                    if days_diff <= 7 and amount_diff < 0.05:
                        continue
                    else:
                        filtered_dates.append(current_date)
                        filtered_amounts.append(current_amount)
                        filtered_descriptions.append(descriptions[idx])
                        last_accepted_date = current_date
                        last_accepted_amount = current_amount

                amounts = filtered_amounts
                dates = filtered_dates
                descriptions = filtered_descriptions

                if len(amounts) < min_occurrences or len(dates) < min_occurrences:
                    continue

                # Calculate amount statistics
                avg_amount = sum(amounts) / len(amounts)

                if avg_amount == 0:
                    continue

                amount_variance = sum(
                    (float(a) - float(avg_amount)) ** 2 for a in amounts
                ) / len(amounts)
                amount_std_dev = Decimal(str(amount_variance**0.5))

                # Check amount consistency
                amount_variance_threshold = (
                    0.15 if is_high_confidence_category else 0.10
                )
                amount_consistent = all(
                    abs(float(amount - avg_amount) / float(avg_amount))
                    <= amount_variance_threshold
                    for amount in amounts
                )

                if not amount_consistent:
                    continue

                # Analyze frequency
                sorted_dates = sorted(dates)
                date_diffs = []
                for i in range(1, len(sorted_dates)):
                    diff = (sorted_dates[i] - sorted_dates[i - 1]).days
                    date_diffs.append(diff)

                if not date_diffs:
                    continue

                avg_interval = sum(date_diffs) / len(date_diffs)
                interval_variance = sum(
                    (d - avg_interval) ** 2 for d in date_diffs
                ) / len(date_diffs)
                interval_std_dev = interval_variance**0.5

                # Classify frequency
                if 25 <= avg_interval <= 35:
                    frequency = "monthly"
                elif 82 <= avg_interval <= 98:
                    frequency = "quarterly"
                elif 350 <= avg_interval <= 380:
                    frequency = "yearly"
                elif 6 <= avg_interval <= 8:
                    frequency = "weekly"
                elif 13 <= avg_interval <= 16:
                    frequency = "bi-weekly"
                else:
                    if avg_interval > 0 and interval_std_dev / avg_interval < 0.3:
                        frequency = "irregular"
                    else:
                        continue

                # Calculate confidence score
                occurrence_score = min(1.0, len(group_transactions) / 6.0)
                freq_consistency = 1.0 - min(
                    1.0, interval_std_dev / avg_interval if avg_interval > 0 else 1.0
                )
                amount_consistency = 1.0 - min(
                    1.0,
                    (
                        float(amount_std_dev) / float(avg_amount)
                        if avg_amount > 0
                        else 1.0
                    ),
                )
                category_bonus = (
                    0.10 if category_value in subscription_categories else 0.0
                )
                keyword_bonus = 0.10 if has_known_keyword else 0.0

                confidence_score = (
                    occurrence_score * 0.30
                    + freq_consistency * 0.35
                    + amount_consistency * 0.20
                    + category_bonus
                    + keyword_bonus
                    + 0.05
                )

                confidence_score = min(1.0, confidence_score)

                # Determine if subscription is still active
                latest_transaction_date = max(dates)
                days_since_last = (latest_transaction_date - sorted_dates[-1]).days
                expected_next_payment = avg_interval * 1.5
                is_active = days_since_last <= expected_next_payment

                # Use standardized platform name for known subscriptions
                if has_known_keyword:
                    subscription_name = " ".join(
                        word.capitalize() for word in merchant.split()
                    )
                else:
                    subscription_name = (
                        max(descriptions, key=len) if descriptions else merchant
                    )

                # Create subscription record
                subscription_record = {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "name": subscription_name[:100],
                    "amount": float(avg_amount),
                    "frequency": frequency,
                    "category": category_value,
                    "description_pattern": merchant[:255],
                    "first_detected": min(dates).isoformat(),
                    "last_seen": max(dates).isoformat(),
                    "transaction_count": len(group_transactions),
                    "confidence_score": round(float(confidence_score), 2),
                    "is_active": is_active,
                }

                subscription_records.append(subscription_record)

            except Exception as e:
                logger.warning(
                    f"Error analyzing subscription pattern for {merchant}: {str(e)}"
                )
                continue

        # Merge with existing subscriptions instead of replacing
        if subscription_records:
            # Get existing subscriptions for this user
            existing_response = (
                supabase.table("user_subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            existing_subscriptions = {
                s["description_pattern"]: s for s in (existing_response.data or [])
            }

            records_to_insert = []
            records_to_update = []

            for new_sub in subscription_records:
                pattern = new_sub["description_pattern"]

                if pattern in existing_subscriptions:
                    # Update existing subscription with merged data
                    existing = existing_subscriptions[pattern]
                    updated_record = {
                        "id": existing["id"],
                        "last_seen": max(
                            new_sub["last_seen"],
                            existing.get("last_seen", new_sub["last_seen"]),
                        ),
                        "first_detected": min(
                            new_sub["first_detected"],
                            existing.get("first_detected", new_sub["first_detected"]),
                        ),
                        "transaction_count": existing.get("transaction_count", 0)
                        + new_sub["transaction_count"],
                        "amount": (existing.get("amount", 0) + new_sub["amount"])
                        / 2,  # Average amount
                        "confidence_score": max(
                            new_sub["confidence_score"],
                            existing.get("confidence_score", 0),
                        ),
                        "is_active": new_sub["is_active"],
                    }
                    records_to_update.append(updated_record)
                else:
                    # New subscription
                    records_to_insert.append(new_sub)

            # Update existing subscriptions
            for record in records_to_update:
                try:
                    supabase.table("user_subscriptions").update(
                        {
                            "last_seen": record["last_seen"],
                            "first_detected": record["first_detected"],
                            "transaction_count": record["transaction_count"],
                            "amount": record["amount"],
                            "confidence_score": record["confidence_score"],
                            "is_active": record["is_active"],
                        }
                    ).eq("id", record["id"]).execute()
                except Exception as update_error:
                    logger.warning(
                        f"Could not update subscription {record['id']}: {str(update_error)}"
                    )

            # Insert new subscriptions
            if records_to_insert:
                records_to_insert.sort(
                    key=lambda x: x["confidence_score"], reverse=True
                )
                response = (
                    supabase.table("user_subscriptions")
                    .insert(records_to_insert)
                    .execute()
                )

                if response.data:
                    logger.info(
                        f"Inserted {len(records_to_insert)} new subscription records for user {user_id}"
                    )
                else:
                    logger.warning(
                        f"Subscriptions insert returned no data for user {user_id}"
                    )

            # Log summary
            active_count = sum(1 for s in subscription_records if s["is_active"])
            high_confidence = sum(
                1 for s in subscription_records if s["confidence_score"] >= 0.7
            )
            logger.info(
                f"Successfully processed {len(subscription_records)} subscriptions for user {user_id} "
                f"({len(records_to_update)} updated, {len(records_to_insert)} new, "
                f"{active_count} active, {high_confidence} high-confidence)"
            )
        else:
            logger.info(f"No recurring subscriptions detected for user {user_id}")

    except Exception as e:
        logger.error(
            f"Error updating user subscriptions for user {user_id}: {str(e)}",
            exc_info=True,
        )


def sync_subscription_names_from_edits(
    supabase, user_id: str, edited_transaction_id: Optional[str] = None
):
    """
    Sync subscription display names from edited transaction merchant names.

    Modes:
    - Real-time: edited_transaction_id provided → sync related subscription only
    - Batch: no transaction_id → sync all subscriptions for user

    Logic:
    1. Get user's active subscriptions
    2. Get transactions with edited merchant names
    3. Match transactions to subscriptions via description_pattern
    4. Update subscription name if merchant name changed (respects user_display_name)
    """
    try:
        # Get subscriptions
        subscriptions = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .is_("soft_deleted_at", "null")
            .execute()
        )

        if not subscriptions.data:
            return

        # Get edited transactions
        query = (
            supabase.table("transactions")
            .select(
                "id, transaction_description, merchant_name, transaction_amount, transaction_date"
            )
            .eq("user_id", user_id)
            .not_.is_("merchant_name", "null")
        )

        if edited_transaction_id:
            query = query.eq("id", edited_transaction_id)

        transactions = query.execute()

        if not transactions.data:
            return

        # Match and update
        updates = []

        for subscription in subscriptions.data:
            pattern = subscription.get("description_pattern", "").lower()
            if not pattern:
                continue

            # Find matching transactions
            matching = [
                txn
                for txn in transactions.data
                if pattern in txn.get("transaction_description", "").lower()
                and abs(
                    float(txn["transaction_amount"]) - float(subscription["amount"])
                )
                < 1.0
            ]

            if matching:
                # Get most recent merchant name
                latest = max(matching, key=lambda x: x["transaction_date"])
                latest_merchant = latest["merchant_name"]

                # Only update if changed and no user override
                if (
                    latest_merchant
                    and latest_merchant != subscription.get("last_synced_merchant_name")
                    and not subscription.get("user_display_name")
                ):

                    updates.append(
                        {
                            "id": subscription["id"],
                            "name": latest_merchant,
                            "last_synced_merchant_name": latest_merchant,
                        }
                    )

        # Batch update
        for update in updates:
            supabase.table("user_subscriptions").update(
                {
                    "name": update["name"],
                    "last_synced_merchant_name": update["last_synced_merchant_name"],
                    "updated_at": datetime.utcnow().isoformat(),
                }
            ).eq("id", update["id"]).execute()

        logger.info(f"Synced {len(updates)} subscription names for user {user_id}")

    except Exception as e:
        logger.error(f"Error syncing subscription names: {str(e)}", exc_info=True)


def update_daily_challenges(supabase):
    """Generate and maintain daily challenges automatically."""
    try:
        now = datetime.utcnow()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        # Challenges expire at tomorrow's midnight. Checking ``< tomorrow``
        # excluded that exact boundary and generated another batch every time
        # metrics were recalculated.
        existing = (
            supabase.table("daily_challenges")
            .select("id")
            .gt("expires_at", now.isoformat())
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if existing.data:
            return

        # Deactivate expired challenges
        supabase.table("daily_challenges").update({"is_active": False}).lte(
            "expires_at", now.isoformat()
        ).execute()

        # Define challenge templates
        challenge_templates = [
            {
                "title": "Transaction Tracker",
                "description": "Log at least 3 transactions today to build awareness of your spending",
                "challenge_type": "transaction_count",
                "points": 15,
                "target_value": 3.0,
            },
            {
                "title": "Spending Limit",
                "description": "Keep your spending under N$500 today",
                "challenge_type": "spending_limit",
                "points": 20,
                "target_value": 500.0,
            },
            {
                "title": "Category Master",
                "description": "Categorize 5 transactions correctly today",
                "challenge_type": "categorization",
                "points": 10,
                "target_value": 5.0,
            },
            {
                "title": "No Impulse",
                "description": "Avoid any entertainment or shopping purchases today",
                "challenge_type": "avoid_category",
                "points": 25,
                "target_value": 0.0,
            },
            {
                "title": "Savings Champion",
                "description": "Transfer at least N$100 to savings today",
                "challenge_type": "savings_transfer",
                "points": 30,
                "target_value": 100.0,
            },
            {
                "title": "Bill Reminder",
                "description": "Pay at least one bill on time today",
                "challenge_type": "bill_payment",
                "points": 15,
                "target_value": 1.0,
            },
            {
                "title": "Essential Only",
                "description": "Only spend on essential categories today (groceries, utilities, transport)",
                "challenge_type": "essential_only",
                "points": 25,
                "target_value": 1.0,
            },
            {
                "title": "Budget Check",
                "description": "Review your spending in at least 3 categories",
                "challenge_type": "category_review",
                "points": 10,
                "target_value": 3.0,
            },
            {
                "title": "Cash Flow Positive",
                "description": "End today with more income than expenses",
                "challenge_type": "positive_cash_flow",
                "points": 20,
                "target_value": 1.0,
            },
            {
                "title": "Early Bird",
                "description": "Log your first transaction before 9 AM",
                "challenge_type": "early_transaction",
                "points": 10,
                "target_value": 9.0,
            },
        ]

        # Deterministic daily selection and IDs make concurrent recalculations
        # converge on one batch across API workers.
        import random

        daily_random = random.Random(today.isoformat())
        selected_challenges = daily_random.sample(
            challenge_templates, min(3, len(challenge_templates))
        )

        # Create challenge records
        challenge_records = []
        for challenge in selected_challenges:
            challenge_record = {
                "id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            f"mofisc:daily-challenge:{today.isoformat()}:"
                            f"{challenge['challenge_type']}"
                        ),
                    )
                ),
                "title": challenge["title"],
                "description": challenge["description"],
                "challenge_type": challenge["challenge_type"],
                "points": challenge["points"],
                "target_value": challenge["target_value"],
                "expires_at": tomorrow.isoformat(),
                "is_active": True,
            }
            challenge_records.append(challenge_record)

        # Insert daily challenges
        if challenge_records:
            response = (
                supabase.table("daily_challenges")
                .upsert(challenge_records, on_conflict="id")
                .execute()
            )
            if response.data:
                logger.info(
                    f"Successfully created {len(challenge_records)} daily challenges for {today}"
                )
            else:
                logger.warning("Daily challenges insert returned no data")

    except Exception as e:
        logger.error(f"Error updating daily challenges: {str(e)}", exc_info=True)


def update_user_challenges(
    supabase, user_id: str, transactions: List[Dict], metrics: Dict[str, Any]
):
    """Update user progress on active daily challenges."""
    try:
        # Get active daily challenges
        today = datetime.utcnow().date()
        active_challenges = (
            supabase.table("daily_challenges")
            .select("*")
            .eq("is_active", True)
            .gte("expires_at", today.isoformat())
            .execute()
        )

        if not active_challenges.data:
            return

        # Get today's transactions
        today_transactions = []
        for t in transactions:
            try:
                date_str = t.get("transaction_date", "")
                if date_str:
                    trans_date = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    ).date()
                    if trans_date == today:
                        today_transactions.append(t)
            except (AttributeError, TypeError, ValueError):
                continue

        # Evaluate each challenge for this user
        for challenge in active_challenges.data:
            try:
                challenge_id = challenge["id"]
                challenge_type = challenge["challenge_type"]
                target_value = float(challenge["target_value"])

                # Check if user already has progress on this challenge
                existing_progress = (
                    supabase.table("user_challenges")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("challenge_id", challenge_id)
                    .execute()
                )

                # Calculate current progress based on challenge type
                current_progress = 0.0
                is_completed = False

                if challenge_type == "transaction_count":
                    current_progress = len(today_transactions)
                    is_completed = current_progress >= target_value

                elif challenge_type == "spending_limit":
                    total_spending = sum(
                        float(t.get("transaction_amount", 0))
                        for t in today_transactions
                        if t.get("transaction_type", "").lower() == "debit"
                    )
                    current_progress = target_value - total_spending
                    is_completed = total_spending <= target_value

                elif challenge_type == "savings_transfer":
                    savings_amount = sum(
                        float(t.get("transaction_amount", 0))
                        for t in today_transactions
                        if "savings" in t.get("transaction_description", "").lower()
                    )
                    current_progress = savings_amount
                    is_completed = savings_amount >= target_value

                elif challenge_type == "positive_cash_flow":
                    income = sum(
                        float(t.get("transaction_amount", 0))
                        for t in today_transactions
                        if t.get("transaction_type", "").lower() == "credit"
                    )
                    expenses = sum(
                        float(t.get("transaction_amount", 0))
                        for t in today_transactions
                        if t.get("transaction_type", "").lower() == "debit"
                    )
                    current_progress = income - expenses
                    is_completed = income > expenses

                # Update or create progress record
                # Schema: progress, is_completed, completed_at (see schemas/achievement.py)
                progress_data = {
                    "progress": current_progress,
                    "is_completed": is_completed,
                }

                # Add completed_at timestamp if completed
                if is_completed:
                    progress_data["completed_at"] = datetime.utcnow().isoformat()

                if existing_progress.data:
                    supabase.table("user_challenges").update(progress_data).eq(
                        "user_id", user_id
                    ).eq("challenge_id", challenge_id).execute()
                else:
                    progress_data.update(
                        {
                            "id": str(uuid.uuid4()),
                            "user_id": user_id,
                            "challenge_id": challenge_id,
                        }
                    )
                    supabase.table("user_challenges").insert(progress_data).execute()

            except Exception as challenge_error:
                logger.warning(
                    f"Error processing challenge {challenge.get('id', 'unknown')}: {str(challenge_error)}"
                )
                continue

        logger.info(f"Successfully updated challenges for user {user_id}")

    except Exception as e:
        logger.error(f"Error updating user challenges for user {user_id}: {str(e)}")
