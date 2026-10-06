# api/app/routes/calculator_routes.py

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request

from ..sentry import SentryTransaction, set_sentry_user
from ..services.calculators.calculator_service import CalculatorService
from ..utils.auth import get_current_user

router = APIRouter()

# Initialize service once at module level for reuse
calculator_service = CalculatorService()


@router.post("/history/")
async def save_calculator_history(
    request: Request, data: Dict[str, Any], current_user=Depends(get_current_user)
):
    """Save calculator usage history."""
    with SentryTransaction(name="save_calculator_history", op="database"):
        set_sentry_user(current_user)

        try:
            result = calculator_service.save_calculator_history(
                user_id=str(current_user.id),
                calculator_type=data.get("calculator_type"),
                inputs=data.get("inputs", {}),
                outputs=data.get("outputs", {}),
                metadata=data.get("metadata", {}),
            )

            return {"success": True, "data": result}
        except Exception:
            return {
                "success": False,
                "message": "Failed to save calculator history",
                "error": "InternalServerError",
            }


@router.get("/history/")
async def get_calculator_history(
    request: Request,
    calculator_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user=Depends(get_current_user),
):
    """Get calculator usage history."""
    with SentryTransaction(name="get_calculator_history", op="database"):
        set_sentry_user(current_user)

        try:
            result = calculator_service.get_calculator_history(
                user_id=str(current_user.id),
                calculator_type=calculator_type,
                limit=limit,
                offset=offset,
            )

            return {"success": True, "data": result}
        except Exception:
            return {
                "success": False,
                "message": "Failed to retrieve calculator history",
                "error": "InternalServerError",
            }


@router.get("/statistics/")
async def get_calculator_statistics(
    request: Request,
    calculator_type: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Get calculator usage statistics."""
    with SentryTransaction(name="get_calculator_statistics", op="database"):
        set_sentry_user(current_user)

        try:
            result = calculator_service.get_calculator_statistics(
                user_id=str(current_user.id), calculator_type=calculator_type
            )

            return {"success": True, "data": result}
        except Exception:
            return {
                "success": False,
                "message": "Failed to retrieve calculator statistics",
                "error": "InternalServerError",
            }
