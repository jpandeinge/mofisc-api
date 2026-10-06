# api/app/routes/goal_routes.py

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from ..config.settings import settings
from ..sentry import SentryTransaction, set_sentry_user
from ..services.goals.goal_service import GoalService
from ..utils.auth import get_current_user

router = APIRouter()


@router.post("/")
async def create_goal(
    request: Request, goal_data: Dict[str, Any], current_user=Depends(get_current_user)
):
    with SentryTransaction(name="create_goal", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.create_goal(str(current_user.id), goal_data)
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to create goal"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("/")
async def get_goals(
    request: Request,
    include_deleted: bool = False,
    current_user=Depends(get_current_user),
):
    with SentryTransaction(name="get_goals", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.get_goals(str(current_user.id), include_deleted)
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to retrieve goals"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("/{goal_id}")
async def get_goal(
    request: Request, goal_id: str, current_user=Depends(get_current_user)
):
    with SentryTransaction(name="get_goal", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.get_goal(goal_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to retrieve goal"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.put("/{goal_id}")
async def update_goal(
    request: Request,
    goal_id: str,
    updates: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    with SentryTransaction(name="update_goal", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.update_goal(goal_id, str(current_user.id), updates)
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to update goal"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.delete("/{goal_id}")
async def delete_goal(
    request: Request, goal_id: str, current_user=Depends(get_current_user)
):
    with SentryTransaction(name="delete_goal", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.delete_goal(goal_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to delete goal"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.post("/{goal_id}/contribution")
async def add_contribution(
    request: Request,
    goal_id: str,
    contribution_data: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    with SentryTransaction(name="add_contribution", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.add_contribution(
                goal_id, str(current_user.id), float(contribution_data.get("amount", 0))
            )
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to add contribution"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.post("/{goal_id}/archive")
async def archive_goal(
    request: Request, goal_id: str, current_user=Depends(get_current_user)
):
    with SentryTransaction(name="archive_goal", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.archive_goal(goal_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to archive goal"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.post("/{goal_id}/unarchive")
async def unarchive_goal(
    request: Request, goal_id: str, current_user=Depends(get_current_user)
):
    with SentryTransaction(name="unarchive_goal", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.unarchive_goal(goal_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to unarchive goal"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }


@router.get("/{goal_id}/history")
async def get_goal_history(
    request: Request, goal_id: str, current_user=Depends(get_current_user)
):
    with SentryTransaction(name="get_goal_history", op="database"):
        set_sentry_user(current_user)

        try:
            result = GoalService.get_goal_history(goal_id, str(current_user.id))
            return {"success": True, "data": result}
        except Exception as e:
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                return {"success": False, "message": "Failed to retrieve goal history"}
            else:
                return {
                    "success": False,
                    "message": str(e),
                    "error": e.__class__.__name__,
                }
