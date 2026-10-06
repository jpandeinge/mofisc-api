from datetime import datetime, timedelta, timezone

from api.app.routes.organisation_routes import _normalise_user_type
from api.app.utils.subscription import (
    PLAN_LIMITS,
    get_plan_entitlements,
    resolve_subscription_entitlements,
)


def test_only_enterprise_has_api_and_workspace_access():
    for plan in ("free", "basic", "premium"):
        assert PLAN_LIMITS[plan]["api_access"] is False
        assert PLAN_LIMITS[plan]["organisation_workspace"] is False

    assert PLAN_LIMITS["enterprise"]["api_access"] is True
    assert PLAN_LIMITS["enterprise"]["organisation_workspace"] is True
    assert PLAN_LIMITS["enterprise"]["custom_model_configuration"] is True
    assert PLAN_LIMITS["enterprise"]["advanced_data_analytics"] is True
    assert PLAN_LIMITS["enterprise"]["brand_customisation"] is True


def test_enterprise_entitlements_are_json_safe():
    entitlements = get_plan_entitlements("enterprise")

    assert entitlements["maxTransactionsPerMonth"] is None
    assert entitlements["maxBankAccounts"] is None
    assert entitlements["teamMembers"] is None
    assert entitlements["apiAccess"] is True


def test_trialing_enterprise_subscription_grants_enterprise_access():
    has_access, effective_plan = resolve_subscription_entitlements(
        plan="enterprise",
        status="trialing",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=7),
        cancel_at_period_end=False,
    )

    assert has_access is True
    assert effective_plan == "enterprise"


def test_expired_enterprise_subscription_fails_closed_to_free():
    has_access, effective_plan = resolve_subscription_entitlements(
        plan="enterprise",
        status="expired",
        current_period_end=datetime.now(timezone.utc) - timedelta(seconds=1),
        cancel_at_period_end=False,
    )

    assert has_access is False
    assert effective_plan == "free"


def test_expired_enterprise_trial_fails_closed_to_free():
    has_access, effective_plan = resolve_subscription_entitlements(
        plan="enterprise",
        status="trialing",
        current_period_end=datetime.now(timezone.utc) - timedelta(seconds=1),
        cancel_at_period_end=False,
    )

    assert has_access is False
    assert effective_plan == "free"


def test_organisation_spelling_is_normalised():
    assert _normalise_user_type("organisation") == "organisation"
    assert _normalise_user_type("organization") == "organisation"
    assert _normalise_user_type("individual") == "individual"
