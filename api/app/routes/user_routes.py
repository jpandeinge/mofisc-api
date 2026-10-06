from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..errors.exceptions import NotFoundError, ValidationError
from ..sentry import set_sentry_context
from ..services.users.user_service import UserService
from ..utils.auth import get_current_user
from ..utils.db import get_db

router = APIRouter()


@router.get("/profile")
async def get_profile(
    current_user=Depends(get_current_user), db: Session = Depends(get_db)
):
    """Get current user's profile"""
    try:
        return await UserService.get_user_profile(current_user.id, db)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except Exception as e:
        set_sentry_context("route_error", {"operation": "get_profile", "error": str(e)})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/profile")
async def update_profile(
    updates: Dict[str, Any],
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update current user's profile"""
    try:
        return await UserService.update_user_profile(current_user.id, updates, db)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except Exception as e:
        set_sentry_context(
            "route_error",
            {"operation": "update_profile", "updates": updates, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail="Internal server error")
