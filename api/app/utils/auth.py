# api/app/utils/auth.py

from types import SimpleNamespace

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..services.api_keys import ApiKeyService
from ..utils.api_scopes import is_scope_allowed, resolve_required_scope
from ..utils.logging_config import get_logger
from ..utils.supabase_utils import run_supabase_read

logger = get_logger(__name__)

security = HTTPBearer(auto_error=False)


class AuthenticatedUser(SimpleNamespace):
    def get(self, key, default=None):  # pylint: disable=unused-argument
        return getattr(self, key, default)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    try:
        if credentials:
            token = credentials.credentials
            user = await run_in_threadpool(
                run_supabase_read,
                lambda client: client.auth.get_user(token),
                use_service_role=False,
            )

            if not user or not user.user:
                logger.warning("Authentication failed - invalid token")
                raise HTTPException(
                    status_code=401, detail="Invalid authentication credentials"
                )

            return user.user  # Return the user object with .id attribute

        api_key = request.headers.get("X-API-Key")
        if api_key:
            required_scope = resolve_required_scope(request.url.path, request.method)
            user = await _authenticate_api_key(api_key, required_scope)
            if request.url.path.startswith("/api/v1/api-keys"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API keys cannot manage API keys",
                )
            request.state.api_key_id = user.api_key_id
            request.state.api_key_user_id = user.id
            request.state.api_key_scope = required_scope
            return user

        raise HTTPException(
            status_code=401, detail="Invalid authentication credentials"
        )
    except HTTPException:
        raise
    except Exception:
        # Log minimal information about auth errors
        logger.warning("Authentication failed")
        raise HTTPException(
            status_code=401, detail="Invalid authentication credentials"
        )


async def get_api_key_user(request: Request):
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
        )
    required_scope = resolve_required_scope(request.url.path, request.method)
    user = await _authenticate_api_key(api_key, required_scope)
    request.state.api_key_id = user.api_key_id
    request.state.api_key_user_id = user.id
    request.state.api_key_scope = required_scope
    return user


async def _authenticate_api_key(api_key: str, required_scope: str | None):
    api_key_service = ApiKeyService()
    record = await api_key_service.validate_api_key(api_key)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    user_id = str(record.get("user_id"))

    from .subscription import PLAN_LIMITS, get_user_subscription

    subscription = await get_user_subscription(user_id)
    plan = subscription.get("plan", "free")
    api_access_enabled = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get(
        "api_access", False
    )

    if not api_access_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_required",
                "message": "API access requires an Enterprise plan.",
                "current_plan": plan,
                "required_plan": "enterprise",
                "upgrade_url": "/pricing",
            },
        )

    scopes = record.get("scopes") or []
    if not is_scope_allowed(scopes, required_scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "insufficient_scope",
                "message": "This API key does not have access to the requested resource.",
                "required_scope": required_scope,
            },
        )

    return AuthenticatedUser(
        id=user_id,
        role="user",
        is_admin=False,
        auth_type="api_key",
        api_key_id=record.get("id"),
        api_key_scopes=scopes,
    )
