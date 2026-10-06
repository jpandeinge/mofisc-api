# api/app/utils/subscription.py
"""
Subscription enforcement utilities for feature gating.

This module provides FastAPI dependencies to enforce subscription-based
access control on API endpoints. Features are gated by plan tier.

Security Note: This is the AUTHORITATIVE source of truth for feature access.
Frontend checks are for UX only - this backend enforcement cannot be bypassed.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from ..config.settings import settings
from .auth import get_current_user
from .logging_config import get_logger
from .organisations import get_organisation_membership
from .supabase_utils import get_supabase_client, run_supabase_read

logger = get_logger(__name__)


# Plan hierarchy (index = level, higher = more features)
PLAN_HIERARCHY = ["free", "basic", "premium", "enterprise"]

# Feature limits by plan - MUST match frontend PLAN_LIMITS
PLAN_LIMITS = {
    "free": {
        "max_transactions_per_month": 100,
        "max_bank_accounts": 1,
        "advanced_categorisation": False,
        "ner_analysis": False,
        "financial_health_insights": "basic",
        "weekly_insights": False,
        "priority_support": False,
        "api_access": False,
        "organisation_workspace": False,
        "team_members": 1,
        "custom_model_configuration": False,
        "advanced_data_analytics": False,
        "brand_customisation": False,
    },
    "basic": {
        "max_transactions_per_month": 300,
        "max_bank_accounts": 3,
        "advanced_categorisation": True,
        "ner_analysis": "basic",
        "financial_health_insights": "detailed",
        "weekly_insights": True,
        "priority_support": False,
        "api_access": False,
        "organisation_workspace": False,
        "team_members": 1,
        "custom_model_configuration": False,
        "advanced_data_analytics": False,
        "brand_customisation": False,
    },
    "premium": {
        "max_transactions_per_month": float("inf"),
        "max_bank_accounts": float("inf"),
        "advanced_categorisation": True,
        "ner_analysis": "full",
        "financial_health_insights": "advanced",
        "weekly_insights": True,
        "priority_support": True,
        "api_access": False,
        "organisation_workspace": False,
        "team_members": 1,
        "custom_model_configuration": False,
        "advanced_data_analytics": False,
        "brand_customisation": False,
    },
    "enterprise": {
        "max_transactions_per_month": float("inf"),
        "max_bank_accounts": float("inf"),
        "advanced_categorisation": True,
        "ner_analysis": "full",
        "financial_health_insights": "advanced",
        "weekly_insights": True,
        "priority_support": True,
        "api_access": True,
        "organisation_workspace": True,
        "team_members": float("inf"),
        "custom_model_configuration": True,
        "advanced_data_analytics": True,
        "brand_customisation": True,
    },
}

ENTITLEMENT_API_KEYS = {
    "max_transactions_per_month": "maxTransactionsPerMonth",
    "max_bank_accounts": "maxBankAccounts",
    "advanced_categorisation": "advancedCategorisation",
    "ner_analysis": "nerAnalysis",
    "financial_health_insights": "financialHealthInsights",
    "weekly_insights": "weeklyInsights",
    "priority_support": "prioritySupport",
    "api_access": "apiAccess",
    "organisation_workspace": "organisationWorkspace",
    "team_members": "teamMembers",
    "custom_model_configuration": "customModelConfiguration",
    "advanced_data_analytics": "advancedDataAnalytics",
    "brand_customisation": "brandCustomisation",
}


def get_plan_entitlements(plan: str) -> Dict[str, Any]:
    """Return a JSON-safe, frontend-facing entitlement map for a plan."""
    limits = PLAN_LIMITS.get((plan or "free").lower(), PLAN_LIMITS["free"])
    entitlements: Dict[str, Any] = {}
    for internal_key, public_key in ENTITLEMENT_API_KEYS.items():
        value = limits.get(internal_key)
        entitlements[public_key] = None if value == float("inf") else value
    return entitlements


def _parse_iso_datetime(value: Optional[Any]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    return None


def resolve_subscription_entitlements(
    *,
    plan: str,
    status: str,
    current_period_end: Optional[Any],
    cancel_at_period_end: bool,
) -> Tuple[bool, str]:
    """
    Resolve whether a subscription currently grants access and what the effective plan is.

    Policy:
    - active/trialing => access
    - cancelled/canceled/past_due => access until current_period_end if in the future
    - cancel_at_period_end => access until current_period_end if in the future
    - pending/incomplete/expired/unknown => no access
    """
    plan_normalized = (plan or "free").lower()
    if plan_normalized not in PLAN_HIERARCHY:
        plan_normalized = "free"
    status_normalized = (status or "active").lower()
    period_end_dt = _parse_iso_datetime(current_period_end)
    now = datetime.now(timezone.utc)

    has_active_access = False

    if status_normalized == "active":
        has_active_access = True

    if status_normalized == "trialing" and period_end_dt and period_end_dt > now:
        has_active_access = True

    if status_normalized in ["cancelled", "canceled", "past_due"] and period_end_dt:
        if period_end_dt > now:
            has_active_access = True

    if cancel_at_period_end and period_end_dt and period_end_dt > now:
        has_active_access = True

    effective_plan = plan_normalized if has_active_access else "free"
    return has_active_access, effective_plan


async def get_user_subscription(user_id: str) -> Dict[str, Any]:
    """
    Fetch user's current subscription from database.
    Returns default free plan if no subscription exists.
    """
    return await run_in_threadpool(
        run_supabase_read, lambda client: _read_user_subscription(client, user_id)
    )


def _read_user_subscription(supabase, user_id: str) -> Dict[str, Any]:
    # A failed lookup must not silently downgrade paid users or select a
    # different workspace. No data is different from an unavailable database.
    membership = get_organisation_membership(user_id, client=supabase)
    organisation_id = str(membership["organisation_id"]) if membership else None

    query = supabase.table("billing_subscriptions").select(
        "user_id, organisation_id, plan, status, current_period_start, "
        "current_period_end, cancel_at_period_end"
    )
    if organisation_id:
        query = query.eq("organisation_id", organisation_id)
    else:
        query = query.eq("user_id", user_id)

    response = query.limit(1).execute()

    if not response.data:
        return {
            "plan": "free",
            "raw_plan": "free",
            "effective_plan": "free",
            "status": "active",
            "current_period_start": None,
            "current_period_end": None,
            "has_active_access": True,
            "cancel_at_period_end": False,
            "organisation_id": organisation_id,
        }

    subscription = response.data[0]
    plan = subscription.get("plan", "free")
    status = subscription.get("status", "active")
    cancel_at_period_end = bool(subscription.get("cancel_at_period_end") or False)
    period_end = subscription.get("current_period_end")

    has_active_access, effective_plan = resolve_subscription_entitlements(
        plan=plan,
        status=status,
        current_period_end=period_end,
        cancel_at_period_end=cancel_at_period_end,
    )

    return {
        "plan": effective_plan,
        "raw_plan": plan,
        "effective_plan": effective_plan,
        "status": status,
        "current_period_start": subscription.get("current_period_start"),
        "current_period_end": subscription.get("current_period_end"),
        "has_active_access": has_active_access,
        "cancel_at_period_end": cancel_at_period_end,
        "organisation_id": organisation_id,
        "subscription_owner_user_id": subscription.get("user_id"),
    }


def get_plan_level(plan: str) -> int:
    """Get the hierarchy level of a plan (higher = more features)."""
    try:
        return PLAN_HIERARCHY.index(plan.lower())
    except ValueError:
        return 0  # Unknown plans default to free level


class SubscriptionRequired:
    """
    FastAPI dependency to enforce minimum subscription plan.

    Usage:
        @router.post("/premium-feature")
        async def premium_feature(
            current_user = Depends(get_current_user),
            _subscription = Depends(SubscriptionRequired(min_plan="premium")),
        ):
            # Only accessible to premium+ users
            pass
    """

    def __init__(self, min_plan: str = "free"):
        self.min_plan = min_plan.lower()
        self.min_level = get_plan_level(self.min_plan)

    async def __call__(self, current_user=Depends(get_current_user)) -> Dict[str, Any]:
        user_id = str(current_user.id)
        subscription = await get_user_subscription(user_id)
        user_plan = subscription["plan"]
        user_level = get_plan_level(user_plan)

        if user_level < self.min_level:
            logger.warning(
                f"User {user_id} with plan '{user_plan}' attempted to access "
                f"feature requiring '{self.min_plan}'"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "subscription_required",
                    "message": f"This feature requires a {self.min_plan.title()} plan or higher.",
                    "current_plan": user_plan,
                    "required_plan": self.min_plan,
                    "upgrade_url": "/pricing",
                },
            )

        return subscription


class TransactionLimitChecker:
    """
    FastAPI dependency to enforce monthly transaction limits.

    Checks the user's current transaction count against their plan limit.

    Usage:
        @router.post("/upload-statement")
        async def upload_statement(
            file: UploadFile,
            current_user = Depends(get_current_user),
            _limit = Depends(TransactionLimitChecker()),
        ):
            # Will raise 403 if user exceeded their monthly limit
            pass
    """

    async def __call__(self, current_user=Depends(get_current_user)) -> Dict[str, Any]:
        """
        Enforce the plan's visible transaction cap before ingesting new data.

        We intentionally count the user's total transactions (not just the
        current month) to avoid situations where historical uploads bypass
        the cap. This keeps backend enforcement aligned with the UI, which
        only surfaces the most recent transactions up to the plan limit.
        """
        user_id = str(current_user.id)
        subscription = await get_user_subscription(user_id)
        user_plan = subscription["plan"]
        limit = PLAN_LIMITS.get(user_plan, PLAN_LIMITS["free"])[
            "max_transactions_per_month"
        ]

        # Unlimited plans skip the check
        if limit == float("inf"):
            return {
                "subscription": subscription,
                "limit": limit,
                "current_count": 0,
                "remaining": float("inf"),
            }

        supabase = get_supabase_client(use_service_role=True)

        try:
            response = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("id", count="exact")
                .eq("user_id", user_id)
                .execute()
            )

            current_count = response.count if response.count else 0

            if current_count >= limit:
                hidden_transactions = current_count - int(limit)
                logger.warning(
                    f"User {user_id} exceeded transaction limit: {current_count}/{limit}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "transaction_limit_exceeded",
                        "message": (
                            f"You've reached your {user_plan.title()} plan limit of "
                            f"{int(limit)} transactions. Upgrade to import and view your full history."
                        ),
                        "current_count": current_count,
                        "limit": int(limit),
                        "current_plan": user_plan,
                        "hidden_transactions": hidden_transactions,
                        "upgrade_url": "/pricing",
                    },
                )

            return {
                "subscription": subscription,
                "limit": int(limit),
                "current_count": current_count,
                "remaining": int(limit) - current_count,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error checking transaction limit for user {user_id}: {e}")
            # Allow on error (fail open for UX, but log for monitoring)
            return {
                "subscription": subscription,
                "limit": int(limit),
                "current_count": 0,
                "remaining": int(limit),
            }


class FeatureChecker:
    """
    FastAPI dependency to check access to a specific feature.

    Usage:
        @router.get("/ner-analysis")
        async def ner_analysis(
            current_user = Depends(get_current_user),
            _feature = Depends(FeatureChecker(feature="ner_analysis", required_value="full")),
        ):
            # Only accessible if user has full NER analysis
            pass
    """

    def __init__(self, feature: str, required_value: Any = True):
        self.feature = feature
        self.required_value = required_value

    async def __call__(self, current_user=Depends(get_current_user)) -> Dict[str, Any]:
        user_id = str(current_user.id)
        subscription = await get_user_subscription(user_id)
        user_plan = subscription["plan"]

        plan_limits = PLAN_LIMITS.get(user_plan, PLAN_LIMITS["free"])
        feature_value = plan_limits.get(self.feature)

        # Check feature access
        has_access = False

        if isinstance(self.required_value, bool):
            has_access = bool(feature_value)
        elif isinstance(self.required_value, str):
            # For tiered features like ner_analysis: "basic" < "full"
            if feature_value == self.required_value:
                has_access = True
            elif self.required_value == "basic" and feature_value in ["basic", "full"]:
                has_access = True
            elif self.required_value == "full" and feature_value == "full":
                has_access = True
        else:
            has_access = feature_value == self.required_value

        if not has_access:
            logger.warning(
                f"User {user_id} with plan '{user_plan}' denied access to "
                f"feature '{self.feature}' (requires: {self.required_value})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "feature_not_available",
                    "message": "This feature is not available on your current plan.",
                    "feature": self.feature,
                    "current_plan": user_plan,
                    "upgrade_url": "/pricing",
                },
            )

        return {
            "subscription": subscription,
            "feature": self.feature,
            "value": feature_value,
        }


# Convenience dependencies for common use cases
require_basic = SubscriptionRequired(min_plan="basic")
require_premium = SubscriptionRequired(min_plan="premium")
require_enterprise = SubscriptionRequired(min_plan="enterprise")
check_transaction_limit = TransactionLimitChecker()


async def enforce_bank_account_limit(user_id: str, new_bank_name: str):
    """
    Enforce max_bank_accounts per plan. Allows existing banks; blocks adding a new bank beyond the limit.
    """
    subscription = await get_user_subscription(user_id)
    plan = subscription["plan"]
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get("max_bank_accounts", 1)

    if limit == float("inf"):
        return {
            "subscription": subscription,
            "limit": limit,
            "current_banks": 0,
            "remaining": float("inf"),
        }

    supabase = get_supabase_client(use_service_role=True)
    try:
        # Distinct bank names for this user
        response = (
            supabase.table("transactions")
            .select("bank_name", distinct=True)
            .eq("user_id", user_id)
            .execute()
        )
        bank_names = {
            row.get("bank_name")
            for row in (response.data or [])
            if row.get("bank_name")
        }

        # If bank already present, allow
        if new_bank_name in bank_names:
            return {
                "subscription": subscription,
                "limit": int(limit),
                "current_banks": len(bank_names),
                "remaining": int(limit) - len(bank_names),
            }

        # Check against limit
        if len(bank_names) >= limit:
            logger.warning(
                f"User {user_id} exceeded bank limit: {len(bank_names)}/{limit}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "bank_limit_exceeded",
                    "message": f"You've reached your bank account limit for the {plan.title()} plan.",
                    "current_banks": len(bank_names),
                    "limit": int(limit),
                    "current_plan": plan,
                    "upgrade_url": "/pricing",
                },
            )

        return {
            "subscription": subscription,
            "limit": int(limit),
            "current_banks": len(bank_names),
            "remaining": int(limit) - len(bank_names),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking bank limit for user {user_id}: {e}")
        # Fail open for UX, but log for monitoring
        return {
            "subscription": subscription,
            "limit": int(limit),
            "current_banks": 0,
            "remaining": int(limit),
        }
