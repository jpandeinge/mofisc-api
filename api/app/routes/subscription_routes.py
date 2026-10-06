# api/app/routes/subscription_routes.py
"""
Dodo Payments subscription and payment routes.
Uses Dodo Payments Checkout Sessions for a seamless payment experience.
Supports 150+ countries with 80+ currencies and 30+ payment methods.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..config.settings import settings
from ..utils.auth import get_current_user
from ..utils.logging_config import get_logger
from ..utils.subscription import get_plan_entitlements, get_user_subscription
from ..utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)

router = APIRouter()


def _is_admin(current_user) -> bool:
    if getattr(current_user, "is_admin", False):
        return True
    role = None
    if hasattr(current_user, "get"):
        role = current_user.get("role")
    else:
        role = getattr(current_user, "role", None)
    return role == "admin"


def _enforce_user_match(current_user, user_id: str) -> None:
    if _is_admin(current_user):
        return
    if not user_id or str(current_user.id) != str(user_id):
        raise HTTPException(status_code=403, detail="User mismatch")


def _parse_dt(value: Optional[object]) -> Optional[datetime]:
    """Parse various datetime formats from Dodo API responses."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return None


# Dodo Payments Product IDs from Dashboard
PLAN_PRODUCT_IDS = {
    "basic_monthly": "pdt_0NVPvjCZq88G9WqWbdMrz",
    "basic_annually": "pdt_0NVPw1RNlxZpXIywMSaSf",
    "premium_monthly": "pdt_0NVPwBZDQEwBbamxDujp5",
    "premium_annually": "pdt_0NVPwNekgeYf8630uGpaM",
}

# Plan prices in cents (smallest currency unit)
PLAN_PRICES = {
    "basic_monthly": 299,  # $2.99
    "basic_annually": 2870,  # $28.70 (20% off yearly)
    "premium_monthly": 799,  # $7.99
    "premium_annually": 7670,  # $76.70 (20% off yearly)
}

# Trial period in days (from settings, can be set to 0 for testing)
TRIAL_PERIOD_DAYS = settings.DODO_PAYMENTS_TRIAL_DAYS


def _safe_upsert_subscription(supabase_client, data: dict):
    """
    Upsert helper that gracefully handles unique constraint collisions on user_id.

    Supabase upsert should merge on the conflict target, but we've seen 23505 errors
    from Postgres when a pending record already exists. In that case we fall back
    to an update to keep a single authoritative row per user.
    """
    user_id = data.get("user_id")

    try:
        return (
            supabase_client.table("billing_subscriptions")
            .upsert(data, on_conflict="user_id")
            .execute()
        )
    except Exception as e:
        error_message = str(e)
        error_code = getattr(e, "code", None)
        duplicate_violation = (
            error_code == "23505"
            or "duplicate key value violates unique constraint" in error_message
        )

        if duplicate_violation and user_id:
            logger.warning(
                f"Duplicate billing_subscriptions row for user {user_id}; applying update fallback."
            )
            return (
                supabase_client.table("billing_subscriptions")
                .update(data)
                .eq("user_id", user_id)
                .execute()
            )

        raise


class CreateCheckoutRequest(BaseModel):
    """Request body for creating a checkout session."""

    plan: str  # basic, premium
    billing_frequency: str  # monthly, annually
    user_id: str
    user_email: str
    user_name: Optional[str] = None


class CreateCheckoutResponse(BaseModel):
    """Response for checkout session creation."""

    checkout_url: str
    session_id: str


class CustomerPortalRequest(BaseModel):
    """Request for customer portal access."""

    user_id: str


async def dodo_api_request(
    method: str,
    endpoint: str,
    data: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
) -> dict:
    """Make authenticated request to Dodo Payments API."""
    url = f"{settings.DODO_PAYMENTS_BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {settings.DODO_PAYMENTS_API_KEY}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method.upper() == "GET":
            response = await client.get(url, headers=headers)
        elif method.upper() == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method.upper() == "PATCH":
            response = await client.patch(url, headers=headers, json=data)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        if response.status_code >= 400:
            logger.error(f"Dodo API error: {response.status_code} - {response.text}")
            # Handle empty response body gracefully
            error_message = "Payment API error"
            try:
                error_body = response.json()
                error_message = error_body.get("message", error_message)
            except (json.JSONDecodeError, ValueError):
                # Empty or non-JSON response
                error_message = f"Payment API error (HTTP {response.status_code})"
            raise HTTPException(
                status_code=response.status_code,
                detail=error_message,
            )

        # Handle empty success responses
        if not response.text or response.text.strip() == "":
            return {}

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                f"Dodo API returned non-JSON response: {response.text[:100]}"
            )
            return {}


@router.post("/create-checkout-session", response_model=CreateCheckoutResponse)
async def create_checkout_session(
    request: CreateCheckoutRequest, current_user=Depends(get_current_user)
):
    """
    Create a Dodo Payments Checkout Session for subscription.

    Uses Dodo Payments hosted checkout for best UX.
    Returns the checkout URL for redirect.
    """
    try:
        # Validate plan
        if request.plan not in ["basic", "premium"]:
            raise HTTPException(status_code=400, detail="Invalid plan selected")

        if request.billing_frequency not in ["monthly", "annually"]:
            raise HTTPException(status_code=400, detail="Invalid billing frequency")

        user_id = str(current_user.id)
        _enforce_user_match(current_user, request.user_id)

        user_email = getattr(current_user, "email", None) or request.user_email
        if (
            request.user_email
            and user_email
            and request.user_email.lower() != user_email.lower()
        ):
            raise HTTPException(status_code=400, detail="User email mismatch")
        if not user_email:
            raise HTTPException(status_code=400, detail="User email is required")

        supabase = get_supabase_client(use_service_role=True)

        # Check if user already has a Dodo customer ID
        user_response = (
            supabase.table("billing_subscriptions")
            .select("dodo_customer_id")
            .eq("user_id", user_id)
            .execute()
        )

        customer_id = None
        if user_response.data and user_response.data[0].get("dodo_customer_id"):
            customer_id = user_response.data[0]["dodo_customer_id"]

        # Get product ID for selected plan
        price_key = f"{request.plan}_{request.billing_frequency}"
        product_id = PLAN_PRODUCT_IDS.get(price_key)

        # Calculate period start/end (trial if enabled, otherwise billing period)
        period_start = datetime.now(timezone.utc)
        if TRIAL_PERIOD_DAYS > 0:
            # Trial period: 7 days before first charge
            period_end = period_start + timedelta(days=TRIAL_PERIOD_DAYS)
        else:
            # No trial: first billing period based on frequency
            period_days = 30 if request.billing_frequency == "monthly" else 365
            period_end = period_start + timedelta(days=period_days)

        # Build checkout data per Dodo API spec
        # Docs: https://docs.dodopayments.com/developer-resources/checkout-session
        # Official example from docs:
        # {
        #   "product_cart": [{"product_id": "prod_123", "quantity": 1}],
        #   "customer": {"email": "user@example.com", "name": "John Doe"},
        #   "subscription_data": {"trial_period_days": 14},
        #   "billing_currency": "USD",
        #   "return_url": "https://yoursite.com/success"
        # }
        checkout_data = {
            "product_cart": [],  # Required: filled below
            "customer": {
                "email": user_email,
                "name": request.user_name or user_email.split("@")[0],
            },
            "return_url": f"{settings.DODO_PAYMENTS_SUCCESS_URL}?user_id={user_id}&plan={request.plan}",
            # NOTE: Do NOT set billing_currency here - let DoDo's adaptive currency
            # (enabled in dashboard) handle automatic currency conversion for
            # international cards (e.g., African banks like Standard Bank)
            "metadata": {
                "user_id": user_id,
                "plan": request.plan,
                "billing_frequency": request.billing_frequency,
            },
        }

        # Per official docs: trial_period_days goes INSIDE subscription_data
        # See: https://docs.dodopayments.com/developer-resources/checkout-session
        if TRIAL_PERIOD_DAYS > 0:
            checkout_data["subscription_data"] = {
                "trial_period_days": TRIAL_PERIOD_DAYS
            }

        # For existing customers, use customer_id instead of email/name
        if customer_id:
            checkout_data["customer"] = {"customer_id": customer_id}

        # Get the price for this plan
        price_amount = PLAN_PRICES.get(price_key)
        if not price_amount:
            raise HTTPException(
                status_code=500,
                detail=f"Price not configured for plan: {price_key}",
            )

        # Determine billing interval for recurring
        billing_interval = "month" if request.billing_frequency == "monthly" else "year"

        # Use product_id from Dodo Dashboard
        # IMPORTANT: Products in Dodo Dashboard should be configured as:
        # - Type: Subscription
        # - Price: The recurring amount (e.g., $2.99)
        # - Trial: NONE (we pass trial_period_days via API, not Dashboard)
        #
        # If you get "order_amount: Invalid amount entered" error, check:
        # 1. Remove trial from Dashboard product (we set it via API)
        # 2. Ensure product is set as "Subscription" type, not "One-time"
        # 3. Verify the price is set correctly in Dashboard
        if not product_id:
            raise HTTPException(
                status_code=500,
                detail=f"No product_id configured for plan: {price_key}. "
                "Please configure products in Dodo Payments Dashboard.",
            )

        checkout_data["product_cart"] = [
            {
                "product_id": product_id,
                "quantity": 1,
            }
        ]
        logger.info(
            f"Creating checkout for {price_key} with product {product_id}: "
            f"expected ${price_amount/100:.2f} (billing: {billing_interval}, trial: {TRIAL_PERIOD_DAYS} days)"
        )
        logger.info(
            f"Checkout request payload: {json.dumps(checkout_data, default=str)}"
        )

        # Create checkout session
        # Idempotency for checkout creation to avoid duplicate sessions on retry
        idempotency_key = f"{user_id}-{request.plan}-{request.billing_frequency}"
        response = await dodo_api_request(
            "POST",
            "/checkouts",
            checkout_data,
            extra_headers={"Idempotency-Key": idempotency_key},
        )

        session_id = response.get("session_id")
        checkout_url = response.get("checkout_url")

        if not checkout_url:
            raise HTTPException(
                status_code=500, detail="Failed to create checkout session"
            )

        # Store pending subscription with session_id for webhook correlation
        pending_record = {
            "user_id": user_id,
            "status": "pending",
            "plan": request.plan,
            "current_period_start": period_start.isoformat(),
            "current_period_end": period_end.isoformat(),
        }

        # Store session_id in metadata for lookup during webhook processing
        # This helps correlate webhooks that might not have user_id in metadata
        if not customer_id:
            _safe_upsert_subscription(supabase, pending_record)

        logger.info(
            f"Created checkout session for user {user_id}: session_id={session_id}"
        )

        return CreateCheckoutResponse(checkout_url=checkout_url, session_id=session_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


class CustomerPortalResponse(BaseModel):
    """Response for customer portal request."""

    portal_url: Optional[str] = None
    action_required: Optional[str] = None
    message: str
    can_manage: bool = True


@router.post("/customer-portal", response_model=CustomerPortalResponse)
async def create_customer_portal_session(
    request: CustomerPortalRequest, current_user=Depends(get_current_user)
):
    """
    Get customer portal URL for managing subscriptions.

    Uses Dodo Payments Customer Portal API to generate a secure session URL.
    Handles edge cases gracefully with actionable responses:
    - No subscription: Redirect to pricing
    - No customer ID: Offer re-subscription or support contact
    - API failure: Fallback to direct portal URL
    """
    try:
        _enforce_user_match(current_user, request.user_id)
        user_email = getattr(current_user, "email", None) or request.user_email
        if (
            request.user_email
            and user_email
            and request.user_email.lower() != user_email.lower()
        ):
            raise HTTPException(status_code=400, detail="User email mismatch")
        supabase = get_supabase_client(use_service_role=True)

        # Get subscription data from our database
        user_response = (
            supabase.table("billing_subscriptions")
            .select("dodo_customer_id, dodo_subscription_id, plan, status")
            .eq("user_id", request.user_id)
            .execute()
        )

        # Case 1: No subscription record exists - user is on free plan
        if not user_response.data:
            logger.info(f"No subscription found for user {request.user_id}")
            return CustomerPortalResponse(
                portal_url=None,
                action_required="upgrade",
                message="No active subscription. Upgrade to manage your billing.",
                can_manage=False,
            )

        subscription_data = user_response.data[0]
        customer_id = subscription_data.get("dodo_customer_id")
        subscription_id = subscription_data.get("dodo_subscription_id")
        plan = subscription_data.get("plan", "free")
        status = subscription_data.get("status")

        # Case 2: Subscription exists but in pending/incomplete state (likely waiting for provider sync)
        if status in ["pending", "incomplete"]:
            logger.info(f"Subscription in {status} state for user {request.user_id}")
            return CustomerPortalResponse(
                portal_url=None,
                action_required="complete_payment",
                message="Your subscription setup is processing. If you’ve completed checkout, please try again in a moment or tap Sync.",
                can_manage=False,
            )

        # Case 3: Free plan with a record but no customer ID
        if plan == "free" and not customer_id:
            logger.info(f"Free plan user {request.user_id} without customer ID")
            return CustomerPortalResponse(
                portal_url=None,
                action_required="upgrade",
                message="Upgrade your plan to access subscription management.",
                can_manage=False,
            )

        # Case 4: Paid plan but missing customer ID (webhook issue)
        if not customer_id:
            logger.warning(
                f"Subscription exists but no customer ID for user {request.user_id}"
            )
            return CustomerPortalResponse(
                portal_url=None,
                action_required="contact_support",
                message="Unable to locate your payment record. Please contact support or try re-subscribing.",
                can_manage=False,
            )

        # Case 5: Normal flow - generate portal session
        portal_data = {
            "return_url": settings.DODO_PAYMENTS_SUCCESS_URL.replace(
                "/payment/success", "/settings"
            ),
        }

        try:
            response = await dodo_api_request(
                "POST", f"/customers/{customer_id}/portal-session", portal_data
            )
            portal_url = response.get("portal_url") or response.get("url")

            if portal_url:
                logger.info(
                    f"Created customer portal session for user {request.user_id}"
                )
                return CustomerPortalResponse(
                    portal_url=portal_url,
                    message="Opening subscription management portal.",
                    can_manage=True,
                )

            # Response didn't include URL - try fallback
            logger.warning(f"Portal API response missing URL: {response}")

        except HTTPException as e:
            logger.warning(
                f"Portal session API failed ({e.status_code}), trying fallback"
            )

        # Case 6: Fallback - construct portal URL directly using subscription ID
        if subscription_id:
            portal_base = (
                settings.DODO_PAYMENTS_BASE_URL.replace("api.", "checkout.")
                .replace("test.", "checkout.")
                .replace("live.", "checkout.")
            )
            portal_url = f"{portal_base}/manage/{subscription_id}"
            logger.info(f"Using fallback portal URL for user {request.user_id}")
            return CustomerPortalResponse(
                portal_url=portal_url,
                message="Opening subscription management portal.",
                can_manage=True,
            )

        # Case 7: No portal URL and no subscription ID - contact support
        return CustomerPortalResponse(
            portal_url=None,
            action_required="contact_support",
            message="Unable to access subscription portal. Please contact support.",
            can_manage=False,
        )

    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to access subscription management"
        )


def verify_webhook_signature(
    payload: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
) -> bool:
    """
    Verify Dodo Payments webhook signature using HMAC SHA256.
    Follows Standard Webhooks specification.
    """
    try:
        if not settings.DODO_PAYMENTS_WEBHOOK_SECRET:
            logger.warning("Webhook secret not configured")
            return False

        # Construct the signed payload
        signed_payload = f"{webhook_id}.{webhook_timestamp}.{payload.decode('utf-8')}"

        # Compute expected signature
        expected_sig = hmac.new(
            settings.DODO_PAYMENTS_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Compare signatures (handle v1, prefix format)
        signatures = webhook_signature.split(",")
        for sig in signatures:
            sig = sig.strip()
            if sig.startswith("v1,"):
                sig = sig[3:]
            if hmac.compare_digest(expected_sig, sig):
                return True

        return False
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {e}")
        return False


@router.post("/webhook")
async def dodo_webhook(
    request: Request,
    webhook_id: Optional[str] = Header(None, alias="webhook-id"),
    webhook_timestamp: Optional[str] = Header(None, alias="webhook-timestamp"),
    webhook_signature: Optional[str] = Header(None, alias="webhook-signature"),
):
    """
    Handle Dodo Payments webhook events for subscription lifecycle.

    Events handled:
    - payment.succeeded: Payment completed successfully
    - payment.failed: Payment failed
    - subscription.created: New subscription created
    - subscription.active: Subscription is now active
    - subscription.cancelled: Subscription cancelled
    """
    payload = await request.body()

    # Verify webhook signature
    if webhook_id and webhook_timestamp and webhook_signature:
        if not verify_webhook_signature(
            payload, webhook_id, webhook_timestamp, webhook_signature
        ):
            logger.error("Invalid webhook signature")
            raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        logger.error("Invalid webhook payload")
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = event.get("type")
    data = event.get("data", {})

    supabase = get_supabase_client(use_service_role=True)

    logger.info(
        "Received webhook",
        extra={"event_type": event_type, "data_keys": list(data.keys())},
    )

    # Helper to extract IDs from various response formats
    def extract_customer_id(obj):
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return obj.get("customer_id") or obj.get("id")
        return None

    def extract_subscription_id(obj):
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return obj.get("subscription_id") or obj.get("id")
        return None

    if event_type == "payment.succeeded":
        # Payment successful - update subscription status
        payment = data.get("payment", data)
        metadata = payment.get("metadata", {})
        user_id = metadata.get("user_id")
        customer_id = extract_customer_id(payment.get("customer"))
        subscription_id = extract_subscription_id(
            payment.get("subscription") or payment.get("subscription_id")
        )

        logger.info(
            f"Payment webhook data keys: {list(payment.keys())}, "
            f"extracted customer_id={customer_id}, subscription_id={subscription_id}"
        )

        # Fallback: if subscription_id missing but we have a customer_id, fetch subscriptions to capture IDs
        if not subscription_id and customer_id:
            try:
                customer_subs = await dodo_api_request(
                    "GET", f"/customers/{customer_id}/subscriptions"
                )
                subs_list = (
                    customer_subs.get("items", [])
                    if isinstance(customer_subs, dict)
                    else customer_subs if isinstance(customer_subs, list) else []
                )
                if subs_list and len(subs_list) > 0:
                    subscription_id = extract_subscription_id(subs_list[0])
            except HTTPException as e:
                logger.warning(
                    f"Could not fetch subscriptions for customer {customer_id}: {e.detail}"
                )

        # If no user_id in metadata, try to find user by subscription_id or customer_id
        if not user_id:
            user_lookup = None

            if subscription_id:
                user_lookup = (
                    supabase.table("billing_subscriptions")
                    .select("user_id")
                    .eq("dodo_subscription_id", subscription_id)
                    .execute()
                )

            if (not user_lookup or not user_lookup.data) and customer_id:
                user_lookup = (
                    supabase.table("billing_subscriptions")
                    .select("user_id")
                    .eq("dodo_customer_id", customer_id)
                    .execute()
                )

            if user_lookup and user_lookup.data:
                user_id = user_lookup.data[0]["user_id"]

        if user_id:
            update_data = {"status": "active"}
            if customer_id:
                update_data["dodo_customer_id"] = customer_id
            if subscription_id:
                update_data["dodo_subscription_id"] = subscription_id

            _safe_upsert_subscription(supabase, {"user_id": user_id, **update_data})
            logger.info(
                f"Payment succeeded for user {user_id}, "
                f"customer_id={customer_id}, subscription_id={subscription_id}"
            )
        else:
            logger.warning(
                f"Could not identify user for payment webhook: "
                f"customer_id={customer_id}, subscription_id={subscription_id}"
            )

    elif event_type in [
        "subscription.created",
        "subscription.active",
        "subscription.updated",
        "subscription.renewed",
        "subscription.trialing",  # Dodo sends this when trial starts
    ]:
        # Subscription created/activated/in trial
        subscription = data.get("subscription", data)
        subscription_id = extract_subscription_id(subscription)
        customer_id = extract_customer_id(subscription.get("customer"))
        metadata = subscription.get("metadata", {})
        user_id = metadata.get("user_id")
        plan = metadata.get("plan", "basic")

        # Log all keys for debugging ID extraction
        logger.info(
            f"Subscription webhook data keys: {list(subscription.keys())}, "
            f"extracted subscription_id={subscription_id}, customer_id={customer_id}"
        )

        # If no user_id in metadata, try to find user by subscription_id or customer_id
        if not user_id:
            user_lookup = None

            # First try subscription_id
            if subscription_id:
                user_lookup = (
                    supabase.table("billing_subscriptions")
                    .select("user_id, plan")
                    .eq("dodo_subscription_id", subscription_id)
                    .execute()
                )

            # Then try customer_id
            if (not user_lookup or not user_lookup.data) and customer_id:
                user_lookup = (
                    supabase.table("billing_subscriptions")
                    .select("user_id, plan")
                    .eq("dodo_customer_id", customer_id)
                    .execute()
                )

            # Finally, try customer email if available
            if not user_lookup or not user_lookup.data:
                customer_email = (
                    subscription.get("customer", {}).get("email")
                    if isinstance(subscription.get("customer"), dict)
                    else None
                )
                if customer_email:
                    # Look up user by email in auth.users (via profiles or similar)
                    # For now, try finding a pending subscription record
                    pending_lookup = (
                        supabase.table("billing_subscriptions")
                        .select("user_id, plan")
                        .eq("status", "pending")
                        .execute()
                    )
                    if pending_lookup.data:
                        # This is a simplified lookup - in production, correlate by email
                        user_id = pending_lookup.data[0]["user_id"]
                        if not plan or plan == "basic":
                            plan = pending_lookup.data[0].get("plan", "basic")

            if user_lookup and user_lookup.data:
                user_id = user_lookup.data[0]["user_id"]
                if not plan or plan == "basic":
                    plan = user_lookup.data[0].get("plan", "basic")

        # Determine status - map DoDo statuses to our status values
        sub_status = subscription.get("status", "").lower()
        if sub_status == "trialing":
            status = "trialing"
        elif sub_status in ["past_due", "on_hold"]:
            status = "past_due"
        elif sub_status in ["failed", "incomplete"]:
            # subscription.updated with status=failed means initial payment failed
            status = "incomplete"
            logger.warning(
                f"Subscription status is '{sub_status}' in subscription.updated webhook "
                f"for user {user_id}"
            )
        elif sub_status in ["cancelled", "canceled", "expired"]:
            status = "cancelled"
        else:
            status = "active"

        if user_id:
            upsert_data = {
                "user_id": user_id,
                "plan": plan,
                "status": status,
            }

            if customer_id:
                upsert_data["dodo_customer_id"] = customer_id
            if subscription_id:
                upsert_data["dodo_subscription_id"] = subscription_id

            # Store period end or trial end
            period_start = subscription.get("current_period_start") or subscription.get(
                "trial_start"
            )
            period_end = subscription.get("current_period_end") or subscription.get(
                "trial_end"
            )
            # Fallbacks for Dodo payloads that use next/previous billing dates
            if not period_start:
                period_start = subscription.get(
                    "previous_billing_date"
                ) or subscription.get("created_at")
            if not period_end:
                period_end = subscription.get("next_billing_date") or subscription.get(
                    "expires_at"
                )
            if period_start:
                upsert_data["current_period_start"] = period_start
            if period_end:
                upsert_data["current_period_end"] = period_end

            _safe_upsert_subscription(supabase, upsert_data)
            logger.info(
                f"Subscription {status} for user {user_id}: plan={plan}, "
                f"subscription_id={subscription_id}, customer_id={customer_id}"
            )
        else:
            # Log warning if we couldn't identify the user
            logger.warning(
                f"Could not identify user for subscription webhook: "
                f"subscription_id={subscription_id}, customer_id={customer_id}"
            )

    elif event_type == "subscription.on_hold":
        # Subscription on hold - payment failed, awaiting retry or new payment method
        subscription = data.get("subscription", data)
        subscription_id = extract_subscription_id(subscription)
        customer_id = extract_customer_id(subscription.get("customer"))
        metadata = subscription.get("metadata", {})
        user_id = metadata.get("user_id")
        customer = subscription.get("customer", {})
        customer_email = customer.get("email") if isinstance(customer, dict) else None

        # DoDo on_hold webhook has expires_at (when access ends) and next_billing_date
        expires_at = subscription.get("expires_at")
        next_billing = subscription.get("next_billing_date")

        logger.warning(
            f"Subscription ON_HOLD for user {user_id}: "
            f"subscription_id={subscription_id}, customer_email={customer_email}, "
            f"expires_at={expires_at}, next_billing={next_billing}"
        )

        if user_id:
            update_data = {
                "user_id": user_id,
                "status": "past_due",
            }
            if customer_id:
                update_data["dodo_customer_id"] = customer_id
            if subscription_id:
                update_data["dodo_subscription_id"] = subscription_id
            # Use expires_at as period end (when access will be revoked)
            if expires_at:
                update_data["current_period_end"] = expires_at
            elif subscription.get("current_period_end"):
                update_data["current_period_end"] = subscription.get(
                    "current_period_end"
                )

            _safe_upsert_subscription(supabase, update_data)
            logger.info(
                f"Updated billing_subscriptions for user {user_id} to status=past_due (on_hold)"
            )

    elif event_type == "subscription.failed":
        subscription = data.get("subscription", data)
        metadata = subscription.get("metadata", {})
        user_id = metadata.get("user_id")
        subscription_id = extract_subscription_id(subscription)
        payment_method_id = subscription.get("payment_method_id")
        customer = subscription.get("customer", {})
        customer_email = customer.get("email") if isinstance(customer, dict) else None

        # Extract failure details from various possible fields
        failure_reason = (
            subscription.get("failure_reason")
            or subscription.get("error_code")
            or subscription.get("status_reason")
            or "payment_declined"
        )
        failure_message = (
            subscription.get("failure_message")
            or subscription.get("error_message")
            or subscription.get("status_message")
            or "Payment method was declined. Please try a different card."
        )
        last_payment = subscription.get("last_payment_error", {})
        decline_code = (
            last_payment.get("decline_code") or subscription.get("decline_code") or ""
        )

        # Log comprehensive failure details for debugging
        logger.warning(
            f"Subscription FAILED for user {user_id}: "
            f"subscription_id={subscription_id}, "
            f"payment_method_id={payment_method_id}, "
            f"customer_email={customer_email}, "
            f"reason={failure_reason}, message={failure_message}, "
            f"decline_code={decline_code}"
        )
        logger.info(
            f"Full subscription.failed payload: {json.dumps(subscription, default=str)}"
        )

        # Find user if not in metadata (fallback lookup)
        if not user_id and subscription_id:
            user_response = (
                supabase.table("billing_subscriptions")
                .select("user_id")
                .eq("dodo_subscription_id", subscription_id)
                .execute()
            )
            if user_response.data:
                user_id = user_response.data[0]["user_id"]

        if user_id:
            # Update subscription status - use only known columns
            update_data = {"status": "incomplete"}
            if subscription_id:
                update_data["dodo_subscription_id"] = subscription_id

            try:
                supabase.table("billing_subscriptions").update(update_data).eq(
                    "user_id", user_id
                ).execute()
                logger.info(
                    f"Updated billing_subscriptions for user {user_id} to status=incomplete "
                    f"(failure: {failure_reason} - {failure_message})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to update billing_subscriptions for user {user_id}: {e}"
                )

    elif event_type in ["subscription.cancelled", "subscription.canceled"]:
        # Subscription cancelled - Revolut-style: keep access until period ends
        subscription = data.get("subscription", data)
        subscription_id = extract_subscription_id(subscription)
        customer_id = extract_customer_id(subscription.get("customer"))
        metadata = subscription.get("metadata", {})
        user_id = metadata.get("user_id")

        # Try to find user by subscription ID or customer ID
        if not user_id:
            user_response = None
            if subscription_id:
                user_response = (
                    supabase.table("billing_subscriptions")
                    .select("user_id, plan")
                    .eq("dodo_subscription_id", subscription_id)
                    .execute()
                )
            if (not user_response or not user_response.data) and customer_id:
                user_response = (
                    supabase.table("billing_subscriptions")
                    .select("user_id, plan")
                    .eq("dodo_customer_id", customer_id)
                    .execute()
                )

            if user_response and user_response.data:
                user_id = user_response.data[0]["user_id"]

        if user_id:
            # Don't immediately downgrade - let them keep access until period ends
            update_data = {
                "status": "cancelled",
                "cancel_at_period_end": True,
            }

            # If Dodo provides the period end, update it
            period_end = subscription.get("current_period_end") or subscription.get(
                "next_billing_date"
            )
            if period_end:
                update_data["current_period_end"] = period_end

            supabase.table("billing_subscriptions").update(update_data).eq(
                "user_id", user_id
            ).execute()

            logger.info(
                f"Subscription cancelled for user {user_id}. "
                f"Access continues until period ends."
            )

    elif event_type in ["subscription.expired", "subscription.ended"]:
        # Subscription fully expired - downgrade to free
        subscription = data.get("subscription", data)
        subscription_id = extract_subscription_id(subscription)
        customer_id = extract_customer_id(subscription.get("customer"))
        metadata = subscription.get("metadata", {})
        user_id = metadata.get("user_id")

        # Try to find user
        if not user_id:
            user_response = None
            if subscription_id:
                user_response = (
                    supabase.table("billing_subscriptions")
                    .select("user_id")
                    .eq("dodo_subscription_id", subscription_id)
                    .execute()
                )
            if (not user_response or not user_response.data) and customer_id:
                user_response = (
                    supabase.table("billing_subscriptions")
                    .select("user_id")
                    .eq("dodo_customer_id", customer_id)
                    .execute()
                )

            if user_response and user_response.data:
                user_id = user_response.data[0]["user_id"]

        if user_id:
            supabase.table("billing_subscriptions").update(
                {
                    "status": "expired",
                    "plan": "free",
                    "cancel_at_period_end": False,
                }
            ).eq("user_id", user_id).execute()

            logger.info(f"Subscription expired for user {user_id}, downgraded to free")

    elif event_type == "payment.failed":
        # Payment failed - capture detailed failure information
        payment = data.get("payment", data)
        metadata = payment.get("metadata", {})
        user_id = metadata.get("user_id")
        subscription_id = payment.get("subscription_id")
        payment_id = payment.get("payment_id")
        customer = payment.get("customer", {})
        customer_email = customer.get("email") if isinstance(customer, dict) else None

        # Extract failure details from Dodo webhook (fields at top level)
        error_code = payment.get("error_code") or "unknown"
        error_message = payment.get("error_message") or "Payment failed"

        # Card details are at top level in Dodo webhooks
        card_network = payment.get("card_network") or "unknown"
        card_last_four = payment.get("card_last_four") or "****"
        card_country = payment.get("card_issuing_country") or "unknown"
        card_type = payment.get("card_type") or "unknown"

        # Log comprehensive failure details
        logger.warning(
            f"Payment FAILED for user {user_id}: "
            f"error_code={error_code}, error_message={error_message}, "
            f"payment_id={payment_id}, subscription_id={subscription_id}, "
            f"card={card_network} {card_type} ****{card_last_four} ({card_country}), "
            f"customer_email={customer_email}"
        )
        logger.info(f"Full payment.failed payload: {json.dumps(payment, default=str)}")

        # Find user if not in metadata
        if not user_id and subscription_id:
            user_response = (
                supabase.table("billing_subscriptions")
                .select("user_id")
                .eq("dodo_subscription_id", subscription_id)
                .execute()
            )
            if user_response.data:
                user_id = user_response.data[0]["user_id"]

        if user_id:
            try:
                update_data = {"status": "past_due"}
                if subscription_id:
                    update_data["dodo_subscription_id"] = subscription_id

                supabase.table("billing_subscriptions").update(update_data).eq(
                    "user_id", user_id
                ).execute()
                logger.info(
                    f"Updated billing_subscriptions for user {user_id} to status=past_due "
                    f"(error: {error_code} - {error_message})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to update billing_subscriptions for user {user_id}: {e}"
                )

    return {"status": "success"}


@router.get("/subscription-status/{user_id}")
async def get_subscription_status(user_id: str, current_user=Depends(get_current_user)):
    """
    Get current subscription status for a user.
    Includes cancel_at_period_end for Revolut-style cancellation UX.
    """
    try:
        _enforce_user_match(current_user, user_id)
        subscription = await get_user_subscription(user_id)

        def to_epoch_seconds(value):
            if not value:
                return None
            try:
                if isinstance(value, (int, float)):
                    return int(value)
                # ISO string
                return int(
                    datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                )
            except Exception:
                return None

        current_end_epoch = to_epoch_seconds(subscription.get("current_period_end"))
        current_start_epoch = to_epoch_seconds(subscription.get("current_period_start"))
        raw_status = subscription.get("status", "active")
        cancel_at_period_end = bool(subscription.get("cancel_at_period_end") or False)

        return {
            "plan": subscription.get("raw_plan", subscription.get("plan", "free")),
            "effective_plan": subscription.get("effective_plan", "free"),
            "status": raw_status,
            "current_period_start": current_start_epoch,
            "current_period_end": current_end_epoch,
            "is_trial": raw_status == "trialing",
            "cancel_at_period_end": cancel_at_period_end,
            "has_active_access": subscription.get("has_active_access", True),
            "organisation_id": subscription.get("organisation_id"),
            "entitlements": get_plan_entitlements(
                subscription.get("effective_plan", "free")
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting subscription status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get subscription status")


class SyncSubscriptionRequest(BaseModel):
    """Request for syncing subscription from Dodo Payments."""

    user_id: str
    user_email: Optional[str] = None  # Optional: used to look up customer by email


@router.post("/sync-subscription")
async def sync_subscription_from_provider(
    request: SyncSubscriptionRequest, current_user=Depends(get_current_user)
):
    """
    Sync subscription status from Dodo Payments.

    Use this endpoint when webhook delivery may have failed.
    Fetches the latest subscription state from Dodo and updates our database.

    Can look up customer by email if dodo_customer_id is not available.
    """
    try:
        _enforce_user_match(current_user, request.user_id)
        supabase = get_supabase_client(use_service_role=True)

        # Check if user has a customer ID
        user_response = (
            supabase.table("billing_subscriptions")
            .select("dodo_customer_id, dodo_subscription_id, plan, status")
            .eq("user_id", request.user_id)
            .execute()
        )

        if not user_response.data:
            return {
                "synced": False,
                "message": "No subscription record found. User is on free plan.",
                "plan": "free",
                "status": "active",
                "provider_status": None,
                "provider_plan": None,
            }

        subscription_data = user_response.data[0]
        customer_id = subscription_data.get("dodo_customer_id")
        subscription_id = subscription_data.get("dodo_subscription_id")

        # If no customer_id, try to find customer by email from Dodo
        user_email = request.user_email
        if not customer_id and not subscription_id and user_email:
            try:
                # Dodo Payments: List customers with email filter
                customers_response = await dodo_api_request(
                    "GET", f"/customers?email={user_email}"
                )
                customers = (
                    customers_response.get("items", [])
                    if isinstance(customers_response, dict)
                    else (
                        customers_response
                        if isinstance(customers_response, list)
                        else []
                    )
                )
                if customers and len(customers) > 0:
                    customer = customers[0]
                    customer_id = customer.get("customer_id") or customer.get("id")
                    logger.info(f"Found customer by email {user_email}: {customer_id}")
            except HTTPException as e:
                logger.warning(f"Could not look up customer by email: {e.status_code}")

        if not customer_id and not subscription_id:
            return {
                "synced": False,
                "message": (
                    "Waiting for the payment provider to finalize your subscription. "
                    "If you just completed checkout, it will sync automatically shortly."
                ),
                "plan": subscription_data.get("plan", "free"),
                "status": subscription_data.get("status", "pending"),
                "provider_status": None,
                "provider_plan": None,
            }

        # Fetch subscription from Dodo Payments
        dodo_subscription = None
        try:
            if subscription_id:
                dodo_subscription = await dodo_api_request(
                    "GET", f"/subscriptions/{subscription_id}"
                )
            elif customer_id:
                # Try to get subscriptions by customer
                customer_subs = await dodo_api_request(
                    "GET", f"/customers/{customer_id}/subscriptions"
                )
                if (
                    customer_subs
                    and isinstance(customer_subs, list)
                    and len(customer_subs) > 0
                ):
                    dodo_subscription = customer_subs[0]
        except HTTPException as e:
            if e.status_code == 404:
                logger.warning(
                    f"Subscription not found in Dodo for user {request.user_id}"
                )
                return {
                    "synced": False,
                    "message": "Subscription not found in payment provider. Contact support if this persists.",
                    "plan": subscription_data.get("plan", "free"),
                    "status": subscription_data.get("status", "unknown"),
                    "provider_status": "not_found",
                    "provider_plan": None,
                }
            raise

        if not dodo_subscription:
            return {
                "synced": False,
                "message": "Unable to retrieve subscription from payment provider.",
                "plan": subscription_data.get("plan", "free"),
                "status": subscription_data.get("status", "unknown"),
                "provider_status": None,
                "provider_plan": None,
            }

        # Map Dodo status to our status
        dodo_status = dodo_subscription.get("status", "").lower()
        our_status = {
            "active": "active",
            "trialing": "trialing",
            "past_due": "past_due",
            "on_hold": "past_due",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "paused": "cancelled",
            "expired": "expired",
            "incomplete": "incomplete",
            "pending": "pending",
            "failed": "failed",  # Payment failed
            "requires_payment": "pending",  # Needs new payment method
            "unpaid": "past_due",  # Subscription unpaid
        }.get(dodo_status, subscription_data.get("status", "unknown"))

        # Check cancel_at_period_end from Dodo
        cancel_at_period_end = dodo_subscription.get("cancel_at_period_end", False)

        # Update our database
        update_data = {
            "status": our_status,
            "cancel_at_period_end": cancel_at_period_end,
        }

        # Update customer ID if we got it
        customer_obj = dodo_subscription.get("customer", {})
        if isinstance(customer_obj, dict) and customer_obj.get("customer_id"):
            update_data["dodo_customer_id"] = customer_obj["customer_id"]
        elif isinstance(customer_obj, str):
            update_data["dodo_customer_id"] = customer_obj

        # Update subscription ID if we got it
        if dodo_subscription.get("subscription_id"):
            update_data["dodo_subscription_id"] = dodo_subscription["subscription_id"]

        # Update period dates if available
        if dodo_subscription.get("current_period_start"):
            update_data["current_period_start"] = dodo_subscription[
                "current_period_start"
            ]
        if dodo_subscription.get("current_period_end"):
            update_data["current_period_end"] = dodo_subscription["current_period_end"]
        # Fallback to next_billing_date
        elif dodo_subscription.get("next_billing_date"):
            update_data["current_period_end"] = dodo_subscription["next_billing_date"]

        # Update plan from metadata if available
        if dodo_subscription.get("metadata", {}).get("plan"):
            update_data["plan"] = dodo_subscription["metadata"]["plan"]

        supabase.table("billing_subscriptions").update(update_data).eq(
            "user_id", request.user_id
        ).execute()

        logger.info(
            f"Synced subscription for user {request.user_id}: "
            f"status={our_status}, cancel_at_period_end={cancel_at_period_end}"
        )

        return {
            "synced": True,
            "message": "Subscription synced successfully.",
            "plan": update_data.get("plan", subscription_data.get("plan", "free")),
            "status": our_status,
            "provider_status": dodo_status or "unknown",
            "provider_plan": dodo_subscription.get("metadata", {}).get("plan"),
            "cancel_at_period_end": cancel_at_period_end,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing subscription: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to sync subscription")


class VerifySessionResponse(BaseModel):
    """Response for session verification."""

    verified: bool
    plan: str
    billing_frequency: str
    status: str
    user_email: Optional[str] = None
    amount_paid: Optional[float] = None
    currency: Optional[str] = None
    next_billing_date: Optional[str] = None
    is_trial: bool = False
    trial_ends: Optional[str] = None
    error_message: Optional[str] = None  # Card decline reason for better UX


@router.get("/verify-session/{session_id}", response_model=VerifySessionResponse)
async def verify_checkout_session(
    session_id: str,
    subscription_id: Optional[str] = None,
    user_id: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """
    Verify a checkout session and return subscription details.

    Called by the success page to confirm payment and display
    the correct tier information to the user. Also stores the
    dodo_customer_id and dodo_subscription_id in our database.
    """

    def is_placeholder_session(value: Optional[str]) -> bool:
        """Detect placeholder/missing session IDs from redirect."""
        if not value:
            return True
        placeholders = {
            "{session_id}",
            "{{session_id}}",
            "%7Bsession_id%7D",
            "session_id",
            "null",
            "undefined",
            "lookup",  # Frontend uses this when it only has subscription_id
        }
        return value in placeholders

    def is_placeholder_subscription(value: Optional[str]) -> bool:
        if not value:
            return True
        placeholders = {
            "{subscription_id}",
            "{{subscription_id}}",
            "%7Bsubscription_id%7D",
            "subscription_id",
            "null",
            "undefined",
        }
        return value in placeholders

    try:
        if user_id:
            _enforce_user_match(current_user, user_id)
        # Try checkout session first when we have a real ID
        session_data = None
        if not is_placeholder_session(session_id):
            try:
                session_data = await dodo_api_request("GET", f"/checkouts/{session_id}")
            except HTTPException as e:
                if e.status_code == 404:
                    logger.warning(f"Checkout session not found: {session_id}")
                else:
                    raise

        # Fallback: verify by subscription_id if provided (Dodo redirects may only include subscription_id)
        if (
            (not session_data)
            and subscription_id
            and not is_placeholder_subscription(subscription_id)
        ):
            logger.info(f"Verifying via subscription fallback: {subscription_id}")
            try:
                session_data = await dodo_api_request(
                    "GET", f"/subscriptions/{subscription_id}"
                )
                logger.info(
                    f"Subscription lookup successful: status={session_data.get('status')}"
                )
            except HTTPException as e:
                if e.status_code == 404:
                    logger.warning(
                        f"Subscription not found in fallback verification: {subscription_id}"
                    )
                else:
                    raise

        if not session_data:
            # No direct session data - try to find subscription via customer lookup
            if user_id:
                supabase = get_supabase_client(use_service_role=True)
                stored = (
                    supabase.table("billing_subscriptions")
                    .select("*")
                    .eq("user_id", user_id)
                    .execute()
                )
                if stored.data:
                    sub = stored.data[0]
                    stored_customer_id = sub.get("dodo_customer_id")
                    stored_subscription_id = sub.get("dodo_subscription_id")
                    stored_plan = sub.get("plan", "unknown")
                    stored_status = sub.get("status", "unknown")

                    # If we have IDs, try to fetch latest status from Dodo
                    if stored_subscription_id or stored_customer_id:
                        try:
                            dodo_sub = None
                            if stored_subscription_id:
                                dodo_sub = await dodo_api_request(
                                    "GET", f"/subscriptions/{stored_subscription_id}"
                                )
                            elif stored_customer_id:
                                # Get customer's subscriptions
                                customer_subs = await dodo_api_request(
                                    "GET",
                                    f"/customers/{stored_customer_id}/subscriptions",
                                )
                                subs_list = (
                                    customer_subs.get("items", [])
                                    if isinstance(customer_subs, dict)
                                    else (
                                        customer_subs
                                        if isinstance(customer_subs, list)
                                        else []
                                    )
                                )
                                if subs_list:
                                    dodo_sub = subs_list[0]

                            if dodo_sub:
                                dodo_status = dodo_sub.get("status", "").lower()
                                is_trial = dodo_status == "trialing"
                                is_active = dodo_status in ["active", "trialing"]

                                # Update database with real status
                                update_data = {
                                    "user_id": user_id,
                                    "status": (
                                        "trialing"
                                        if is_trial
                                        else ("active" if is_active else dodo_status)
                                    ),
                                }
                                if dodo_sub.get("subscription_id"):
                                    update_data["dodo_subscription_id"] = dodo_sub[
                                        "subscription_id"
                                    ]
                                sub_customer = dodo_sub.get("customer", {})
                                if isinstance(sub_customer, dict) and sub_customer.get(
                                    "customer_id"
                                ):
                                    update_data["dodo_customer_id"] = sub_customer[
                                        "customer_id"
                                    ]
                                if dodo_sub.get("current_period_end"):
                                    update_data["current_period_end"] = dodo_sub[
                                        "current_period_end"
                                    ]
                                if dodo_sub.get("current_period_start"):
                                    update_data["current_period_start"] = dodo_sub[
                                        "current_period_start"
                                    ]

                                _safe_upsert_subscription(supabase, update_data)
                                logger.info(
                                    f"Verified subscription via Dodo lookup for user {user_id}: status={dodo_status}"
                                )

                                if is_active:
                                    return VerifySessionResponse(
                                        verified=True,
                                        plan=stored_plan,
                                        billing_frequency="monthly",
                                        status="trialing" if is_trial else "active",
                                        user_email=None,
                                        amount_paid=None,
                                        currency="USD",
                                        next_billing_date=dodo_sub.get(
                                            "current_period_end"
                                        ),
                                        is_trial=is_trial,
                                        trial_ends=(
                                            dodo_sub.get("current_period_end")
                                            if is_trial
                                            else None
                                        ),
                                    )
                        except HTTPException as e:
                            logger.warning(
                                f"Could not verify via Dodo lookup: {e.status_code}"
                            )

                    # Return stored state as pending/unverified
                    return VerifySessionResponse(
                        verified=stored_status in ["active", "trialing"],
                        plan=stored_plan,
                        billing_frequency="monthly",
                        status=stored_status,
                        user_email=None,
                        amount_paid=None,
                        currency=None,
                        next_billing_date=sub.get("current_period_end"),
                        is_trial=stored_status == "trialing",
                        trial_ends=(
                            sub.get("current_period_end")
                            if stored_status == "trialing"
                            else None
                        ),
                    )

            return VerifySessionResponse(
                verified=False,
                plan="unknown",
                billing_frequency="monthly",
                status="not_found",
            )

        logger.info(f"Checkout session data keys: {list(session_data.keys())}")

        # Extract metadata from session
        metadata = session_data.get("metadata", {})
        plan = metadata.get("plan", "basic")
        billing_frequency = metadata.get("billing_frequency", "monthly")
        metadata_user_id = metadata.get("user_id")

        # Optional tamper check: if caller supplies user_id, enforce it matches metadata
        if user_id and metadata_user_id and user_id != metadata_user_id:
            logger.warning(
                f"Verification user mismatch: caller={user_id}, metadata={metadata_user_id}"
            )
            raise HTTPException(
                status_code=403,
                detail="Verification user does not match checkout metadata.",
            )

        resolved_user_id = metadata_user_id or user_id

        # Get session status - handle various Dodo response structures
        session_status = session_data.get("status", "").lower()
        payment_status = session_data.get("payment_status", "").lower()
        subscription_data = session_data.get("subscription", {}) or {}
        subscription_status = subscription_data.get("status", "").lower()

        # Valid statuses for verified subscription
        valid_statuses = [
            "completed",
            "paid",
            "succeeded",
            "active",
            "trialing",
        ]

        final_status = subscription_status or payment_status or session_status

        # Extract customer info BEFORE status check - we need IDs even for failed subscriptions
        customer = session_data.get("customer", {})
        if isinstance(customer, str):
            customer_id = customer
            user_email = session_data.get("customer_email")
        else:
            customer_id = customer.get("customer_id") or customer.get("id")
            user_email = customer.get("email") or session_data.get("customer_email")

        # Extract subscription ID - handle various response formats
        subscription_id = (
            None if is_placeholder_subscription(subscription_id) else subscription_id
        ) or (
            subscription_data.get("subscription_id")
            or subscription_data.get("id")
            or session_data.get("subscription_id")
        )

        # If status is not valid, save IDs anyway (for sync-subscription) and return unverified
        if final_status not in valid_statuses:
            # Extract failure details for debugging
            failure_reason = (
                session_data.get("failure_reason")
                or session_data.get("error_code")
                or subscription_data.get("failure_reason")
                or subscription_data.get("last_payment_error", {}).get("code")
                or "unknown"
            )
            failure_message = (
                session_data.get("failure_message")
                or session_data.get("error_message")
                or subscription_data.get("last_payment_error", {}).get("message")
                or ""
            )
            decline_code = (
                session_data.get("decline_code")
                or subscription_data.get("last_payment_error", {}).get("decline_code")
                or ""
            )

            logger.warning(
                f"Session {session_id} failed - status: {final_status}, "
                f"reason: {failure_reason}, message: {failure_message}, "
                f"decline_code: {decline_code}, "
                f"session_data_keys: {list(session_data.keys())}"
            )

            # Still save the IDs so sync-subscription can work later
            if user_id or metadata.get("user_id"):
                resolved_user = metadata.get("user_id") or user_id
                supabase = get_supabase_client(use_service_role=True)
                update_data = {
                    "user_id": resolved_user,
                    "status": final_status,
                    "plan": plan,
                }
                if subscription_id:
                    update_data["dodo_subscription_id"] = subscription_id
                    logger.info(
                        f"Storing subscription ID for failed/pending: {subscription_id}"
                    )
                if customer_id:
                    update_data["dodo_customer_id"] = customer_id
                    logger.info(
                        f"Storing customer ID for failed/pending: {customer_id}"
                    )

                _safe_upsert_subscription(supabase, update_data)

            # Build user-friendly error message
            user_error_message = None
            if failure_message:
                user_error_message = failure_message
            elif decline_code:
                # Map common decline codes to user-friendly messages
                decline_messages = {
                    "card_declined": "Your card was declined by your bank.",
                    "insufficient_funds": "Insufficient funds on your card.",
                    "expired_card": "Your card has expired.",
                    "incorrect_cvc": "The security code (CVC) was incorrect.",
                    "processing_error": "A processing error occurred. Please try again.",
                    "do_not_honor": "Your bank declined the transaction.",
                    "transaction_not_allowed": "This transaction is not allowed by your bank.",
                    "pickup_card": "Your card cannot be used for this transaction.",
                    "restricted_card": "Your card is restricted from this type of purchase.",
                    "lost_card": "This card has been reported lost.",
                    "stolen_card": "This card has been reported stolen.",
                    "generic_decline": "Your card was declined.",
                }
                user_error_message = decline_messages.get(
                    decline_code.lower(), f"Payment declined: {decline_code}"
                )
            elif failure_reason and failure_reason != "unknown":
                user_error_message = f"Payment failed: {failure_reason}"

            return VerifySessionResponse(
                verified=False,
                plan=plan,
                billing_frequency=billing_frequency,
                status=final_status,
                error_message=user_error_message,
            )

        # Per Dodo docs: checkout session returns payment_id, use it to get subscription details
        payment_id = session_data.get("payment_id")
        if payment_id and (not subscription_id or not customer_id):
            try:
                payment_data = await dodo_api_request("GET", f"/payments/{payment_id}")
                logger.info(f"Payment data keys: {list(payment_data.keys())}")

                # Extract subscription_id from payment
                if not subscription_id:
                    subscription_id = (
                        payment_data.get("subscription_id")
                        or payment_data.get("subscription", {}).get("subscription_id")
                        or payment_data.get("subscription", {}).get("id")
                    )

                # Extract customer_id from payment
                if not customer_id:
                    payment_customer = payment_data.get("customer", {})
                    if isinstance(payment_customer, str):
                        customer_id = payment_customer
                    else:
                        customer_id = payment_customer.get(
                            "customer_id"
                        ) or payment_customer.get("id")

            except HTTPException as e:
                logger.warning(
                    f"Payment fetch during verify failed: {payment_id} ({e.status_code})"
                )

        # If we have a subscription_id but no subscription payload, fetch it for accurate status/period/customer
        if subscription_id and (not subscription_data or not customer_id):
            try:
                subscription_data = await dodo_api_request(
                    "GET", f"/subscriptions/{subscription_id}"
                )
                subscription_status = subscription_data.get("status", "").lower()
                logger.info(f"Subscription data keys: {list(subscription_data.keys())}")

                # Extract customer_id from subscription
                if not customer_id:
                    sub_customer = subscription_data.get("customer", {})
                    if isinstance(sub_customer, str):
                        customer_id = sub_customer
                    else:
                        customer_id = sub_customer.get(
                            "customer_id"
                        ) or sub_customer.get("id")

            except HTTPException as e:
                logger.warning(
                    f"Subscription fetch during verify failed: {subscription_id} ({e.status_code})"
                )

        # Get payment details for display
        payment = session_data.get("payment", {})
        if not payment:
            payment = (
                session_data.get("payments", [{}])[0]
                if session_data.get("payments")
                else {}
            )

        amount = payment.get("amount", 0)
        currency = payment.get("currency", "USD")

        # Extract period end - handle various formats
        current_period_start = subscription_data.get(
            "current_period_start"
        ) or subscription_data.get("trial_start")
        current_period_end = subscription_data.get(
            "current_period_end"
        ) or subscription_data.get("trial_end")

        # Check for trial status (Dodo can report either "trialing" status or a future trial_end)
        subscription_status = subscription_data.get("status", "").lower()
        subscription_data_block = session_data.get("subscription_data", {}) or {}
        trial_period_days = (
            subscription_data.get("trial_period_days")
            or subscription_data_block.get("trial_period_days")
            or session_data.get("trial_period_days")
        )
        trial_start_candidate = (
            subscription_data.get("trial_start")
            or subscription_data.get("trial_starts_at")
            or subscription_data.get("trial_start_at")
        )
        trial_end_candidate = (
            subscription_data.get("trial_end")
            or subscription_data.get("trial_ends_at")
            or subscription_data.get("trial_end_at")
        )
        trial_end_dt = _parse_dt(trial_end_candidate)

        is_trial = False
        if subscription_status == "trialing":
            is_trial = True
        elif trial_end_dt and trial_end_dt > datetime.now(timezone.utc):
            # Only treat as a trial when the provider actually surfaces trial_* fields (or we know trial was requested)
            is_trial = bool(trial_start_candidate or trial_period_days)

        # If we have a user_id, ensure their subscription is updated with IDs
        if resolved_user_id:
            supabase = get_supabase_client(use_service_role=True)

            update_data = {
                "user_id": resolved_user_id,
                "plan": plan,
                "status": "trialing" if is_trial else "active",
            }

            # Store the critical IDs from Dodo Payments
            if subscription_id:
                update_data["dodo_subscription_id"] = subscription_id
                logger.info(f"Storing subscription ID: {subscription_id}")

            if customer_id:
                update_data["dodo_customer_id"] = customer_id
                logger.info(f"Storing customer ID: {customer_id}")

            if current_period_start:
                update_data["current_period_start"] = current_period_start

            if current_period_end:
                update_data["current_period_end"] = current_period_end

            # Upsert subscription record - this ensures IDs are captured
            _safe_upsert_subscription(supabase, update_data)

            logger.info(
                f"Verified session for user {resolved_user_id}: plan={plan}, "
                f"subscription_id={subscription_id}, customer_id={customer_id}"
            )

        # Format dates for display
        from datetime import datetime

        next_billing = None
        trial_ends = None
        period_end = current_period_end

        if period_end:
            try:
                if isinstance(period_end, int):
                    date_obj = datetime.fromtimestamp(period_end)
                    if is_trial:
                        trial_ends = date_obj.strftime("%B %d, %Y")
                    else:
                        next_billing = date_obj.strftime("%B %d, %Y")
                elif isinstance(period_end, str):
                    if is_trial:
                        trial_ends = period_end
                    else:
                        next_billing = period_end
            except Exception as e:
                logger.warning(f"Could not format date: {e}")

        # For trials, calculate when trial ends based on TRIAL_PERIOD_DAYS
        if is_trial and not trial_ends:
            from datetime import timedelta

            trial_end_date = datetime.now() + timedelta(days=TRIAL_PERIOD_DAYS)
            trial_ends = trial_end_date.strftime("%B %d, %Y")

        return VerifySessionResponse(
            verified=True,
            plan=plan,
            billing_frequency=billing_frequency,
            status="trialing" if is_trial else "active",
            user_email=user_email,
            amount_paid=amount / 100 if amount else None,
            currency=currency.upper() if currency else None,
            next_billing_date=next_billing,
            is_trial=is_trial,
            trial_ends=trial_ends,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying session: {str(e)}")
        # Return partial data rather than failing completely
        return VerifySessionResponse(
            verified=False,
            plan="unknown",
            billing_frequency="monthly",
            status="error",
        )


class CancelSubscriptionRequest(BaseModel):
    """Request for cancelling a subscription."""

    user_id: str
    reason: Optional[str] = None
    feedback: Optional[str] = None


class CancelSubscriptionResponse(BaseModel):
    """Response for subscription cancellation."""

    success: bool
    message: str
    cancel_at_period_end: bool = False
    current_period_end: Optional[str] = None
    plan: str = "free"


@router.post("/cancel-subscription", response_model=CancelSubscriptionResponse)
async def cancel_subscription(
    request: CancelSubscriptionRequest, current_user=Depends(get_current_user)
):
    """
    Cancel a subscription at the end of the current billing period.

    Revolut-style cancellation:
    - User keeps access until the billing period ends
    - Subscription won't renew
    - Can be reactivated before period ends
    """
    try:
        _enforce_user_match(current_user, request.user_id)
        supabase = get_supabase_client(use_service_role=True)

        # Get subscription data
        user_response = (
            supabase.table("billing_subscriptions")
            .select(
                "dodo_subscription_id, dodo_customer_id, plan, status, current_period_end"
            )
            .eq("user_id", request.user_id)
            .execute()
        )

        if not user_response.data:
            return CancelSubscriptionResponse(
                success=False,
                message="No active subscription found.",
                plan="free",
            )

        subscription_data = user_response.data[0]
        subscription_id = subscription_data.get("dodo_subscription_id")
        plan = subscription_data.get("plan", "free")
        status = subscription_data.get("status")

        # Check if already cancelled
        if status == "cancelled":
            return CancelSubscriptionResponse(
                success=False,
                message="Subscription is already cancelled.",
                cancel_at_period_end=True,
                current_period_end=subscription_data.get("current_period_end"),
                plan=plan,
            )

        # Free plan cannot be cancelled
        if plan == "free":
            return CancelSubscriptionResponse(
                success=False,
                message="Free plan does not require cancellation.",
                plan="free",
            )

        # Call Dodo Payments API to schedule cancellation at period end
        if subscription_id:
            try:
                await dodo_api_request(
                    "PATCH",
                    f"/subscriptions/{subscription_id}",
                    {"cancel_at_period_end": True},
                )
                logger.info(
                    f"Scheduled cancellation for subscription {subscription_id} (user {request.user_id})"
                )
            except HTTPException as e:
                logger.warning(
                    f"Dodo API cancellation failed ({e.status_code}), updating local state only"
                )

        # Update local database - mark as pending cancellation
        supabase.table("billing_subscriptions").update(
            {
                "cancel_at_period_end": True,
                "status": "cancelled",
            }
        ).eq("user_id", request.user_id).execute()

        # Log cancellation reason for analytics (optional)
        if request.reason or request.feedback:
            logger.info(
                f"User {request.user_id} cancelled subscription. "
                f"Reason: {request.reason}, Feedback: {request.feedback}"
            )

        return CancelSubscriptionResponse(
            success=True,
            message=f"Your {plan.title()} subscription will end on your next billing date. You'll keep full access until then.",
            cancel_at_period_end=True,
            current_period_end=subscription_data.get("current_period_end"),
            plan=plan,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling subscription: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")


class ReactivateSubscriptionRequest(BaseModel):
    """Request for reactivating a cancelled subscription."""

    user_id: str


class ReactivateSubscriptionResponse(BaseModel):
    """Response for subscription reactivation."""

    success: bool
    message: str
    status: str
    plan: str


@router.post("/reactivate-subscription", response_model=ReactivateSubscriptionResponse)
async def reactivate_subscription(
    request: ReactivateSubscriptionRequest, current_user=Depends(get_current_user)
):
    """
    Reactivate a cancelled subscription before the period ends.

    Revolut-style reactivation:
    - Only works if subscription was cancelled but period hasn't ended
    - Removes the cancel_at_period_end flag
    - Subscription continues as normal
    """
    try:
        _enforce_user_match(current_user, request.user_id)
        supabase = get_supabase_client(use_service_role=True)

        # Get subscription data
        user_response = (
            supabase.table("billing_subscriptions")
            .select(
                "dodo_subscription_id, plan, status, current_period_end, cancel_at_period_end"
            )
            .eq("user_id", request.user_id)
            .execute()
        )

        if not user_response.data:
            return ReactivateSubscriptionResponse(
                success=False,
                message="No subscription found to reactivate.",
                status="none",
                plan="free",
            )

        subscription_data = user_response.data[0]
        subscription_id = subscription_data.get("dodo_subscription_id")
        plan = subscription_data.get("plan", "free")
        status = subscription_data.get("status")
        period_end = subscription_data.get("current_period_end")

        # Check if subscription can be reactivated
        if status != "cancelled" and not subscription_data.get("cancel_at_period_end"):
            return ReactivateSubscriptionResponse(
                success=False,
                message="Subscription is already active.",
                status=status,
                plan=plan,
            )

        # Check if period has already ended
        if period_end:
            try:
                end_date = datetime.fromisoformat(
                    period_end.replace("Z", "+00:00")
                    if isinstance(period_end, str)
                    else period_end
                )
                if end_date < datetime.now(timezone.utc):
                    return ReactivateSubscriptionResponse(
                        success=False,
                        message="Subscription period has ended. Please subscribe again to continue.",
                        status="expired",
                        plan="free",
                    )
            except (ValueError, TypeError):
                pass

        # Call Dodo Payments API to remove cancellation
        if subscription_id:
            try:
                await dodo_api_request(
                    "PATCH",
                    f"/subscriptions/{subscription_id}",
                    {"cancel_at_period_end": False},
                )
                logger.info(
                    f"Reactivated subscription {subscription_id} (user {request.user_id})"
                )
            except HTTPException as e:
                logger.warning(
                    f"Dodo API reactivation failed ({e.status_code}), updating local state only"
                )

        # Update local database
        supabase.table("billing_subscriptions").update(
            {
                "cancel_at_period_end": False,
                "status": "active",
            }
        ).eq("user_id", request.user_id).execute()

        return ReactivateSubscriptionResponse(
            success=True,
            message=f"Welcome back! Your {plan.title()} subscription has been reactivated.",
            status="active",
            plan=plan,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reactivating subscription: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to reactivate subscription")


class UsageResponse(BaseModel):
    """Response for subscription usage statistics."""

    plan: str
    status: str
    total_transactions: int
    visible_transactions: int
    hidden_transactions: int

    # Transaction usage
    transactions_used: int
    transactions_limit: int
    transactions_remaining: int
    transactions_percent: float

    # Bank account usage
    bank_accounts_used: int
    bank_accounts_limit: int
    bank_accounts_remaining: int

    # Warning thresholds
    is_near_limit: bool  # > 80% used
    is_at_limit: bool  # 100% used

    # Billing period
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    days_until_reset: int = 0


@router.get("/usage/{user_id}", response_model=UsageResponse)
async def get_subscription_usage(user_id: str, current_user=Depends(get_current_user)):
    """
    Get current subscription usage statistics.

    Revolut-style usage tracking:
    - Shows exactly how many transactions used this month
    - Shows bank account usage
    - Indicates when user is near or at limits
    - Provides clear upgrade signals
    """
    try:
        _enforce_user_match(current_user, user_id)
        supabase = get_supabase_client(use_service_role=True)

        subscription = await get_user_subscription(user_id)
        plan = subscription.get("effective_plan", subscription.get("plan", "free"))
        status = subscription.get("status", "active")
        period_start = subscription.get("current_period_start")
        period_end = subscription.get("current_period_end")

        # Get plan limits
        from ..utils.subscription import PLAN_LIMITS

        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        transaction_limit = limits["max_transactions_per_month"]
        bank_limit = limits["max_bank_accounts"]

        # Count total transactions for the user
        now = datetime.now(timezone.utc)

        tx_response = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        total_transactions = tx_response.count if tx_response.count else 0

        # Count distinct bank accounts
        bank_response = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("bank_name")
            .eq("user_id", user_id)
            .execute()
        )
        bank_names = set()
        for row in bank_response.data or []:
            if row.get("bank_name"):
                bank_names.add(row["bank_name"])
        bank_accounts_used = len(bank_names)

        # Calculate remaining and percentages
        if transaction_limit == float("inf"):
            visible_transactions = total_transactions
            hidden_transactions = 0
            transactions_remaining = 999999
            transactions_percent = 0.0
            tx_limit_int = 999999
        else:
            tx_limit_int = int(transaction_limit)
            visible_transactions = min(total_transactions, tx_limit_int)
            hidden_transactions = max(0, total_transactions - tx_limit_int)
            transactions_remaining = max(0, tx_limit_int - visible_transactions)
            transactions_percent = (
                (visible_transactions / tx_limit_int) * 100 if tx_limit_int > 0 else 0
            )

        if bank_limit == float("inf"):
            bank_accounts_remaining = 999999
            bank_limit_int = 999999
        else:
            bank_limit_int = int(bank_limit)
            bank_accounts_remaining = max(0, bank_limit_int - bank_accounts_used)

        # Calculate days until billing period reset (not calendar month)
        # Paid users: reset based on their subscription period
        # Free users: reset based on calendar month
        if period_end and plan != "free":
            try:
                # Parse period_end (could be ISO string or datetime)
                if isinstance(period_end, str):
                    # Handle various ISO formats
                    period_end_str = period_end.replace("Z", "+00:00")
                    if "+" not in period_end_str and "-" not in period_end_str[-6:]:
                        period_end_str += "+00:00"
                    period_end_dt = datetime.fromisoformat(period_end_str)
                else:
                    period_end_dt = period_end

                # Ensure timezone aware
                if period_end_dt.tzinfo is None:
                    period_end_dt = period_end_dt.replace(tzinfo=timezone.utc)

                days_until_reset = max(0, (period_end_dt - now).days)
            except Exception as e:
                logger.warning(f"Failed to parse period_end '{period_end}': {e}")
                # Fallback to calendar month
                next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
                days_until_reset = (next_month - now).days
        else:
            # Free plan uses calendar month reset
            next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            days_until_reset = (next_month - now).days

        return UsageResponse(
            plan=plan,
            status=status,
            total_transactions=total_transactions,
            visible_transactions=visible_transactions,
            hidden_transactions=hidden_transactions,
            transactions_used=visible_transactions,
            transactions_limit=tx_limit_int,
            transactions_remaining=transactions_remaining,
            transactions_percent=round(transactions_percent, 1),
            bank_accounts_used=bank_accounts_used,
            bank_accounts_limit=bank_limit_int,
            bank_accounts_remaining=bank_accounts_remaining,
            is_near_limit=transactions_percent >= 80,
            is_at_limit=(
                (visible_transactions >= tx_limit_int)
                if tx_limit_int < 999999
                else False
            ),
            period_start=period_start,
            period_end=period_end,
            days_until_reset=days_until_reset,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting usage stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get usage statistics")


@router.get("/config")
async def get_payment_config(current_user=Depends(get_current_user)):
    """
    Get payment configuration for frontend.
    Returns non-sensitive configuration needed for checkout.
    """
    return {
        "provider": "dodo_payments",
        "supported_countries": 150,
        "supported_currencies": 80,
        "supported_payment_methods": 30,
    }
