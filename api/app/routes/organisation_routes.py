"""Authenticated organisation workspace routes."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..utils.auth import get_current_user
from ..utils.logging_config import get_logger
from ..utils.organisations import get_user_organisation
from ..utils.subscription import get_user_subscription
from ..utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)
router = APIRouter()


def _user_metadata(current_user) -> Dict[str, Any]:
    metadata = getattr(current_user, "user_metadata", None)
    if isinstance(metadata, dict):
        return metadata
    if hasattr(current_user, "get"):
        candidate = current_user.get("user_metadata", {})
        if isinstance(candidate, dict):
            return candidate
    return {}


def _normalise_user_type(value: Any) -> str:
    candidate = str(value or "individual").strip().lower()
    return (
        "organisation"
        if candidate in {"organisation", "organization"}
        else "individual"
    )


@router.post("/bootstrap")
async def bootstrap_organisation(current_user=Depends(get_current_user)):
    """Create or recover the signed-in user's organisation workspace.

    The database function is idempotent, so this endpoint is safe to call after
    every sign-in. It creates an owner membership and a seven-day Enterprise
    trial only when the workspace does not already have paid billing state.
    """
    metadata = _user_metadata(current_user)
    if _normalise_user_type(metadata.get("user_type")) != "organisation":
        raise HTTPException(
            status_code=400,
            detail="Organisation onboarding is only available to organisation accounts",
        )

    organisation_name = str(metadata.get("organisation_name") or "").strip()
    if len(organisation_name) < 2:
        raise HTTPException(
            status_code=422,
            detail="Organisation name is required to finish workspace setup",
        )

    email = str(getattr(current_user, "email", None) or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Account email is required")

    full_name = str(metadata.get("full_name") or email.split("@", 1)[0]).strip()

    try:
        supabase = get_supabase_client(use_service_role=True)
        result = supabase.rpc(
            "bootstrap_organisation_workspace",
            {
                "p_user_id": str(current_user.id),
                "p_email": email,
                "p_full_name": full_name,
                "p_organisation_name": organisation_name,
            },
        ).execute()
        if not result.data:
            raise RuntimeError("Workspace bootstrap returned no data")

        workspace = result.data[0] if isinstance(result.data, list) else result.data
        return {"workspace": workspace}
    except HTTPException:
        raise
    except Exception as error:
        logger.error(
            "Organisation bootstrap failed",
            extra={"user_id": str(current_user.id), "error": str(error)},
        )
        raise HTTPException(
            status_code=500, detail="Failed to set up organisation workspace"
        )


@router.get("/me")
async def get_my_organisation(current_user=Depends(get_current_user)):
    """Return the current user's workspace, role, and inherited plan."""
    try:
        workspace = get_user_organisation(str(current_user.id))
        if not workspace:
            return {"workspace": None}

        subscription = await get_user_subscription(str(current_user.id))
        return {
            "workspace": {
                **workspace["organisation"],
                "role": workspace["membership"]["role"],
                "membership_status": workspace["membership"]["status"],
                "plan": subscription.get("plan", "free"),
                "subscription_status": subscription.get("status", "active"),
            }
        }
    except Exception as error:
        logger.error(
            "Organisation lookup failed",
            extra={"user_id": str(current_user.id), "error": str(error)},
        )
        raise HTTPException(
            status_code=500, detail="Failed to load organisation workspace"
        )
