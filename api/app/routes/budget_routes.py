# api/app/routes/budget_routes.py

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from ..config.settings import settings
from ..sentry import SentryTransaction, set_sentry_user
from ..services.budgets.budget_service import BudgetService
from ..utils.auth import get_current_user

router = APIRouter()


@router.post("")
async def create_budget(
    request: Request,
    budget_data: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Create a new budget."""
    with SentryTransaction(name="create_budget", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.create_budget(str(current_user.id), budget_data)
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to create budget"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("")
async def get_budgets(
    request: Request,
    include_inactive: bool = False,
    current_user=Depends(get_current_user),
):
    """Get all budgets for the current user."""
    with SentryTransaction(name="get_budgets", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.get_budgets(str(current_user.id), include_inactive)
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to retrieve budgets"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("/status")
async def get_budget_status(request: Request, current_user=Depends(get_current_user)):
    """Get budget status with spending analysis."""
    with SentryTransaction(name="get_budget_status", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.get_budget_status(str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to retrieve budget status"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("/insights")
async def get_spending_insights(
    request: Request,
    period: str = "monthly",
    current_user=Depends(get_current_user),
):
    """Get spending insights and recommendations."""
    with SentryTransaction(name="get_spending_insights", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.get_spending_insights(str(current_user.id), period)
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {
                    "success": False,
                    "message": "Failed to retrieve spending insights",
                }
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("/{budget_id}")
async def get_budget(
    request: Request, budget_id: str, current_user=Depends(get_current_user)
):
    """Get a specific budget."""
    with SentryTransaction(name="get_budget", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.get_budget(budget_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to retrieve budget"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.put("/{budget_id}")
async def update_budget(
    request: Request,
    budget_id: str,
    updates: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Update a budget."""
    with SentryTransaction(name="update_budget", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.update_budget(
                budget_id, str(current_user.id), updates
            )
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to update budget"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.delete("/{budget_id}")
async def delete_budget(
    request: Request, budget_id: str, current_user=Depends(get_current_user)
):
    """Delete a budget (soft delete)."""
    with SentryTransaction(name="delete_budget", op="database"):
        set_sentry_user(current_user)

        try:
            result = BudgetService.delete_budget(budget_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to delete budget"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }
