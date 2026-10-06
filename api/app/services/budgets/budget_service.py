# api/app/services/budgets/budget_service.py

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List
from uuid import uuid4

from ...config.settings import settings
from ...errors.exceptions import DatabaseError, NotFoundError, ValidationError
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)


class BudgetService:
    VALID_PERIODS = ["daily", "weekly", "monthly", "yearly"]

    @staticmethod
    def create_budget(user_id: str, budget_data: Dict[str, Any]):
        """Create a new budget."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Validate period
            period = budget_data.get("period", "monthly")
            if period not in BudgetService.VALID_PERIODS:
                raise ValidationError(
                    f"Invalid period. Must be one of: {BudgetService.VALID_PERIODS}"
                )

            # Prepare budget data
            budget = {
                "id": str(uuid4()),
                "user_id": user_id,
                "category": budget_data.get("category"),
                "amount": budget_data.get("amount"),
                "period": period,
                "start_date": budget_data.get("start_date", date.today().isoformat()),
                "end_date": budget_data.get("end_date"),
                "alert_threshold": budget_data.get("alert_threshold", 80),
                "is_active": True,
            }

            # Validate required fields
            if not budget.get("category"):
                raise ValidationError("Category is required")
            if not budget.get("amount") or float(budget["amount"]) <= 0:
                raise ValidationError("Amount must be greater than 0")

            # Check for duplicate active budget for same category
            existing = (
                supabase.table("budgets")
                .select("id")
                .eq("user_id", user_id)
                .eq("category", budget["category"])
                .eq("is_active", True)
                .execute()
            )

            if existing.data and len(existing.data) > 0:
                raise ValidationError(
                    f"An active budget already exists for {budget['category']}"
                )

            # Insert into database
            result = supabase.table("budgets").insert(budget).execute()

            if not result.data:
                raise DatabaseError("Failed to create budget")

            return result.data[0]
        except Exception as e:
            logger.warning(f"Budget creation failed: {str(e)}")
            raise

    @staticmethod
    def get_budgets(user_id: str, include_inactive: bool = False):
        """Get all budgets for a user."""
        try:
            supabase = get_supabase_client()

            query = supabase.table("budgets").select("*").eq("user_id", user_id)

            if not include_inactive:
                query = query.eq("is_active", True)

            result = query.order("created_at", desc=True).execute()

            return result.data
        except Exception:
            logger.warning("Failed to retrieve budgets")
            raise

    @staticmethod
    def get_budget(budget_id: str, user_id: str):
        """Get a specific budget."""
        try:
            supabase = get_supabase_client()

            result = (
                supabase.table("budgets")
                .select("*")
                .eq("id", budget_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise NotFoundError("Budget", budget_id)

            return result.data[0]
        except Exception:
            logger.warning("Failed to retrieve budget")
            raise

    @staticmethod
    def update_budget(budget_id: str, user_id: str, updates: Dict[str, Any]):
        """Update a budget."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify budget exists and belongs to user
            BudgetService.get_budget(budget_id, user_id)

            # Validate period if being updated
            if (
                "period" in updates
                and updates["period"] not in BudgetService.VALID_PERIODS
            ):
                raise ValidationError(
                    f"Invalid period. Must be one of: {BudgetService.VALID_PERIODS}"
                )

            # Validate amount if being updated
            if "amount" in updates and float(updates["amount"]) <= 0:
                raise ValidationError("Amount must be greater than 0")

            # Apply updates
            result = (
                supabase.table("budgets")
                .update(updates)
                .eq("id", budget_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to update budget")

            return result.data[0]
        except Exception:
            logger.warning("Failed to update budget")
            raise

    @staticmethod
    def delete_budget(budget_id: str, user_id: str):
        """Delete a budget (soft delete by marking inactive)."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify budget exists and belongs to user
            BudgetService.get_budget(budget_id, user_id)

            # Mark as inactive
            result = (
                supabase.table("budgets")
                .update({"is_active": False})
                .eq("id", budget_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to delete budget")

            return result.data[0]
        except Exception:
            logger.warning("Failed to delete budget")
            raise

    @staticmethod
    def get_budget_status(user_id: str):
        """Get budget status with spending analysis for all active budgets."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Call the database function
            try:
                result = supabase.rpc(
                    "check_budget_alerts", {"p_user_id": user_id}
                ).execute()
            except Exception as rpc_error:
                logger.warning(
                    f"Budget status RPC failed, using manual fallback: {str(rpc_error)}"
                )
                return BudgetService._get_budget_status_fallback(supabase, user_id)

            if not result.data:
                return []

            # Format the response
            budgets_status = []
            for item in result.data:
                budgets_status.append(
                    {
                        "budget_id": item["budget_id"],
                        "category": item["category"],
                        "budget_amount": float(item["budget_amount"]),
                        "spent_amount": float(item["spent_amount"]),
                        "percentage": int(item["percentage"]),
                        "remaining": float(item["budget_amount"])
                        - float(item["spent_amount"]),
                        "threshold_exceeded": item["threshold_exceeded"],
                        "status": BudgetService._get_budget_health_status(
                            int(item["percentage"])
                        ),
                    }
                )

            return budgets_status
        except Exception as e:
            logger.warning(f"Failed to get budget status: {str(e)}")
            raise

    @staticmethod
    def _get_budget_health_status(percentage: int) -> str:
        """Determine budget health status based on percentage spent."""
        if percentage >= 100:
            return "exceeded"
        elif percentage >= 90:
            return "critical"
        elif percentage >= 80:
            return "warning"
        elif percentage >= 50:
            return "on_track"
        else:
            return "healthy"

    @staticmethod
    def get_spending_insights(user_id: str, period: str = "monthly"):
        """Get spending insights and recommendations."""
        try:
            supabase = get_supabase_client()

            # Calculate date range based on period
            end_date = date.today()
            if period == "daily":
                start_date = end_date
            elif period == "weekly":
                start_date = end_date - timedelta(days=7)
            elif period == "monthly":
                start_date = date(end_date.year, end_date.month, 1)
            elif period == "yearly":
                start_date = date(end_date.year, 1, 1)
            else:
                start_date = date(end_date.year, end_date.month, 1)

            # Get transactions for the period
            result = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("category, transaction_amount, transaction_type")
                .eq("user_id", user_id)
                .gte("transaction_date", start_date.isoformat())
                .lte("transaction_date", end_date.isoformat())
                .eq("transaction_type", "debit")
                .execute()
            )

            if not result.data:
                return {
                    "total_spent": 0,
                    "categories": [],
                    "insights": [],
                }

            # Aggregate by category
            category_spending = {}
            total_spent = Decimal(0)

            for transaction in result.data:
                category = transaction["category"] or "Uncategorized"
                amount = Decimal(str(transaction.get("transaction_amount") or 0))
                total_spent += amount

                if category in category_spending:
                    category_spending[category] += amount
                else:
                    category_spending[category] = amount

            # Sort by spending
            sorted_categories = sorted(
                category_spending.items(), key=lambda x: x[1], reverse=True
            )

            # Generate insights
            insights = []
            if sorted_categories:
                top_category, top_amount = sorted_categories[0]
                top_percentage = (
                    (top_amount / total_spent * 100) if total_spent > 0 else 0
                )

                insights.append(
                    {
                        "type": "top_spending",
                        "message": f"Your top spending category is {top_category}",
                        "detail": f"${float(top_amount):.2f} ({top_percentage:.0f}% of total spending)",
                        "icon": "📊",
                    }
                )

            return {
                "total_spent": float(total_spent),
                "period": period,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "categories": [
                    {
                        "category": cat,
                        "amount": float(amt),
                        "percentage": (
                            (float(amt) / float(total_spent) * 100)
                            if total_spent > 0
                            else 0
                        ),
                    }
                    for cat, amt in sorted_categories
                ],
                "insights": insights,
            }
        except Exception as e:
            logger.warning(f"Failed to get spending insights: {str(e)}")
            raise

    @staticmethod
    def _period_start_date(period: str, today: date) -> date:
        if period == "daily":
            return today
        if period == "weekly":
            return today - timedelta(days=7)
        if period == "yearly":
            return date(today.year, 1, 1)
        return date(today.year, today.month, 1)

    @staticmethod
    def _get_spent_for_budget(
        supabase, user_id: str, category: str, start_date: date, end_date: date
    ) -> Decimal:
        result = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("transaction_amount")
            .eq("user_id", user_id)
            .eq("category", category)
            .eq("transaction_type", "debit")
            .gte("transaction_date", start_date.isoformat())
            .lte("transaction_date", end_date.isoformat())
            .execute()
        )

        total = Decimal(0)
        for tx in result.data or []:
            total += Decimal(str(tx.get("transaction_amount") or 0))
        return total

    @staticmethod
    def _get_budget_status_fallback(supabase, user_id: str) -> List[Dict[str, Any]]:
        budgets_response = (
            supabase.table("budgets")
            .select("id, category, amount, period, alert_threshold")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )

        budgets = budgets_response.data or []
        if not budgets:
            return []

        today = date.today()
        statuses: List[Dict[str, Any]] = []
        for budget in budgets:
            budget_amount = Decimal(str(budget.get("amount") or 0))
            if budget_amount <= 0:
                continue

            period = budget.get("period") or "monthly"
            start_date = BudgetService._period_start_date(period, today)
            spent = BudgetService._get_spent_for_budget(
                supabase,
                user_id=user_id,
                category=budget.get("category"),
                start_date=start_date,
                end_date=today,
            )
            percentage = int((spent / budget_amount) * 100) if budget_amount > 0 else 0
            threshold = int(budget.get("alert_threshold") or 80)

            statuses.append(
                {
                    "budget_id": budget.get("id"),
                    "category": budget.get("category"),
                    "budget_amount": float(budget_amount),
                    "spent_amount": float(spent),
                    "percentage": percentage,
                    "remaining": float(max(budget_amount - spent, 0)),
                    "threshold_exceeded": percentage >= threshold,
                    "status": BudgetService._get_budget_health_status(percentage),
                }
            )

        return statuses
