# api/app/routes/user_metrics_routes.py

from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool

from ..config.settings import settings
from ..schemas.user_metrics import (
    UserActivityMetricsResponse,
    UserFinancialMetricsResponse,
)
from ..sentry import SentryTransaction, set_sentry_context, set_sentry_user
from ..services.user_metrics.metrics_service import UserMetricsSupabaseService
from ..utils.auth import get_current_user
from ..utils.logging_config import get_logger
from ..utils.subscription import PLAN_LIMITS, get_user_subscription
from ..utils.supabase_utils import run_supabase_read

logger = get_logger(__name__)
router = APIRouter(prefix="/metrics", tags=["metrics"])

# Initialize the service instance
user_metrics_service = UserMetricsSupabaseService()


@router.get("")  # Removed response_model to allow flexible fields like subscriptions
async def get_user_metrics(
    request: Request,
    force_recalculate: bool = Query(
        False, description="Force recalculation of metrics"
    ),
    bank_name: Optional[str] = Query(
        None, description="Filter metrics by bank name (e.g., 'fnb', 'nedbank')"
    ),
    current_user=Depends(get_current_user),
):
    """
    Get comprehensive user metrics including financial health, achievements, streaks, and activity data.
    This replaces frontend calculations for better security and consistency.

    Query Parameters:
        force_recalculate: Force recalculation of metrics (ignores cache)
        bank_name: Filter metrics for a specific bank (optional)
    """
    with SentryTransaction(name="get_comprehensive_user_metrics", op="calculation"):
        set_sentry_user(current_user)
        set_sentry_context(
            "user_metrics",
            {
                "user_id": str(current_user.id),
                "force_recalculate": force_recalculate,
                "bank_name": bank_name,
            },
        )

        try:
            subscription = await get_user_subscription(str(current_user.id))
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits.get("max_transactions_per_month", float("inf"))
            max_transactions = None if tx_limit == float("inf") else int(tx_limit)

            metrics = await run_in_threadpool(
                user_metrics_service.calculate_comprehensive_user_metrics,
                str(current_user.id),
                # Let the metrics service decide whether cached data is valid for this plan/window.
                # We only force recalculation when explicitly requested.
                force_recalculate=force_recalculate,
                bank_name=bank_name,
                max_transactions=max_transactions,
            )

            # Get subscriptions from Supabase (single source of truth)
            try:
                subscriptions_response = await run_in_threadpool(
                    run_supabase_read,
                    lambda client: client.table("user_subscriptions")
                    .select("*")
                    .eq("user_id", str(current_user.id))
                    .eq("is_active", True)
                    .execute(),
                )
                subscriptions = (
                    subscriptions_response.data if subscriptions_response.data else []
                )
                metrics["subscriptions"] = subscriptions
            except Exception as sub_error:
                logger.warning(
                    "Failed to get subscriptions (%s)", type(sub_error).__name__
                )
                metrics["subscriptions"] = []
                metrics["subscriptions_unavailable"] = True

            return {
                "success": True,
                "data": metrics,
                "calculated_at": datetime.utcnow().isoformat(),
                "user_id": str(current_user.id),
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error getting comprehensive user metrics for {current_user.id}: {str(e)}"
            )

            if settings.ENVIRONMENT == "production":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to calculate user metrics",
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to calculate user metrics: {str(e)}",
                )


@router.get("/financial", response_model=UserFinancialMetricsResponse)
async def get_financial_metrics(
    request: Request,
    bank_name: Optional[str] = Query(None, description="Filter metrics by bank name"),
    current_user=Depends(get_current_user),
):
    """Get financial metrics for the authenticated user"""
    with SentryTransaction(name="get_user_financial_metrics", op="calculation"):
        set_sentry_user(current_user)

        try:
            subscription = await get_user_subscription(str(current_user.id))
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits.get("max_transactions_per_month", float("inf"))
            max_transactions = None if tx_limit == float("inf") else int(tx_limit)

            metrics = await run_in_threadpool(
                user_metrics_service.calculate_comprehensive_user_metrics,
                str(current_user.id),
                force_recalculate=False,
                bank_name=bank_name,
                max_transactions=max_transactions,
            )

            financial_metrics = {
                "savings_streak": metrics.get("savings_streak", 0),
                "total_savings": metrics.get("total_savings", 0),
                "total_income": metrics.get("total_income", 0),
                "total_expenses": metrics.get("total_expenses", 0),
                "avg_daily_spending": metrics.get("avg_daily_spending", 0),
                "savings_rate": metrics.get("savings_rate", 0),
                "spending_trend": metrics.get("spending_trend", "stable"),
                "financial_score": metrics.get("financial_score", 50),
                "transaction_count": metrics.get("transaction_count", 0),
            }

            return {
                "success": True,
                "data": financial_metrics,
                "calculated_at": datetime.utcnow().isoformat(),
                "user_id": str(current_user.id),
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error getting financial metrics for {current_user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to calculate financial metrics",
            )


@router.get("/activity", response_model=UserActivityMetricsResponse)
async def get_activity_metrics(
    request: Request,
    bank_name: Optional[str] = Query(None, description="Filter metrics by bank name"),
    current_user=Depends(get_current_user),
):
    """Get activity metrics for the authenticated user"""
    with SentryTransaction(name="get_user_activity_metrics", op="calculation"):
        set_sentry_user(current_user)

        try:
            subscription = await get_user_subscription(str(current_user.id))
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits.get("max_transactions_per_month", float("inf"))
            max_transactions = None if tx_limit == float("inf") else int(tx_limit)

            metrics = await run_in_threadpool(
                user_metrics_service.calculate_comprehensive_user_metrics,
                str(current_user.id),
                force_recalculate=False,
                bank_name=bank_name,
                max_transactions=max_transactions,
            )

            activity_metrics = {
                "days_active": metrics.get("days_active", 0),
                "avg_transactions_per_day": metrics.get("avg_transactions_per_day", 0),
                "most_active_day": metrics.get("most_active_day"),
                "most_active_day_count": metrics.get("most_active_day_count", 0),
                "recent_activity": metrics.get("recent_activity", False),
                "transaction_count": metrics.get("transaction_count", 0),
            }

            return {
                "success": True,
                "data": activity_metrics,
                "calculated_at": datetime.utcnow().isoformat(),
                "user_id": str(current_user.id),
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error getting activity metrics for {current_user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to calculate activity metrics",
            )


@router.post("/recalculate", status_code=status.HTTP_200_OK)
async def recalculate_metrics(request: Request, current_user=Depends(get_current_user)):
    """Force recalculation of metrics for the authenticated user"""
    with SentryTransaction(name="recalculate_user_metrics", op="calculation"):
        set_sentry_user(current_user)

        try:
            metrics = await run_in_threadpool(
                user_metrics_service.calculate_comprehensive_user_metrics,
                str(current_user.id),
                force_recalculate=True,
            )

            return {
                "success": True,
                "message": "User metrics recalculated successfully",
                "calculated_at": datetime.utcnow().isoformat(),
                "metrics_summary": {
                    "financial_score": metrics.get("financial_score", 0),
                    "total_achievements": metrics.get("total_real_achievements", 0),
                    "current_level": metrics.get("current_level", 1),
                    "transaction_count": metrics.get("transaction_count", 0),
                },
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error recalculating user metrics for {current_user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to recalculate user metrics",
            )


@router.post("/initialize", status_code=status.HTTP_200_OK)
async def initialize_metrics(request: Request, current_user=Depends(get_current_user)):
    """Initialize metrics for the authenticated user - useful for first-time setup or data migration"""
    with SentryTransaction(name="initialize_user_metrics", op="initialization"):
        set_sentry_user(current_user)

        try:
            # Force calculation and storage of metrics
            metrics = await run_in_threadpool(
                user_metrics_service.calculate_comprehensive_user_metrics,
                str(current_user.id),
                force_recalculate=True,
            )

            return {
                "success": True,
                "message": "User metrics initialized successfully",
                "initialized_at": datetime.utcnow().isoformat(),
                "metrics_summary": {
                    "financial_score": metrics.get("financial_score", 0),
                    "total_achievements": metrics.get("total_real_achievements", 0),
                    "current_level": metrics.get("current_level", 1),
                    "total_points": metrics.get("total_points", 0),
                    "transaction_count": metrics.get("transaction_count", 0),
                },
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error initializing user metrics for {current_user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to initialize user metrics",
            )


@router.get("/summary")
async def get_metrics_summary(
    request: Request,
    bank_name: Optional[str] = Query(None, description="Filter metrics by bank name"),
    current_user=Depends(get_current_user),
):
    """Get a quick summary of metrics for dashboard widgets"""
    with SentryTransaction(name="get_user_metrics_summary", op="calculation"):
        set_sentry_user(current_user)

        try:
            subscription = await get_user_subscription(str(current_user.id))
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits.get("max_transactions_per_month", float("inf"))
            max_transactions = None if tx_limit == float("inf") else int(tx_limit)

            metrics = await run_in_threadpool(
                user_metrics_service.calculate_comprehensive_user_metrics,
                str(current_user.id),
                force_recalculate=False,
                bank_name=bank_name,
                max_transactions=max_transactions,
            )

            summary = {
                "financial_health_score": metrics.get("financial_score", 50),
                "current_level": metrics.get("current_level", 1),
                "total_points": metrics.get("total_points", 0),
                "savings_streak": metrics.get("savings_streak", 0),
                "total_achievements": metrics.get("total_real_achievements", 0),
                "savings_rate": round(metrics.get("savings_rate", 0), 1),
                "spending_trend": metrics.get("spending_trend", "stable"),
                "recent_activity": metrics.get("recent_activity", False),
                "last_calculated": metrics.get("last_calculated"),
            }

            return {"success": True, "data": summary, "user_id": str(current_user.id)}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error getting user metrics summary for {current_user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get user metrics summary",
            )


@router.get("/banks")
async def get_user_banks(
    request: Request,
    current_user=Depends(get_current_user),
):
    """
    Get list of banks from user's uploaded statements.
    Returns unique banks with transaction counts and display names.
    """
    with SentryTransaction(name="get_user_banks", op="query"):
        set_sentry_user(current_user)

        try:
            from ..services.transactions.transaction_service import (
                TransactionService,
            )
            from ..utils.bank_detection import format_bank_name

            # Imported at module level for consistent enforcement
            from ..utils.supabase_utils import get_supabase_client

            supabase = get_supabase_client(use_service_role=True)
            user_id = str(current_user.id)

            # Enforce plan-based visibility for bank list
            subscription = await get_user_subscription(user_id)
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits.get("max_transactions_per_month", float("inf"))
            tx_limit_int = 999999 if tx_limit == float("inf") else int(tx_limit)

            # Determine visible cap from total transactions
            count_response = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("id", count="exact")
                .eq("user_id", user_id)
                .execute()
            )
            total_transactions = count_response.count if count_response.count else 0
            visible_cap = (
                total_transactions
                if tx_limit_int == 999999
                else min(total_transactions, tx_limit_int)
            )

            # Fetch only the visible transactions to derive bank counts
            visible_transactions = TransactionService.get_all_user_transactions(
                user_id, max_records=visible_cap
            )

            bank_counts: Dict[str, int] = {}
            for row in visible_transactions:
                bank = row.get("bank_name")
                if bank:
                    bank_counts[bank] = bank_counts.get(bank, 0) + 1

            # Build response with display names
            banks = [
                {
                    "name": bank,
                    "display_name": format_bank_name(bank),
                    "transaction_count": count,
                }
                for bank, count in sorted(bank_counts.items())
            ]

            return {
                "success": True,
                "banks": banks,
                "total_banks": len(banks),
                "total_transactions": sum(bank_counts.values()),
                "hidden_transactions": max(0, total_transactions - visible_cap),
                "visible_limit": None if tx_limit_int == 999999 else tx_limit_int,
                "plan": plan,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting user banks for {current_user.id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get user banks",
            )
