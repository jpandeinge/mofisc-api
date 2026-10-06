# api/app/services/financial/calculation_service.py

import re
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from ...models.financial_metrics import (
    UserCashFlowData,
    UserCategorySpending,
    UserFinancialMetrics,
    UserMonthlyTrends,
    UserSubscriptions,
)
from ...models.transaction import Transaction
from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class FinancialCalculationService:
    """Service for calculating and storing financial metrics in the database"""

    @staticmethod
    def calculate_and_store_all_metrics(
        db: Session, user_id: str, statement_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculate all financial metrics for a user and store them in the database.
        This replaces frontend calculations and ensures consistency.
        Always calculates for the given statement (if provided) and for all statements (overall).
        """
        results = {}
        # --- Per-statement metrics ---
        if statement_id:
            try:
                query = db.query(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.statement_id == statement_id,
                )
                transactions = query.order_by(Transaction.transaction_date.desc()).all()
                if transactions:
                    dates = [t.transaction_date for t in transactions]
                    period_start = min(dates)
                    period_end = max(dates)
                    db.query(UserFinancialMetrics).filter(
                        and_(
                            UserFinancialMetrics.user_id == user_id,
                            UserFinancialMetrics.statement_id == statement_id,
                        )
                    ).update({"is_current": False})
                    health_metrics = (
                        FinancialCalculationService._calculate_financial_health_score(
                            transactions
                        )
                    )
                    (
                        FinancialCalculationService._calculate_cash_flow_data(
                            db, user_id, transactions
                        )
                    )
                    (
                        FinancialCalculationService._detect_and_store_subscriptions(
                            db, user_id, transactions
                        )
                    )
                    (
                        FinancialCalculationService._calculate_category_spending(
                            db, user_id, transactions, period_start, period_end
                        )
                    )
                    (
                        FinancialCalculationService._calculate_monthly_trends(
                            db, user_id, transactions
                        )
                    )
                    financial_metrics = UserFinancialMetrics(
                        user_id=user_id,
                        statement_id=statement_id,
                        period_start=period_start,
                        period_end=period_end,
                        financial_health_score=health_metrics["score"],
                        dti_score=health_metrics["metrics"]["dtiScore"],
                        savings_score=health_metrics["metrics"]["savingsScore"],
                        cash_flow_score=health_metrics["metrics"]["cashFlowScore"],
                        behavior_score=health_metrics["metrics"]["behaviorScore"],
                        total_income=health_metrics["raw_data"]["total_income"],
                        total_expenses=health_metrics["raw_data"]["total_expenses"],
                        total_debt_payments=health_metrics["raw_data"][
                            "total_debt_payments"
                        ],
                        net_cash_flow=health_metrics["raw_data"]["net_cash_flow"],
                        latest_balance=health_metrics["raw_data"]["latest_balance"],
                        transaction_count=len(transactions),
                        overdraft_count=health_metrics["raw_data"]["overdraft_count"],
                        expense_volatility=health_metrics["raw_data"][
                            "expense_volatility"
                        ],
                        recommendations=health_metrics["recommendations"],
                        insights=health_metrics["insights"],
                        score_interpretation=health_metrics["interpretation"],
                        is_current=True,
                    )
                    db.add(financial_metrics)
                    db.commit()
                    results["statement_metrics"] = financial_metrics.to_dict()
            except Exception as e:
                logger.error(f"Error calculating statement metrics: {str(e)}")
                db.rollback()
                results["statement_metrics_error"] = str(e)
        # --- Overall metrics (all statements) ---
        try:
            query = db.query(Transaction).filter(Transaction.user_id == user_id)
            transactions = query.order_by(Transaction.transaction_date.desc()).all()
            if transactions:
                dates = [t.transaction_date for t in transactions]
                period_start = min(dates)
                period_end = max(dates)
                db.query(UserFinancialMetrics).filter(
                    and_(
                        UserFinancialMetrics.user_id == user_id,
                        UserFinancialMetrics.statement_id.is_(None),
                    )
                ).update({"is_current": False})
                health_metrics = (
                    FinancialCalculationService._calculate_financial_health_score(
                        transactions
                    )
                )
                FinancialCalculationService._calculate_cash_flow_data(
                    db, user_id, transactions
                )
                (
                    FinancialCalculationService._detect_and_store_subscriptions(
                        db, user_id, transactions
                    )
                )
                (
                    FinancialCalculationService._calculate_category_spending(
                        db, user_id, transactions, period_start, period_end
                    )
                )
                FinancialCalculationService._calculate_monthly_trends(
                    db, user_id, transactions
                )
                financial_metrics = UserFinancialMetrics(
                    user_id=user_id,
                    statement_id=None,
                    period_start=period_start,
                    period_end=period_end,
                    financial_health_score=health_metrics["score"],
                    dti_score=health_metrics["metrics"]["dtiScore"],
                    savings_score=health_metrics["metrics"]["savingsScore"],
                    cash_flow_score=health_metrics["metrics"]["cashFlowScore"],
                    behavior_score=health_metrics["metrics"]["behaviorScore"],
                    total_income=health_metrics["raw_data"]["total_income"],
                    total_expenses=health_metrics["raw_data"]["total_expenses"],
                    total_debt_payments=health_metrics["raw_data"][
                        "total_debt_payments"
                    ],
                    net_cash_flow=health_metrics["raw_data"]["net_cash_flow"],
                    latest_balance=health_metrics["raw_data"]["latest_balance"],
                    transaction_count=len(transactions),
                    overdraft_count=health_metrics["raw_data"]["overdraft_count"],
                    expense_volatility=health_metrics["raw_data"]["expense_volatility"],
                    recommendations=health_metrics["recommendations"],
                    insights=health_metrics["insights"],
                    score_interpretation=health_metrics["interpretation"],
                    is_current=True,
                )
                db.add(financial_metrics)
                db.commit()
                results["overall_metrics"] = financial_metrics.to_dict()

                # Calculate and store savings streak and trigger achievement events
                try:
                    from ..achievements import achievement_service

                    # Update savings streak
                    savings_streak_record = achievement_service.update_streak(
                        db, user_id, "savings"
                    )
                    results["savings_streak"] = (
                        savings_streak_record.current_count
                        if savings_streak_record
                        else 0
                    )

                    # Trigger achievement events based on calculated metrics
                    _trigger_financial_achievements(
                        db, user_id, financial_metrics, transactions
                    )

                except Exception as streak_error:
                    logger.warning(
                        f"Failed to calculate achievements and streaks: {str(streak_error)}"
                    )
                    results["savings_streak"] = 0

        except Exception as e:
            logger.error(f"Error calculating overall metrics: {str(e)}")
            db.rollback()
            results["overall_metrics_error"] = str(e)
        return results

    @staticmethod
    def _calculate_financial_health_score(
        transactions: List[Transaction],
    ) -> Dict[str, Any]:
        """Calculate comprehensive financial health score with all components"""

        # Helper function to categorize transactions by month
        def categorize_by_month(
            trans: List[Transaction],
        ) -> Dict[str, List[Transaction]]:
            monthly_data = defaultdict(list)
            for t in trans:
                month = t.transaction_date.strftime("%Y-%m")
                monthly_data[month].append(t)
            return dict(monthly_data)

        # Debt categories for DTI calculation
        debt_categories = [
            "Home Loan",
            "Credit Card",
            "Loan",
            "Furniture Account",
            "Vehicle Finance",
            "Student Loan",
            "Clothing Account",
        ]

        # Calculate monthly metrics
        monthly_data = categorize_by_month(transactions)
        monthly_metrics = []

        for month_trans in monthly_data.values():
            income = sum(
                float(t.transaction_amount)
                for t in month_trans
                if t.transaction_type.lower() == "credit"
            )

            expenses = sum(
                float(t.transaction_amount)
                for t in month_trans
                if t.transaction_type.lower() == "debit"
            )

            debt_payments = sum(
                float(t.transaction_amount)
                for t in month_trans
                if t.category in debt_categories
                and t.transaction_type.lower() == "debit"
            )

            monthly_metrics.append(
                {
                    "income": income,
                    "expenses": expenses,
                    "debt_payments": debt_payments,
                    "savings_rate": (income - expenses) / income if income > 0 else 0,
                    "dti": debt_payments / income if income > 0 else 1,
                    "cash_flow_ratio": income / expenses if expenses > 0 else 2,
                }
            )

        if not monthly_metrics:
            return {
                "score": 0,
                "metrics": {
                    "dtiScore": 0,
                    "savingsScore": 0,
                    "cashFlowScore": 0,
                    "behaviorScore": 0,
                },
                "interpretation": "Insufficient data",
                "recommendations": [],
                "insights": {},
                "raw_data": {
                    "total_income": 0,
                    "total_expenses": 0,
                    "total_debt_payments": 0,
                    "net_cash_flow": 0,
                    "latest_balance": 0,
                    "overdraft_count": 0,
                    "expense_volatility": 0,
                },
            }

        # Calculate component scores
        avg_metrics = {
            "dti": sum(m["dti"] for m in monthly_metrics) / len(monthly_metrics),
            "savings_rate": sum(m["savings_rate"] for m in monthly_metrics)
            / len(monthly_metrics),
            "cash_flow_ratio": sum(m["cash_flow_ratio"] for m in monthly_metrics)
            / len(monthly_metrics),
        }

        dti_score = max(0, min(100, (1 - avg_metrics["dti"] * 2.33) * 100))
        savings_score = max(0, min(100, avg_metrics["savings_rate"] * 500))
        cash_flow_score = max(0, min(100, (avg_metrics["cash_flow_ratio"] / 1.5) * 100))

        # Calculate behavior score
        behavior_score = 100
        overdraft_count = sum(1 for t in transactions if float(t.balance) < 0)
        if overdraft_count > 0:
            behavior_score -= 20

        # Calculate expense volatility
        avg_expenses = sum(m["expenses"] for m in monthly_metrics) / len(
            monthly_metrics
        )
        expense_volatility = sum(
            (m["expenses"] - avg_expenses) ** 2 for m in monthly_metrics
        ) / len(monthly_metrics)

        if expense_volatility > avg_expenses * 0.5:
            behavior_score -= 15
        if expense_volatility > avg_expenses:
            behavior_score -= 15

        behavior_score = max(0, behavior_score)

        # Calculate final weighted score
        final_score = round(
            dti_score * 0.25
            + savings_score * 0.25
            + cash_flow_score * 0.25
            + behavior_score * 0.25
        )

        # Generate recommendations
        recommendations = []
        if dti_score < 70:
            recommendations.append(
                "Consider debt consolidation or accelerated debt repayment"
            )
        if savings_score < 70:
            recommendations.append(
                "Increase monthly savings rate to at least 20% of income"
            )
        if cash_flow_score < 70:
            recommendations.append(
                "Review and optimize monthly expenses to improve cash flow"
            )
        if behavior_score < 70:
            recommendations.append(
                "Work on maintaining consistent spending and saving patterns"
            )

        # Generate interpretation
        if final_score >= 90:
            interpretation = "Excellent financial health"
        elif final_score >= 80:
            interpretation = "Very good financial health"
        elif final_score >= 70:
            interpretation = "Good financial health"
        elif final_score >= 60:
            interpretation = "Fair financial health"
        else:
            interpretation = "Needs improvement"

        # Calculate raw data totals
        total_income = sum(m["income"] for m in monthly_metrics)
        total_expenses = sum(m["expenses"] for m in monthly_metrics)
        total_debt_payments = sum(m["debt_payments"] for m in monthly_metrics)
        latest_balance = float(
            max(transactions, key=lambda t: t.transaction_date).balance
        )

        return {
            "score": final_score,
            "metrics": {
                "dtiScore": round(dti_score, 2),
                "savingsScore": round(savings_score, 2),
                "cashFlowScore": round(cash_flow_score, 2),
                "behaviorScore": round(behavior_score, 2),
            },
            "interpretation": interpretation,
            "recommendations": recommendations,
            "insights": {
                "avg_monthly_income": round(total_income / len(monthly_metrics), 2),
                "avg_monthly_expenses": round(total_expenses / len(monthly_metrics), 2),
                "debt_to_income_ratio": round(avg_metrics["dti"] * 100, 2),
                "savings_rate": round(avg_metrics["savings_rate"] * 100, 2),
            },
            "raw_data": {
                "total_income": total_income,
                "total_expenses": total_expenses,
                "total_debt_payments": total_debt_payments,
                "net_cash_flow": total_income - total_expenses,
                "latest_balance": latest_balance,
                "overdraft_count": overdraft_count,
                "expense_volatility": expense_volatility,
            },
        }

    @staticmethod
    def _calculate_cash_flow_data(
        db: Session, user_id: str, transactions: List[Transaction]
    ) -> List[UserCashFlowData]:
        """Calculate daily cash flow data and store in database"""

        # Clear existing cash flow data for this user
        db.query(UserCashFlowData).filter(UserCashFlowData.user_id == user_id).delete()

        # Group transactions by date
        daily_data = defaultdict(
            lambda: {"income": 0, "expenses": 0, "count": 0, "balance": 0}
        )

        for transaction in transactions:
            date_key = transaction.transaction_date
            amount = float(transaction.transaction_amount)

            daily_data[date_key]["count"] += 1
            daily_data[date_key]["balance"] = float(transaction.balance)

            if transaction.transaction_type.lower() == "credit":
                daily_data[date_key]["income"] += amount
            else:
                daily_data[date_key]["expenses"] += amount

        # Create cash flow records
        cash_flow_records = []
        for date_key, data in daily_data.items():
            net_flow = data["income"] - data["expenses"]

            cash_flow = UserCashFlowData(
                user_id=user_id,
                date=date_key,
                income=data["income"],
                expenses=data["expenses"],
                net_flow=net_flow,
                running_balance=data["balance"],
                transaction_count=data["count"],
            )

            db.add(cash_flow)
            cash_flow_records.append(cash_flow)

        db.commit()
        return cash_flow_records

    @staticmethod
    def _detect_and_store_subscriptions(
        db: Session, user_id: str, transactions: List[Transaction]
    ) -> List[UserSubscriptions]:
        """Detect recurring subscriptions and store them"""

        # Clear existing subscriptions
        db.query(UserSubscriptions).filter(
            UserSubscriptions.user_id == user_id
        ).delete()

        # Group transactions by description pattern
        description_groups = defaultdict(list)

        for transaction in transactions:
            if transaction.transaction_type.lower() == "debit":
                # Normalize description for pattern matching
                desc = transaction.transaction_description.lower()
                # Remove numbers, dates, and special characters for pattern matching
                pattern = re.sub(r"[\d\-/\s]+", " ", desc).strip()
                if len(pattern) > 3:  # Minimum pattern length
                    description_groups[pattern].append(transaction)

        subscription_records = []

        for pattern, group_transactions in description_groups.items():
            if len(group_transactions) >= 2:  # At least 2 occurrences
                amounts = [float(t.transaction_amount) for t in group_transactions]
                dates = [t.transaction_date for t in group_transactions]

                # Check if amounts are similar (within 10% variance)
                avg_amount = sum(amounts) / len(amounts)
                if all(
                    abs(amount - avg_amount) / avg_amount <= 0.1 for amount in amounts
                ):
                    # Calculate frequency
                    date_diffs = []
                    sorted_dates = sorted(dates)
                    for i in range(1, len(sorted_dates)):
                        diff = (sorted_dates[i] - sorted_dates[i - 1]).days
                        date_diffs.append(diff)

                    if date_diffs:
                        avg_days = sum(date_diffs) / len(date_diffs)

                        # Determine frequency
                        if 25 <= avg_days <= 35:
                            frequency = "monthly"
                        elif 350 <= avg_days <= 375:
                            frequency = "yearly"
                        elif 6 <= avg_days <= 8:
                            frequency = "weekly"
                        else:
                            frequency = "irregular"

                        # Create subscription record
                        subscription = UserSubscriptions(
                            user_id=user_id,
                            name=group_transactions[0].transaction_description[
                                :100
                            ],  # Truncate if needed
                            amount=avg_amount,
                            frequency=frequency,
                            category=group_transactions[0].category,
                            description_pattern=pattern,
                            first_detected=min(dates),
                            last_seen=max(dates),
                            transaction_count=len(group_transactions),
                            confidence_score=min(
                                1.0, len(group_transactions) / 5.0
                            ),  # Higher confidence with more occurrences
                            is_active=True,
                        )

                        db.add(subscription)
                        subscription_records.append(subscription)

        db.commit()
        return subscription_records

    @staticmethod
    def _calculate_category_spending(
        db: Session,
        user_id: str,
        transactions: List[Transaction],
        period_start: date,
        period_end: date,
    ) -> List[UserCategorySpending]:
        """Calculate category spending analysis"""

        # Clear existing category spending for this period
        db.query(UserCategorySpending).filter(
            and_(
                UserCategorySpending.user_id == user_id,
                UserCategorySpending.period_start == period_start,
                UserCategorySpending.period_end == period_end,
            )
        ).delete()

        # Group expenses by category
        category_data = defaultdict(lambda: {"total": 0, "count": 0, "amounts": []})
        total_expenses = 0

        for transaction in transactions:
            if transaction.transaction_type.lower() == "debit" and transaction.category:
                amount = float(transaction.transaction_amount)
                category_data[transaction.category]["total"] += amount
                category_data[transaction.category]["count"] += 1
                category_data[transaction.category]["amounts"].append(amount)
                total_expenses += amount

        category_records = []

        for category, data in category_data.items():
            avg_amount = data["total"] / data["count"] if data["count"] > 0 else 0
            percentage = (
                (data["total"] / total_expenses * 100) if total_expenses > 0 else 0
            )

            category_spending = UserCategorySpending(
                user_id=user_id,
                category=category,
                period_start=period_start,
                period_end=period_end,
                total_amount=data["total"],
                transaction_count=data["count"],
                average_amount=avg_amount,
                percentage_of_expenses=percentage,
                trend_direction="stable",  # Would need historical data for trend analysis
                trend_percentage=0,
            )

            db.add(category_spending)
            category_records.append(category_spending)

        db.commit()
        return category_records

    @staticmethod
    def _calculate_monthly_trends(
        db: Session, user_id: str, transactions: List[Transaction]
    ) -> Optional[UserMonthlyTrends]:
        """Calculate monthly trends comparison"""

        # Group transactions by month
        monthly_data = defaultdict(
            lambda: {"income": 0, "expenses": 0, "count": 0, "balance": 0}
        )

        for transaction in transactions:
            month_key = transaction.transaction_date.replace(day=1)
            amount = float(transaction.transaction_amount)

            monthly_data[month_key]["count"] += 1
            monthly_data[month_key]["balance"] = float(transaction.balance)

            if transaction.transaction_type.lower() == "credit":
                monthly_data[month_key]["income"] += amount
            else:
                monthly_data[month_key]["expenses"] += amount

        # Get current and previous month data
        sorted_months = sorted(monthly_data.keys(), reverse=True)

        if len(sorted_months) >= 2:
            current_month = sorted_months[0]
            previous_month = sorted_months[1]

            current_data = monthly_data[current_month]
            previous_data = monthly_data[previous_month]

            # Calculate percentage changes
            def calc_change_pct(current: float, previous: float) -> float:
                if previous == 0:
                    return 100.0 if current > 0 else 0.0
                return round(((current - previous) / previous) * 100, 2)

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
                current_data["count"], previous_data["count"]
            )

            # Clear existing trend for this month
            db.query(UserMonthlyTrends).filter(
                and_(
                    UserMonthlyTrends.user_id == user_id,
                    UserMonthlyTrends.month == current_month,
                )
            ).delete()

            # Create new trend record
            monthly_trend = UserMonthlyTrends(
                user_id=user_id,
                month=current_month,
                income_current=current_data["income"],
                income_previous=previous_data["income"],
                income_change_pct=income_change,
                expenses_current=current_data["expenses"],
                expenses_previous=previous_data["expenses"],
                expenses_change_pct=expenses_change,
                balance_current=current_data["balance"],
                balance_previous=previous_data["balance"],
                balance_change_pct=balance_change,
                transaction_count_current=current_data["count"],
                transaction_count_previous=previous_data["count"],
                transaction_count_change_pct=count_change,
            )

            db.add(monthly_trend)
            db.commit()

            return monthly_trend

        return None

    @staticmethod
    def get_user_financial_metrics(
        db: Session, user_id: str, statement_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Retrieve stored financial metrics for a user"""

        query = db.query(UserFinancialMetrics).filter(
            and_(
                UserFinancialMetrics.user_id == user_id,
                UserFinancialMetrics.is_current.is_(True),
            )
        )

        if statement_id:
            query = query.filter(UserFinancialMetrics.statement_id == statement_id)

        metrics = query.first()

        if metrics:
            return metrics.to_dict()

        return None

    @staticmethod
    def get_user_cash_flow_data(db: Session, user_id: str) -> List[Dict[str, Any]]:
        """Retrieve stored cash flow data for a user"""

        cash_flow_data = (
            db.query(UserCashFlowData)
            .filter(UserCashFlowData.user_id == user_id)
            .order_by(UserCashFlowData.date)
            .all()
        )

        return [cf.to_dict() for cf in cash_flow_data]

    @staticmethod
    def get_user_subscriptions(
        db: Session, user_id: str, active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Retrieve detected subscriptions for a user"""

        query = db.query(UserSubscriptions).filter(UserSubscriptions.user_id == user_id)

        if active_only:
            query = query.filter(UserSubscriptions.is_active.is_(True))

        subscriptions = query.order_by(desc(UserSubscriptions.amount)).all()

        return [sub.to_dict() for sub in subscriptions]

    @staticmethod
    def get_user_category_spending(db: Session, user_id: str) -> List[Dict[str, Any]]:
        """Retrieve category spending analysis for a user"""

        category_spending = (
            db.query(UserCategorySpending)
            .filter(UserCategorySpending.user_id == user_id)
            .order_by(desc(UserCategorySpending.total_amount))
            .all()
        )

        return [cat.to_dict() for cat in category_spending]

    @staticmethod
    def get_user_monthly_trends(db: Session, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve monthly trends for a user"""

        trend = (
            db.query(UserMonthlyTrends)
            .filter(UserMonthlyTrends.user_id == user_id)
            .order_by(desc(UserMonthlyTrends.month))
            .first()
        )

        if trend:
            return trend.to_dict()

        return None

    @staticmethod
    def get_all_user_financial_metrics(db: Session, user_id: str) -> list:
        """Return all financial metrics for a user (all statements and overall)."""
        metrics = (
            db.query(UserFinancialMetrics)
            .filter(UserFinancialMetrics.user_id == user_id)
            .order_by(UserFinancialMetrics.period_end.desc())
            .all()
        )
        return [m.to_dict() for m in metrics]


def _trigger_financial_achievements(
    db: Session, user_id: str, financial_metrics, transactions: List
):
    """
    Trigger achievement events based on calculated financial metrics
    This ensures achievements and points are calculated and stored on the backend
    """
    from ..achievements import achievement_service

    try:
        # Track number of transactions processed
        achievement_service.update_achievement_progress(
            db, user_id, achievement_service.EVENT_TRANSACTION_ADDED, len(transactions)
        )

        # Track financial wellness based on health score
        if financial_metrics.financial_health_score >= 70:
            achievement_service.update_achievement_progress(
                db, user_id, achievement_service.EVENT_FINANCIAL_WELLNESS_CHECK, 1
            )

        # Track savings achievements based on net cash flow
        if financial_metrics.net_cash_flow > 0:
            achievement_service.update_achievement_progress(
                db,
                user_id,
                achievement_service.EVENT_SAVINGS_ADDED,
                float(financial_metrics.net_cash_flow),
            )

        # Track spending pattern achievements based on expense volatility
        if (
            financial_metrics.expense_volatility < 10
        ):  # Low volatility = good spending control
            achievement_service.update_achievement_progress(
                db, user_id, achievement_service.EVENT_BUDGET_FOLLOWED, 1
            )

        # Update tracking streak for consistent financial monitoring
        achievement_service.update_streak(
            db, user_id, achievement_service.CATEGORY_TRACKING
        )

        # Track categories if transactions have been categorized
        categorized_transactions = [t for t in transactions if t.transaction_category]
        if categorized_transactions:
            achievement_service.update_achievement_progress(
                db,
                user_id,
                achievement_service.EVENT_CATEGORY_TRACKED,
                len(categorized_transactions),
            )

        logger.info(
            f"Triggered financial achievements for user {user_id}: "
            f"transactions={len(transactions)}, health_score={financial_metrics.financial_health_score}, "
            f"net_flow={financial_metrics.net_cash_flow}, categorized={len(categorized_transactions)}"
        )

    except Exception as e:
        logger.warning(
            f"Failed to trigger financial achievements for user {user_id}: {str(e)}"
        )
        # Don't fail the financial calculation if achievements fail
