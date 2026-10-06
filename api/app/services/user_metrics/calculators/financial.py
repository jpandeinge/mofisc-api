# api/app/services/user_metrics/calculators/financial.py

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List

from ....utils.logging_config import get_logger
from ...financial.industry_standard_calculator import (
    IndustryStandardFinancialCalculator,
)

logger = get_logger(__name__)


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


def calculate_industry_standard_financial_metrics(
    transactions: List[Dict],
    financial_calculator: IndustryStandardFinancialCalculator,
    months: int = 12,
) -> Dict[str, Any]:
    """Calculate financial metrics using industry-standard methods with comprehensive edge case handling"""
    try:
        if not transactions:
            return get_default_financial_metrics()

        # Calculate income metrics
        income_metrics = financial_calculator.calculate_monthly_income(
            transactions, months
        )

        # Calculate expense metrics
        expense_metrics = financial_calculator.calculate_monthly_expenses(
            transactions, months
        )

        # Calculate actual savings (money moved to savings/investment accounts)
        monthly_savings = calculate_actual_monthly_savings(
            transactions, financial_calculator, months
        )

        # Calculate liquid savings amount (for emergency fund calculation)
        liquid_savings = estimate_liquid_savings(transactions, financial_calculator)

        # Calculate DTI ratio
        dti_metrics = financial_calculator.calculate_debt_to_income_ratio(
            expense_metrics["debt_payments_monthly"],
            income_metrics["gross_monthly_income"],
        )

        # Calculate emergency fund coverage
        emergency_metrics = financial_calculator.calculate_emergency_fund_coverage(
            liquid_savings, expense_metrics["essential_monthly_expenses"]
        )

        # Calculate savings rate
        savings_metrics = financial_calculator.calculate_savings_rate(
            monthly_savings, income_metrics["gross_monthly_income"]
        )

        # Calculate comprehensive financial health score
        health_score_metrics = (
            financial_calculator.calculate_comprehensive_financial_health_score(
                dti_metrics["dti_score"],
                emergency_metrics["emergency_fund_score"],
                savings_metrics["savings_score"],
            )
        )

        # Calculate legacy metrics for backward compatibility
        total_income = float(income_metrics["gross_monthly_income"]) * months
        total_expenses = float(expense_metrics["total_monthly_expenses"]) * months
        net_cash_flow = total_income - total_expenses

        # Calculate spending trend (simplified for now)
        spending_trend = calculate_simple_spending_trend(
            transactions, financial_calculator
        )

        # Calculate savings streak (updated method)
        savings_streak = calculate_improved_savings_streak(
            transactions, financial_calculator
        )

        return {
            # Core financial metrics (industry standard)
            "gross_monthly_income": float(income_metrics["gross_monthly_income"]),
            "monthly_expenses": float(expense_metrics["total_monthly_expenses"]),
            "essential_expenses": float(expense_metrics["essential_monthly_expenses"]),
            "discretionary_expenses": float(
                expense_metrics["discretionary_monthly_expenses"]
            ),
            "monthly_debt_payments": float(expense_metrics["debt_payments_monthly"]),
            "monthly_savings": float(monthly_savings),
            "liquid_savings": float(liquid_savings),
            # Industry-standard ratios and scores
            "dti_ratio": float(dti_metrics["dti_ratio"]),
            "dti_score": dti_metrics["dti_score"],
            "emergency_fund_months": float(emergency_metrics["months_coverage"]),
            "emergency_fund_score": emergency_metrics["emergency_fund_score"],
            "actual_savings_rate": float(savings_metrics["savings_rate"]),
            "savings_rate_percentage": float(
                savings_metrics["savings_rate_percentage"]
            ),
            "savings_score": savings_metrics["savings_score"],
            "financial_score": health_score_metrics["financial_health_score"],
            "financial_health_category": health_score_metrics["category"],
            # Legacy metrics for backward compatibility
            "savings_streak": savings_streak,
            "total_savings": net_cash_flow,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "avg_daily_spending": float(expense_metrics["total_monthly_expenses"]) / 30,
            "savings_rate": float(savings_metrics["savings_rate"]),  # For compatibility
            "spending_trend": spending_trend,
            "transaction_count": len(transactions),
            # Additional insights
            "income_consistency_score": float(income_metrics["consistency_score"]),
            "component_scores": health_score_metrics["component_scores"],
            "recommendations": {
                "dti": dti_metrics["recommendation"],
                "emergency_fund": emergency_metrics["recommendation"],
                "savings": savings_metrics["recommendation"],
                "overall": health_score_metrics["recommendation"],
            },
        }

    except Exception as e:
        logger.error(f"Error calculating industry-standard financial metrics: {str(e)}")
        return get_default_financial_metrics()


def calculate_actual_monthly_savings(
    transactions: List[Dict],
    financial_calculator: IndustryStandardFinancialCalculator,
    months: int,
) -> Decimal:
    """Calculate actual money moved to savings/investment accounts"""
    try:
        savings_categories = [
            "emergency_savings",
            "retirement_savings",
            "investments",
            "general_savings",
        ]

        total_savings = Decimal("0")
        for transaction in transactions:
            category = financial_calculator.categorize_transaction(transaction)
            if category.value in savings_categories:
                amount = financial_calculator.safe_decimal(
                    transaction.get("transaction_amount", 0)
                )
                if (
                    transaction.get("transaction_type", "").lower() == "debit"
                ):  # Money moved out to savings
                    total_savings += amount

        return total_savings / Decimal(str(months)) if months > 0 else Decimal("0")

    except Exception as e:
        logger.error(f"Error calculating actual monthly savings: {str(e)}")
        return Decimal("0")


def estimate_liquid_savings(
    transactions: List[Dict], financial_calculator: IndustryStandardFinancialCalculator
) -> Decimal:
    """Estimate liquid savings from transaction patterns"""
    try:
        # This is a simplified estimation - in a real system, you'd track account balances
        # For now, we'll estimate based on net positive transfers to savings accounts
        savings_transfers = Decimal("0")

        for transaction in transactions:
            category = financial_calculator.categorize_transaction(transaction)
            if category.value in ["emergency_savings", "general_savings"]:
                amount = financial_calculator.safe_decimal(
                    transaction.get("transaction_amount", 0)
                )
                if transaction.get("transaction_type", "").lower() == "debit":
                    savings_transfers += amount

        # Estimate liquid savings as 70% of total savings transfers (conservative estimate)
        return savings_transfers * Decimal("0.7")

    except Exception as e:
        logger.error(f"Error estimating liquid savings: {str(e)}")
        return Decimal("0")


def calculate_simple_spending_trend(
    transactions: List[Dict], financial_calculator: IndustryStandardFinancialCalculator
) -> str:
    """Calculate spending trend using improved methodology"""
    try:
        if len(transactions) < 14:
            return "stable"

        # Group expenses by week for trend analysis
        weekly_expenses = defaultdict(Decimal)

        for transaction in transactions:
            if transaction.get("transaction_type", "").lower() != "debit":
                continue

            try:
                date_obj = datetime.fromisoformat(
                    transaction.get("transaction_date", "").replace("Z", "+00:00")
                )
                week_key = f"{date_obj.year}-{date_obj.isocalendar()[1]:02d}"
                amount = financial_calculator.safe_decimal(
                    transaction.get("transaction_amount", 0)
                )
                weekly_expenses[week_key] += amount
            except (AttributeError, TypeError, ValueError):
                continue

        if len(weekly_expenses) < 2:
            return "stable"

        # Compare recent weeks to earlier weeks
        sorted_weeks = sorted(weekly_expenses.keys())
        half_point = len(sorted_weeks) // 2

        recent_avg = sum(
            weekly_expenses[week] for week in sorted_weeks[half_point:]
        ) / len(sorted_weeks[half_point:])
        earlier_avg = sum(
            weekly_expenses[week] for week in sorted_weeks[:half_point]
        ) / len(sorted_weeks[:half_point])

        if earlier_avg > 0:
            change_ratio = float(recent_avg / earlier_avg)
            if change_ratio > 1.15:  # 15% increase
                return "up"
            elif change_ratio < 0.85:  # 15% decrease
                return "down"

        return "stable"

    except Exception as e:
        logger.error(f"Error calculating spending trend: {str(e)}")
        return "stable"


def calculate_improved_savings_streak(
    transactions: List[Dict], financial_calculator: IndustryStandardFinancialCalculator
) -> int:
    """Calculate savings streak based on actual savings transactions"""
    try:
        # Group savings transactions by date
        daily_savings = defaultdict(Decimal)

        for transaction in transactions:
            category = financial_calculator.categorize_transaction(transaction)
            if category.value in [
                "emergency_savings",
                "retirement_savings",
                "investments",
                "general_savings",
            ]:
                try:
                    date_obj = datetime.fromisoformat(
                        transaction.get("transaction_date", "").replace("Z", "+00:00")
                    ).date()
                    date_key = date_obj.strftime("%Y-%m-%d")
                    amount = financial_calculator.safe_decimal(
                        transaction.get("transaction_amount", 0)
                    )

                    if transaction.get("transaction_type", "").lower() == "debit":
                        daily_savings[date_key] += amount
                except (AttributeError, TypeError, ValueError):
                    continue

        # Calculate streak from most recent date backwards
        today = datetime.now().date()
        streak = 0

        for i in range(30):  # Check last 30 days
            check_date = today - timedelta(days=i)
            date_key = check_date.strftime("%Y-%m-%d")

            if daily_savings.get(date_key, 0) > 0:
                streak += 1
            else:
                break  # Streak broken

        return streak

    except Exception as e:
        logger.error(f"Error calculating improved savings streak: {str(e)}")
        return 0


def calculate_financial_behavior_metrics(
    transactions: List[Dict], financial_calculator: IndustryStandardFinancialCalculator
) -> Dict[str, Any]:
    """Calculate financial behavior metrics from transaction data"""
    if not transactions:
        return get_default_financial_metrics()

    try:
        # Sort transactions by date
        sorted_transactions = sorted(
            transactions, key=lambda x: x.get("transaction_date", "")
        )

        # Calculate daily net flows for savings streak
        daily_net_flow = defaultdict(float)
        for t in sorted_transactions:
            date_str = t.get("transaction_date", "")
            if not date_str:
                continue

            # Parse date string to get date key
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

        # Calculate savings streak
        savings_streak = _calculate_savings_streak_from_daily_flow(daily_net_flow)

        # Calculate total metrics
        total_income = sum(
            float(t.get("transaction_amount", 0))
            for t in transactions
            if t.get("transaction_type", "").lower() == "credit"
        )

        total_expenses = sum(
            float(t.get("transaction_amount", 0))
            for t in transactions
            if t.get("transaction_type", "").lower() == "debit"
        )

        net_cash_flow = total_income - total_expenses

        # Calculate average daily spending
        if sorted_transactions:
            try:
                start_date = datetime.fromisoformat(
                    sorted_transactions[0]
                    .get("transaction_date", "")
                    .replace("Z", "+00:00")
                ).date()
                end_date = datetime.fromisoformat(
                    sorted_transactions[-1]
                    .get("transaction_date", "")
                    .replace("Z", "+00:00")
                ).date()
                date_range = (end_date - start_date).days
                avg_daily_spending = total_expenses / max(date_range, 1)
            except (AttributeError, TypeError, ValueError):
                avg_daily_spending = total_expenses / max(len(transactions), 1)
        else:
            avg_daily_spending = 0

        # Calculate savings rate
        savings_rate = (net_cash_flow / total_income * 100) if total_income > 0 else 0

        # Calculate spending trend
        spending_trend = _calculate_spending_trend_from_daily_flow(daily_net_flow)

        # Calculate financial health score
        financial_score = _calculate_financial_health_score_simple(
            savings_rate, savings_streak, spending_trend, len(transactions)
        )

        return {
            "savings_streak": savings_streak,
            "total_savings": net_cash_flow,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "avg_daily_spending": avg_daily_spending,
            "savings_rate": savings_rate,
            "spending_trend": spending_trend,
            "financial_score": financial_score,
            "transaction_count": len(transactions),
        }

    except Exception as e:
        logger.error(f"Error calculating financial behavior metrics: {str(e)}")
        return get_default_financial_metrics()


def _calculate_savings_streak_from_daily_flow(daily_net_flow: Dict[str, float]) -> int:
    """Calculate current savings streak from daily net flows"""
    if not daily_net_flow:
        return 0

    # Get last N days sorted by date (configurable)
    today = datetime.now().date()
    streak = 0
    SAVINGS_STREAK_MAX_DAYS = 30  # From UserMetricsConfig

    for i in range(SAVINGS_STREAK_MAX_DAYS):
        check_date = today - timedelta(days=i)
        date_key = check_date.strftime("%Y-%m-%d")

        if daily_net_flow.get(date_key, 0) > 0:
            streak += 1
        else:
            break

    return streak


def _calculate_spending_trend_from_daily_flow(daily_net_flow: Dict[str, float]) -> str:
    """Calculate spending trend from daily net flows"""
    if len(daily_net_flow) < 2:
        return "stable"

    sorted_dates = sorted(daily_net_flow.keys())
    half_point = len(sorted_dates) // 2

    if half_point == 0:
        return "stable"

    recent_avg = sum(-daily_net_flow[d] for d in sorted_dates[half_point:]) / len(
        sorted_dates[half_point:]
    )
    earlier_avg = sum(-daily_net_flow[d] for d in sorted_dates[:half_point]) / len(
        sorted_dates[:half_point]
    )

    if earlier_avg > 0:
        change_ratio = recent_avg / earlier_avg
        if change_ratio > 1.15:  # 15% increase
            return "up"
        elif change_ratio < 0.85:  # 15% decrease
            return "down"

    return "stable"


def _calculate_financial_health_score_simple(
    savings_rate: float,
    savings_streak: int,
    spending_trend: str,
    transaction_count: int,
) -> float:
    """Calculate a simplified financial health score"""
    score = 50.0  # Base score

    # Savings rate component (up to 25 points)
    if savings_rate >= 20:
        score += 25
    elif savings_rate >= 10:
        score += 15
    elif savings_rate >= 0:
        score += 5

    # Savings streak component (up to 15 points)
    if savings_streak >= 20:
        score += 15
    elif savings_streak >= 10:
        score += 10
    elif savings_streak > 0:
        score += 5

    # Spending trend component (up to 10 points)
    if spending_trend == "down":
        score += 10
    elif spending_trend == "stable":
        score += 5

    # Transaction count component (up to 10 points)
    if transaction_count >= 100:
        score += 10
    elif transaction_count >= 50:
        score += 5

    return min(score, 100.0)
