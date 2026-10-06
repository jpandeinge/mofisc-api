"""
User subscription management endpoints (pause, resume, delete, rename).
Production fintech patterns with full audit trail and security.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..utils.auth import get_current_user
from ..utils.logging_config import get_logger
from ..utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)
router = APIRouter()


class SubscriptionPauseRequest(BaseModel):
    resume_date: Optional[str] = Field(None, description="ISO date for auto-resume")


class SubscriptionRenameRequest(BaseModel):
    custom_name: str = Field(..., min_length=1, max_length=100)


@router.patch("/subscriptions/{subscription_id}/pause")
async def pause_subscription(
    subscription_id: str,
    request: SubscriptionPauseRequest,
    current_user=Depends(get_current_user),
):
    """Pause subscription (keeps for tracking)."""
    supabase = get_supabase_client()

    try:
        # Verify ownership
        subscription = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("id", subscription_id)
            .eq("user_id", str(current_user.id))
            .is_("soft_deleted_at", "null")
            .single()
            .execute()
        )

        if not subscription.data:
            raise HTTPException(404, "Subscription not found")

        # Update
        update_data = {
            "is_paused": True,
            "paused_at": datetime.now(timezone.utc).isoformat(),
            "paused_by_user_id": str(current_user.id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if request.resume_date:
            update_data["resume_date"] = request.resume_date

        supabase.table("user_subscriptions").update(update_data).eq(
            "id", subscription_id
        ).execute()

        logger.info(
            f"User {str(current_user.id)[:8]}... paused subscription {subscription_id[:8]}..."
        )

        return {
            "success": True,
            "message": "Subscription paused",
            "subscription_id": subscription_id,
            "is_paused": True,
            "resume_date": request.resume_date,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to pause subscription {subscription_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to pause subscription",
        )


@router.patch("/subscriptions/{subscription_id}/resume")
async def resume_subscription(
    subscription_id: str,
    current_user=Depends(get_current_user),
):
    """Resume paused subscription."""
    supabase = get_supabase_client()

    try:
        # Verify ownership
        subscription = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("id", subscription_id)
            .eq("user_id", str(current_user.id))
            .is_("soft_deleted_at", "null")
            .single()
            .execute()
        )

        if not subscription.data:
            raise HTTPException(404, "Subscription not found")

        # Update
        supabase.table("user_subscriptions").update(
            {
                "is_paused": False,
                "paused_at": None,
                "paused_by_user_id": None,
                "resume_date": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", subscription_id).execute()

        logger.info(
            f"User {str(current_user.id)[:8]}... resumed subscription {subscription_id[:8]}..."
        )

        return {
            "success": True,
            "message": "Subscription resumed",
            "subscription_id": subscription_id,
            "is_paused": False,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to resume subscription {subscription_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resume subscription",
        )


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(
    subscription_id: str,
    current_user=Depends(get_current_user),
):
    """Soft delete subscription (30-day recovery window)."""
    supabase = get_supabase_client()

    try:
        # Verify ownership
        subscription = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("id", subscription_id)
            .eq("user_id", str(current_user.id))
            .is_("soft_deleted_at", "null")
            .single()
            .execute()
        )

        if not subscription.data:
            raise HTTPException(404, "Subscription not found")

        # Soft delete
        supabase.table("user_subscriptions").update(
            {
                "soft_deleted_at": datetime.now(timezone.utc).isoformat(),
                "is_active": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", subscription_id).execute()

        logger.info(
            f"User {str(current_user.id)[:8]}... deleted subscription {subscription_id[:8]}..."
        )

        return {
            "success": True,
            "message": "Subscription deleted",
            "subscription_id": subscription_id,
            "can_recover_until": int(
                (datetime.now(timezone.utc).timestamp() + 30 * 24 * 60 * 60)
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to delete subscription {subscription_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete subscription",
        )


@router.patch("/subscriptions/{subscription_id}/rename")
async def rename_subscription(
    subscription_id: str,
    request: SubscriptionRenameRequest,
    current_user=Depends(get_current_user),
):
    """Set custom display name (overrides auto-sync)."""
    supabase = get_supabase_client()

    try:
        # Verify ownership
        subscription = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("id", subscription_id)
            .eq("user_id", str(current_user.id))
            .is_("soft_deleted_at", "null")
            .single()
            .execute()
        )

        if not subscription.data:
            raise HTTPException(404, "Subscription not found")

        # Update
        supabase.table("user_subscriptions").update(
            {
                "user_display_name": request.custom_name,
                "name": request.custom_name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", subscription_id).execute()

        logger.info(
            f"User {str(current_user.id)[:8]}... renamed subscription {subscription_id[:8]}..."
        )

        return {
            "success": True,
            "message": "Subscription renamed",
            "subscription_id": subscription_id,
            "custom_name": request.custom_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to rename subscription {subscription_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rename subscription",
        )
