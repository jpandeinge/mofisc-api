"""Organisation workspace lookup helpers.

All helpers use the service-role client because they are called only after the
request has been authenticated by the API. Route handlers must still verify the
current user before passing an ID into this module.
"""

from typing import Any, Dict, Optional

from .supabase_utils import get_supabase_client


def get_organisation_membership(
    user_id: str, *, client=None
) -> Optional[Dict[str, Any]]:
    """Return the user's active workspace membership, if one exists."""
    supabase = (
        client if client is not None else get_supabase_client(use_service_role=True)
    )
    response = (
        supabase.table("organisation_members")
        .select("id, organisation_id, user_id, role, status, created_at")
        .eq("user_id", user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def get_organisation(organisation_id: str) -> Optional[Dict[str, Any]]:
    """Return an active organisation by ID."""
    supabase = get_supabase_client(use_service_role=True)
    response = (
        supabase.table("organisations")
        .select(
            "id, name, slug, owner_user_id, status, settings, created_at, updated_at"
        )
        .eq("id", organisation_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def get_user_organisation(user_id: str) -> Optional[Dict[str, Any]]:
    """Return a combined organisation and membership record for a user."""
    membership = get_organisation_membership(user_id)
    if not membership:
        return None

    organisation = get_organisation(str(membership["organisation_id"]))
    if not organisation:
        return None

    return {"organisation": organisation, "membership": membership}
