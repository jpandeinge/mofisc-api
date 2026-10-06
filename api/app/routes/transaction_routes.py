# api/app/routes/transaction_routes.py - Seamlessly enhanced single system

import datetime
import hashlib
import logging
import os
import tempfile
import uuid
from typing import List, Optional

import sentry_sdk
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..config.settings import settings
from ..errors.exceptions import (
    DatabaseError,
    NotFoundError,
    StatementProcessingError,
    ValidationError,
)
from ..sentry import (
    SentryTransaction,
    set_sentry_context,
    set_sentry_tag,
    set_sentry_user,
)
from ..services.achievements.achievement_service import AchievementService
from ..services.budgets.budget_alert_service import budget_alert_service
from ..services.notifications.base import NotificationPriority, NotificationType
from ..services.notifications.notification_service import notification_service
from ..services.transactions.transaction_service import TransactionService
from ..services.user_metrics.metrics_service import UserMetricsSupabaseService
from ..utils.async_classification import classify_transactions_background
from ..utils.auth import get_current_user
from ..utils.bank_detection import detect_bank_from_pdf, format_bank_name
from ..utils.db import get_db
from ..utils.hybrid_statement_ocr import (
    extract_bank_windhoek_account_statement,
    extract_fnb_account_statement,
    extract_nedbank_account_statement,
    extract_standard_bank_account_statement,
)
from ..utils.statement_ocr import BANK_CONFIGS
from ..utils.subscription import (
    PLAN_LIMITS,
    enforce_bank_account_limit,
    get_user_subscription,
)
from ..utils.supabase_utils import get_supabase_client

router = APIRouter()
logger = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 1024 * 1024


class UploadSizeExceededError(Exception):
    """Raised when an upload exceeds the configured limit."""


def _get_financial_gamification(user_id: str):
    """Read the Supabase service using its user-id-only interface."""
    game_stats = AchievementService.get_user_game_stats(user_id)
    streak_data = list(game_stats.get("streaks", {}).values())
    achievements = AchievementService.get_user_achievements(user_id)
    try:
        challenges = AchievementService.get_daily_challenges(user_id)
    except Exception as exc:
        logger.warning("Failed to get financial-metrics challenges: %s", exc)
        challenges = []
    savings_streak = next(
        (streak.get("current_count", 0) for streak in streak_data
         if streak.get("streak_type") == "savings"),
        0,
    )
    return {
        "streaks": streak_data,
        "achievements": achievements,
        "game_stats": game_stats,
        "active_challenges": challenges,
    }, savings_streak


async def _stream_upload_and_hash(
    file: UploadFile,
    filename: str,
    max_bytes: int,
    temp_path: Optional[str] = None,
) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size_bytes = 0
    temp_file = open(temp_path, "wb") if temp_path else None

    try:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                raise UploadSizeExceededError()
            hasher.update(chunk)
            if temp_file:
                temp_file.write(chunk)
        hasher.update(filename.encode("utf-8"))
        return hasher.hexdigest(), size_bytes
    finally:
        if temp_file:
            temp_file.close()


async def calculate_financial_metrics_background(
    db: Session,
    user_id: str,
    statement_id: Optional[str] = None,
    bank_name: Optional[str] = None,
):
    """Enhanced background calculation that integrates with existing workflow"""
    try:
        scope_parts = []
        if statement_id:
            scope_parts.append(f"statement {statement_id}")
        if bank_name:
            scope_parts.append(f"bank {bank_name}")
        scope = ", ".join(scope_parts) if scope_parts else "overall"

        logger.info(
            f"Calculating enhanced financial metrics for user {user_id} ({scope})"
        )

        # Note: FinancialCalculationService uses local PostgreSQL which is deprecated
        # All metrics are now calculated via Supabase UserMetricsSupabaseService

        # Calculate comprehensive user metrics using Supabase service (for Activity page)
        # Pass statement_id and bank_name to support filtered metrics
        user_metrics_service = UserMetricsSupabaseService()
        user_metrics_service.calculate_comprehensive_user_metrics(
            user_id,
            force_recalculate=True,
            statement_id=statement_id,
            bank_name=bank_name,
        )

        logger.info(
            f"Enhanced financial metrics and user metrics calculated successfully for user {user_id} ({scope})"
        )
    except Exception as e:
        logger.error(
            f"Enhanced metrics calculation failed for user {user_id}: {str(e)}"
        )
        sentry_sdk.capture_exception(e)


async def check_tier_limits_after_upload(
    user_id: str, new_transaction_count: int
) -> dict:
    """
    Check tier limits AFTER upload succeeds (production fintech UX).

    Returns tier information for upgrade messaging instead of blocking upload.
    This provides better UX by:
    1. Accepting the upload first (user sees their data)
    2. Then showing tier-appropriate upgrade message if needed

    Args:
        user_id: User ID
        new_transaction_count: Number of transactions just uploaded

    Returns:
        Dict with tier info: current_plan, limit, at_limit, over_limit, upgrade_message
    """
    from ..utils.supabase_utils import get_supabase_client

    try:
        subscription = await get_user_subscription(user_id)
        current_plan = subscription["plan"]
        limit = PLAN_LIMITS.get(current_plan, PLAN_LIMITS["free"])[
            "max_transactions_per_month"
        ]

        # Unlimited plans don't need tier checks
        if limit == float("inf"):
            return {
                "current_plan": current_plan,
                "current_count": 0,
                "limit": limit,
                "at_limit": False,
                "over_limit": False,
            }

        supabase = get_supabase_client(use_service_role=True)
        response = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )

        current_count = response.count if response.count else 0

        # Determine tier-appropriate messaging
        at_limit = current_count >= limit
        over_limit = current_count > limit

        # Generate tier-specific upgrade message
        upgrade_message = None
        suggested_plan = None

        if at_limit or over_limit:
            if current_plan == "free":
                suggested_plan = "basic"
                upgrade_message = (
                    f"You've reached your Free plan limit of {int(limit)} transactions. "
                    f"Upgrade to Basic (300 transactions/month) to view all {current_count} "
                    f"transactions and unlock advanced features."
                )
            elif current_plan == "basic":
                suggested_plan = "premium"
                upgrade_message = (
                    f"You've reached your Basic plan limit of {int(limit)} transactions. "
                    f"Upgrade to Premium for unlimited transactions and priority support."
                )
            else:
                # Fallback for edge cases
                upgrade_message = (
                    f"You've reached your plan limit of {int(limit)} transactions. "
                    f"Consider upgrading to view your full history."
                )

        return {
            "current_plan": current_plan,
            "current_count": current_count,
            "limit": int(limit),
            "at_limit": at_limit,
            "over_limit": over_limit,
            "upgrade_message": upgrade_message,
            "suggested_plan": suggested_plan,
        }

    except Exception as e:
        logger.error(f"Error checking tier limits for user {user_id}: {e}")
        # Return safe defaults on error - don't block upload
        return {
            "current_plan": "free",
            "current_count": 0,
            "limit": 100,
            "at_limit": False,
            "over_limit": False,
        }


def calculate_metrics(
    transactions: List[dict],
    db: Session = None,
    user_id: str = None,
    statement_id: str = None,
):
    """Enhanced calculate_metrics following industry financial standards"""
    with SentryTransaction(name="calculate_metrics", op="metrics"):
        # Input validation
        if not transactions or not isinstance(transactions, list):
            logger.warning("Invalid transactions input provided to calculate_metrics")
            return {
                "total_transactions": 0,
                "total_income": 0,
                "total_expenses": 0,
                "balance": 0,
                "net_cash_flow": 0,
                "average_transaction_amount": 0,
                "expense_ratio": 0,
                "savings_rate": 0,
                "enhanced": False,
            }

        # Calculate basic metrics (preserving original logic)
        total_transactions = len(transactions)

        # Industry standard: More sophisticated income/expense classification
        total_income = 0
        total_expenses = 0

        # Categorize transactions properly with data validation
        for t in transactions:
            try:
                # Validate transaction data
                if not isinstance(t, dict):
                    logger.warning(f"Invalid transaction format: {type(t)}")
                    continue

                amount = float(t.get("transaction_amount", 0))
                trans_type = str(t.get("transaction_type", "")).lower().strip()

                # Skip invalid transactions
                if amount < 0:
                    logger.warning(f"Negative transaction amount: {amount}")
                    continue

                if trans_type not in ["credit", "debit"]:
                    logger.warning(f"Invalid transaction type: {trans_type}")
                    continue

                # Credit transactions (money coming in)
                if trans_type == "credit":
                    total_income += amount
                # Debit transactions (money going out)
                elif trans_type == "debit":
                    total_expenses += amount

            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Error processing transaction {t}: {str(e)}")
                continue

        # Calculate net cash flow (industry standard metric)
        net_cash_flow = total_income - total_expenses

        # Get current balance (latest transaction balance) with validation
        current_balance = 0
        try:
            if transactions:
                # Sort by date and get the most recent balance
                sorted_transactions = sorted(
                    [
                        t
                        for t in transactions
                        if "transaction_date" in t and "balance" in t
                    ],
                    key=lambda t: t["transaction_date"],
                    reverse=True,
                )
                if sorted_transactions:
                    current_balance = float(sorted_transactions[0].get("balance", 0))
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"Error calculating current balance: {str(e)}")
            current_balance = net_cash_flow  # Fallback to calculated net cash flow

        # Calculate additional industry standard metrics with validation
        # Average transaction amount (sum of all transaction amounts / count)
        total_transaction_volume = 0
        valid_transactions = 0

        for t in transactions:
            try:
                amount = float(t.get("transaction_amount", 0))
                if amount >= 0:  # Only count valid positive amounts
                    total_transaction_volume += amount
                    valid_transactions += 1
            except (ValueError, TypeError):
                continue

        average_transaction_amount = (
            total_transaction_volume / valid_transactions
            if valid_transactions > 0
            else 0
        )

        # Industry standard financial ratios with proper handling
        if total_income > 0:
            expense_ratio = total_expenses / total_income
            savings_rate = net_cash_flow / total_income
        else:
            expense_ratio = 0 if total_expenses == 0 else float("inf")
            savings_rate = 0

        # Basic metrics (enhanced with industry standards)
        metrics = {
            "total_transactions": total_transactions,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "balance": current_balance,
            "net_cash_flow": net_cash_flow,
            "average_transaction_amount": round(average_transaction_amount, 2),
            "expense_ratio": round(expense_ratio, 4),
            "savings_rate": round(savings_rate, 4),
        }

        # Try to enhance with metrics from Supabase cache
        if user_id:
            try:
                from ..utils.supabase_utils import get_supabase_client

                supabase = get_supabase_client(use_service_role=True)

                # Get metrics from user_metrics_cache or user_financial_metrics
                cache_response = (
                    supabase.table("user_metrics_cache")
                    .select("*")
                    .eq("user_id", user_id)
                    .execute()
                )
                if cache_response.data:
                    cached = cache_response.data[0]
                    metrics.update(
                        {
                            "financial_health_score": cached.get(
                                "financial_health_score", 0
                            ),
                            "dti_score": (
                                cached.get("metrics", {}).get("dtiScore", 0)
                                if isinstance(cached.get("metrics"), dict)
                                else 0
                            ),
                            "savings_score": (
                                cached.get("metrics", {}).get("savingsScore", 0)
                                if isinstance(cached.get("metrics"), dict)
                                else 0
                            ),
                            "cash_flow_score": (
                                cached.get("metrics", {}).get("cashFlowScore", 0)
                                if isinstance(cached.get("metrics"), dict)
                                else 0
                            ),
                            "behavior_score": (
                                cached.get("metrics", {}).get("behaviorScore", 0)
                                if isinstance(cached.get("metrics"), dict)
                                else 0
                            ),
                            "enhanced": True,
                        }
                    )
                else:
                    metrics["enhanced"] = False
            except Exception as e:
                logger.warning(f"Could not enhance metrics: {str(e)}")
                metrics["enhanced"] = False
        else:
            metrics["enhanced"] = False

        set_sentry_context("calculated_metrics", metrics)
        return metrics


@router.post("/upload-statement/")
async def upload_statement(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    with SentryTransaction(name="upload_statement", op="transaction") as transaction:
        # Preserve all original validation and setup
        set_sentry_user(current_user)
        set_sentry_tag("operation", "statement_upload")
        filename = file.filename or "statement.pdf"
        set_sentry_tag("file_type", filename.split(".")[-1].lower())

        if not filename.lower().endswith(".pdf"):
            logger.warning(
                "Invalid file type attempted: %s", filename.split(".")[-1].lower()
            )
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "Only PDF files are supported",
                    "error": "VALIDATION_ERROR",
                },
            )

        max_upload_bytes = max(1, settings.MAX_STATEMENT_UPLOAD_MB) * 1024 * 1024
        temp_file_path = os.path.join(
            tempfile.gettempdir(), f"statement_{uuid.uuid4().hex}.pdf"
        )
        try:
            statement_id, size_bytes = await _stream_upload_and_hash(
                file, filename, max_upload_bytes, temp_file_path
            )
        except UploadSizeExceededError:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            return JSONResponse(
                status_code=413,
                content={
                    "success": False,
                    "message": f"File too large. Maximum size is {settings.MAX_STATEMENT_UPLOAD_MB} MB.",
                    "error": "PAYLOAD_TOO_LARGE",
                },
            )
        finally:
            await file.close()

        if size_bytes == 0:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "Uploaded file is empty",
                    "error": "VALIDATION_ERROR",
                },
            )

        set_sentry_tag("file_size_bytes", size_bytes)

        # Check for existing statement
        existing_transactions = TransactionService.get_transactions_by_statement_id(
            statement_id
        )
        if existing_transactions.data:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            # Schedule enhanced calculation for existing data
            background_tasks.add_task(
                calculate_financial_metrics_background,
                db,
                str(current_user.id),
                statement_id,
            )
            background_tasks.add_task(
                calculate_financial_metrics_background, db, str(current_user.id), None
            )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "Statement already processed",
                    "data": {
                        "transactions": existing_transactions.data,
                        "metrics": calculate_metrics(
                            existing_transactions.data,
                            db,
                            str(current_user.id),
                            statement_id,
                        ),
                    },
                },
            )

        try:
            # Preserve all original processing logic
            with transaction.start_child(
                op="database_check", description="Check existing statement"
            ) as span:
                existing_transactions = (
                    TransactionService.get_transactions_by_statement_id(statement_id)
                )
                if existing_transactions.data:
                    background_tasks.add_task(
                        calculate_financial_metrics_background,
                        db,
                        str(current_user.id),
                        statement_id,
                    )
                    background_tasks.add_task(
                        calculate_financial_metrics_background,
                        db,
                        str(current_user.id),
                        None,
                    )
                    return {
                        "message": "Statement has already been processed",
                        "transactions": existing_transactions.data,
                        "metrics": calculate_metrics(
                            existing_transactions.data,
                            db,
                            str(current_user.id),
                            statement_id,
                        ),
                    }

            with transaction.start_child(
                op="bank_detection", description="Detect bank type"
            ) as span:
                bank_type = detect_bank_from_pdf(temp_file_path)
                set_sentry_tag("bank_type", bank_type)
                # Enforce bank account limits per plan
                BANK_NAME_MAPPING = {
                    "standard_bank": "Standard Bank Namibia",
                    "fnb": "First National Bank Namibia",
                    "nedbank": "Nedbank Namibia",
                    "bank_windhoek": "Bank Windhoek",
                    "unknown": "Unknown Bank",
                }
                bank_full_name = BANK_NAME_MAPPING.get(bank_type, "Unknown Bank")
                await enforce_bank_account_limit(str(current_user.id), bank_full_name)

            # Preserve all original OCR processing
            with transaction.start_child(
                op="statement_processing", description="Process bank statement"
            ) as span:
                if bank_type == "standard_bank":
                    df = extract_standard_bank_account_statement(
                        temp_file_path, BANK_CONFIGS["standard_bank"]
                    )
                elif bank_type == "fnb":
                    df = extract_fnb_account_statement(temp_file_path)
                elif bank_type == "nedbank":
                    df = extract_nedbank_account_statement(temp_file_path)
                elif bank_type == "bank_windhoek":
                    df = extract_bank_windhoek_account_statement(temp_file_path)
                else:
                    raise ValidationError("Unsupported bank statement format")

                if df.empty:
                    raise ValidationError(
                        "No transactions could be extracted from the statement"
                    )

            # Preserve original validation
            required_columns = [
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                logger.error(
                    "Statement processing failed: Missing required columns: %s",
                    ", ".join(missing_columns),
                )
                raise ValidationError(
                    f"Extracted data is missing required columns: {', '.join(missing_columns)}"
                )

            # Preserve original bank mapping
            # Preserve all original transaction processing
            with transaction.start_child(
                op="transaction_processing", description="Process transactions"
            ) as span:
                # Don't classify here - save as "Processing..." and classify in background
                # This allows fast response to user while HF model spins up (60-90s cold start)
                df["category"] = "Processing..."
                df["statement_id"] = statement_id
                df["bank_name"] = BANK_NAME_MAPPING.get(bank_type, "Unknown Bank")

                transactions_data = df.to_dict("records")

                ordered_transactions = []
                for transaction in transactions_data:
                    ordered_transaction = {
                        "id": str(uuid.uuid4()),
                        "statement_id": statement_id,
                        "user_id": str(current_user.id),
                        "bank_name": transaction.get("bank_name"),
                        "transaction_date": transaction.get("transaction_date", ""),
                        "transaction_description": transaction.get(
                            "transaction_description", ""
                        ),
                        "transaction_amount": float(
                            transaction.get("transaction_amount", 0)
                        ),
                        "transaction_type": transaction.get("transaction_type", ""),
                        "balance": float(transaction.get("balance", 0)),
                        "category": transaction.get("category", "Unknown"),
                    }
                    ordered_transactions.append(ordered_transaction)

                result = TransactionService.bulk_create_transactions(
                    ordered_transactions, str(current_user.id)
                )

                # Production fintech tier checking: Upload succeeds first, then check limits
                # This provides better UX with tier-appropriate upgrade messaging
                tier_check_result = await check_tier_limits_after_upload(
                    str(current_user.id), len(ordered_transactions)
                )

                # Schedule background classification (handles HF cold starts gracefully)
                transaction_ids = [t["id"] for t in ordered_transactions]

                logger.info(
                    f"Scheduling background classification for {len(transaction_ids)} transactions",
                    extra={
                        "user_id": str(current_user.id),
                        "statement_id": statement_id,
                        "transaction_count": len(transaction_ids),
                        "sample_transaction_ids": (
                            transaction_ids[:3]
                            if len(transaction_ids) > 3
                            else transaction_ids
                        ),
                        "sample_categories": [
                            t.get("category") for t in ordered_transactions[:3]
                        ],
                    },
                )

                background_tasks.add_task(
                    classify_transactions_background,
                    str(current_user.id),
                    statement_id,
                    transaction_ids,
                )

                # Trigger budget alert check after new data lands
                background_tasks.add_task(
                    budget_alert_service.check_and_notify_for_user_sync,
                    str(current_user.id),
                )

                # Schedule enhanced calculation for new data
                # 1. Recalculate for this specific statement
                background_tasks.add_task(
                    calculate_financial_metrics_background,
                    db,
                    str(current_user.id),
                    statement_id,
                )
                # 2. Recalculate for this specific bank (for bank filter feature)
                bank_full_name = BANK_NAME_MAPPING.get(bank_type, "Unknown Bank")
                background_tasks.add_task(
                    calculate_financial_metrics_background,
                    db,
                    str(current_user.id),
                    None,  # No statement filter
                    bank_full_name,  # Filter by bank
                )
                # 3. Recalculate overall aggregated metrics (all banks)
                background_tasks.add_task(
                    calculate_financial_metrics_background,
                    db,
                    str(current_user.id),
                    None,
                )

                # Send notification for successful statement upload
                total_income = sum(
                    t.get("transaction_amount", 0)
                    for t in ordered_transactions
                    if t.get("transaction_type") == "credit"
                )
                total_expenses = sum(
                    abs(t.get("transaction_amount", 0))
                    for t in ordered_transactions
                    if t.get("transaction_type") == "debit"
                )

                background_tasks.add_task(
                    notification_service.send_notification,
                    user_id=str(current_user.id),
                    notification_type=NotificationType.TRANSACTION_CREATED,
                    data={
                        "amount": f"{len(ordered_transactions)} transactions",
                        "category": (
                            format_bank_name(bank_type)
                            if bank_type
                            else "Bank Statement"
                        ),
                        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                        "description": f"Statement uploaded: {total_income:.2f} income, {total_expenses:.2f} expenses",
                        "statement_id": statement_id,
                        "transaction_count": len(ordered_transactions),
                        "total_income": total_income,
                        "total_expenses": total_expenses,
                    },
                    priority=NotificationPriority.MEDIUM,
                )

                # Build production response with tier information
                response_content = {
                    "success": True,
                    "message": "Statement processed successfully",
                    "data": {
                        "transactions": result.data,
                        "metrics": calculate_metrics(
                            result.data, db, str(current_user.id), statement_id
                        ),
                    },
                }

                # Add tier information for upgrade messaging
                if tier_check_result.get("at_limit") or tier_check_result.get(
                    "over_limit"
                ):
                    response_content["tier_info"] = {
                        "current_plan": tier_check_result["current_plan"],
                        "current_count": tier_check_result["current_count"],
                        "limit": tier_check_result["limit"],
                        "at_limit": tier_check_result.get("at_limit", False),
                        "over_limit": tier_check_result.get("over_limit", False),
                        "upgrade_message": tier_check_result.get("upgrade_message"),
                        "upgrade_url": "/pricing",
                        "suggested_plan": tier_check_result.get("suggested_plan"),
                    }

                return JSONResponse(status_code=200, content=response_content)

        except Exception as e:
            # Preserve original error handling
            set_sentry_context(
                "error_details",
                {
                    "filename": file.filename,
                    "bank_type": bank_type if "bank_type" in locals() else "unknown",
                    "error_type": e.__class__.__name__,
                },
            )
            logger.error(
                "Statement processing failed: %s", e.__class__.__name__, exc_info=True
            )
            sentry_sdk.capture_exception(e)
            if isinstance(e, ValidationError):
                raise
            raise StatementProcessingError(str(e))
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)


@router.get("/transactions/")
async def read_transactions(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    statement_id: Optional[str] = None,
    sort_by: str = "transaction_date",
    sort_order: str = "desc",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enhanced transactions endpoint with tier-based filtering and usage metadata."""
    try:
        user_id = str(current_user.id)

        # Determine plan limits
        subscription = await get_user_subscription(user_id)
        plan = subscription.get("plan", "free")
        plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        tx_limit = plan_limits["max_transactions_per_month"]
        tx_limit_int = 999999 if tx_limit == float("inf") else int(tx_limit)

        # Count total transactions that match the filters to align visibility with plan limits
        supabase = get_supabase_client(use_service_role=True)
        count_query = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("id", count="exact")
            .eq("user_id", user_id)
        )
        if statement_id:
            count_query = count_query.eq("statement_id", statement_id)
        if date_from:
            count_query = count_query.gte("transaction_date", date_from)
        if date_to:
            count_query = count_query.lte("transaction_date", date_to)

        count_response = count_query.execute()
        total_transactions = count_response.count if count_response.count else 0

        visible_total = (
            total_transactions
            if tx_limit_int == 999999
            else min(total_transactions, tx_limit_int)
        )
        hidden_transactions = (
            0 if tx_limit_int == 999999 else max(0, total_transactions - tx_limit_int)
        )

        # Short-circuit when the request is beyond the visible window
        remaining_quota = max(0, visible_total - skip)
        if remaining_quota == 0:
            statements = TransactionService.get_statement_summary(user_id)
            metadata = {
                "total_records": visible_total,
                "current_page": (skip // limit) + 1 if limit > 0 else 1,
                "total_pages": (visible_total + limit - 1) // limit if limit > 0 else 1,
                "has_next": False,
                "has_previous": skip > 0,
                "statements": statements,
                "hidden_transactions": hidden_transactions,
                "visible_limit": None if tx_limit_int == 999999 else tx_limit_int,
            }
            usage = {
                "plan": plan,
                "transactions_used": visible_total,
                "transactions_limit": tx_limit_int,
                "transactions_remaining": 0,
                "transactions_percent": (0.0 if tx_limit_int in [0, 999999] else 100.0),
                "is_near_limit": tx_limit_int != 999999,
                "is_at_limit": tx_limit_int != 999999,
                "visible_transactions": 0,
                "hidden_transactions": hidden_transactions,
                "upgrade_message": (
                    f"You've reached your {plan.title()} plan limit of {tx_limit_int} transactions. "
                    "Upgrade to see your full history and upload new statements."
                    if tx_limit_int != 999999
                    else None
                ),
            }
            return {
                "success": True,
                "data": {
                    "transactions": [],
                    "metadata": metadata,
                    "usage": usage,
                },
            }

        # Clip the requested page to the remaining visible quota
        effective_limit = (
            limit if tx_limit_int == 999999 else min(limit, remaining_quota)
        )

        # Fetch transactions
        result = TransactionService.get_transactions_with_metadata(
            user_id=user_id,
            skip=skip,
            limit=effective_limit,
            statement_id=statement_id,
            sort_by=sort_by,
            sort_order=sort_order,
            date_from=date_from,
            date_to=date_to,
        )

        transactions = result.get("transactions", [])
        statements = result.get("metadata", {}).get("statements", [])

        # Calculate limit-related values
        visible_used = visible_total
        transactions_remaining = (
            999999 if tx_limit_int == 999999 else max(0, tx_limit_int - visible_used)
        )
        transactions_percent = (
            0.0 if tx_limit_int in [0, 999999] else (visible_used / tx_limit_int) * 100
        )

        metadata = {
            "total_records": visible_total,
            "current_page": (skip // limit) + 1 if limit > 0 else 1,
            "total_pages": (visible_total + limit - 1) // limit if limit > 0 else 1,
            "has_next": (skip + effective_limit) < visible_total,
            "has_previous": skip > 0,
            "statements": statements,
            "hidden_transactions": hidden_transactions,
            "visible_limit": None if tx_limit_int == 999999 else tx_limit_int,
        }

        usage = {
            "plan": plan,
            "transactions_used": visible_used,
            "transactions_limit": tx_limit_int,
            "transactions_remaining": transactions_remaining,
            "transactions_percent": round(transactions_percent, 1),
            "is_near_limit": transactions_percent >= 80 and tx_limit_int != 999999,
            "is_at_limit": (tx_limit_int != 999999 and visible_used >= tx_limit_int),
            "visible_transactions": len(transactions),
            "hidden_transactions": hidden_transactions,
            "upgrade_message": None,
        }

        if usage["is_at_limit"]:
            usage["upgrade_message"] = (
                f"You've reached your {plan.title()} plan limit of {tx_limit_int} transactions. "
                "Upgrade to unlock unlimited history."
            )
        elif usage["is_near_limit"]:
            usage["upgrade_message"] = (
                f"You're approaching your plan limit ({visible_used}/{tx_limit_int}). "
                "Upgrade to keep importing statements without interruption."
            )

        # Enhance with backend calculations while preserving original structure
        if transactions:
            result["metrics"] = calculate_metrics(
                transactions, db, user_id, statement_id
            )

        result["transactions"] = transactions
        result["metadata"] = metadata
        result["usage"] = usage

        return {"success": True, "data": result}
    except Exception as e:
        logger.error(
            "Failed to fetch transactions: %s", e.__class__.__name__, exc_info=True
        )
        return {
            "success": False,
            "message": "Failed to fetch transactions",
            "error": "DATABASE_ERROR",
        }


@router.get("/financial-metrics/")
async def get_financial_metrics(
    request: Request,
    statement_id: Optional[str] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """New comprehensive endpoint for enhanced financial data"""
    with SentryTransaction(name="get_financial_metrics", op="metrics"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "get_financial_metrics")

        try:
            user_id = str(current_user.id)

            # Enforce plan-based visibility for metrics (aligns with visible transactions)
            subscription = await get_user_subscription(user_id)
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits["max_transactions_per_month"]
            tx_limit_int = 999999 if tx_limit == float("inf") else int(tx_limit)

            # Get all enhanced financial data from Supabase (single source of truth)
            from ..utils.supabase_utils import get_supabase_client

            supabase = get_supabase_client(use_service_role=True)

            # Get financial metrics from user_financial_metrics table
            metrics_response = (
                supabase.table("user_financial_metrics")
                .select("*")
                .eq("user_id", user_id)
                .is_("statement_id", "null")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            financial_metrics = (
                metrics_response.data[0] if metrics_response.data else None
            )

            # If the plan is limited, recompute metrics from the visible transaction cap
            if tx_limit_int != 999999:
                count_response = (
                    supabase.table(settings.SUPABASE_TABLE_NAME)
                    .select("id", count="exact")
                    .eq("user_id", user_id)
                    .execute()
                )
                total_transactions = count_response.count if count_response.count else 0
                visible_cap = min(total_transactions, tx_limit_int)

                visible_transactions = TransactionService.get_all_user_transactions(
                    user_id, max_records=visible_cap
                )
                metrics_from_visible = calculate_metrics(
                    visible_transactions, db, user_id, None
                )
                metrics_from_visible["hidden_transactions"] = max(
                    0, total_transactions - visible_cap
                )
                metrics_from_visible["visible_limit"] = tx_limit_int
                financial_metrics = metrics_from_visible

            # Get cash flow data
            cash_flow_response = (
                supabase.table("user_cash_flow_data")
                .select("*")
                .eq("user_id", user_id)
                .order("date", desc=False)
                .execute()
            )
            cash_flow_data = cash_flow_response.data if cash_flow_response.data else []

            # Get subscriptions
            subscriptions_response = (
                supabase.table("user_subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )
            subscriptions = (
                subscriptions_response.data if subscriptions_response.data else []
            )

            # Get category spending
            category_response = (
                supabase.table("user_category_spending")
                .select("*")
                .eq("user_id", user_id)
                .order("total_amount", desc=True)
                .execute()
            )
            category_spending = category_response.data if category_response.data else []

            # Get monthly trends
            trends_response = (
                supabase.table("user_monthly_trends")
                .select("*")
                .eq("user_id", user_id)
                .order("month", desc=True)
                .limit(1)
                .execute()
            )
            monthly_trends = trends_response.data[0] if trends_response.data else None

            # If no metrics, calculate on demand using Supabase service
            if not financial_metrics:
                try:
                    user_metrics_service = UserMetricsSupabaseService()
                    user_metrics_service.calculate_comprehensive_user_metrics(
                        user_id, force_recalculate=True, statement_id=None
                    )

                    # Fetch the newly calculated metrics
                    metrics_response = (
                        supabase.table("user_financial_metrics")
                        .select("*")
                        .eq("user_id", user_id)
                        .is_("statement_id", "null")
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    financial_metrics = (
                        metrics_response.data[0] if metrics_response.data else None
                    )

                    if financial_metrics:
                        return JSONResponse(
                            status_code=200,
                            content={
                                "success": True,
                                "data": {"overall_metrics": financial_metrics},
                                "calculated_on_demand": True,
                            },
                        )
                except Exception as calc_error:
                    logger.warning(f"On-demand calculation failed: {str(calc_error)}")

                return JSONResponse(
                    status_code=200,
                    content={
                        "success": False,
                        "message": "No financial metrics available",
                        "error": "CALCULATION_ERROR",
                    },
                )

            # Get comprehensive gamification data from achievement service
            gamification_data = {}
            try:
                gamification_data, savings_streak = await run_in_threadpool(
                    _get_financial_gamification, user_id
                )

                # Add savings streak to financial metrics for backward compatibility
                if financial_metrics and isinstance(financial_metrics, dict):
                    financial_metrics["savings_streak"] = savings_streak
                elif financial_metrics and hasattr(financial_metrics, "to_dict"):
                    financial_dict = financial_metrics.to_dict()
                    financial_dict["savings_streak"] = savings_streak
                    financial_metrics = financial_dict

            except Exception as gamification_error:
                logger.warning(
                    f"Failed to get gamification data: {str(gamification_error)}"
                )
                # Set default values if gamification fetch fails
                gamification_data = {
                    "streaks": [],
                    "achievements": [],
                    "game_stats": {"points": {"points": 0, "level": 1}},
                    "active_challenges": [],
                }
                if financial_metrics and isinstance(financial_metrics, dict):
                    financial_metrics["savings_streak"] = 0
                elif financial_metrics and hasattr(financial_metrics, "to_dict"):
                    financial_dict = financial_metrics.to_dict()
                    financial_dict["savings_streak"] = 0
                    financial_metrics = financial_dict

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "data": {
                        "financial_metrics": financial_metrics,
                        "cash_flow_data": cash_flow_data,
                        "subscriptions": subscriptions,
                        "category_spending": category_spending,
                        "monthly_trends": monthly_trends,
                        "gamification": gamification_data,
                    },
                },
            )

        except Exception as e:
            logger.error(
                "Failed to get financial metrics: %s",
                e.__class__.__name__,
                exc_info=True,
            )
            sentry_sdk.capture_exception(e)
            raise DatabaseError("Failed to retrieve financial metrics")


@router.post("/recalculate-metrics/")
async def recalculate_metrics(
    request: Request,
    background_tasks: BackgroundTasks,
    statement_id: Optional[str] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger manual recalculation of enhanced metrics"""
    with SentryTransaction(name="recalculate_metrics", op="calculation"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "recalculate_metrics")

        try:
            user_id = str(current_user.id)
            background_tasks.add_task(
                calculate_financial_metrics_background, db, user_id, statement_id
            )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "Enhanced financial metrics recalculation scheduled",
                    "user_id": user_id,
                    "statement_id": statement_id,
                },
            )

        except Exception as e:
            logger.error(
                "Failed to schedule recalculation: %s",
                e.__class__.__name__,
                exc_info=True,
            )
            sentry_sdk.capture_exception(e)
            raise DatabaseError("Failed to schedule metrics recalculation")


@router.get("/statements/")
async def get_statements(request: Request, current_user=Depends(get_current_user)):
    """Preserve original statements endpoint"""
    with SentryTransaction(name="get_statements", op="database"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "get_statements")
        try:
            return TransactionService.get_statement_summary(str(current_user.id))
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise DatabaseError("Failed to fetch statement summary")


@router.get("/transactions/{transaction_id}")
async def get_transaction_detail(
    transaction_id: str,
    current_user=Depends(get_current_user),
):
    """Return one transaction owned by the authenticated user."""
    try:
        response = (
            get_supabase_client(use_service_role=True)
            .table(settings.SUPABASE_TABLE_NAME)
            .select("*")
            .eq("id", transaction_id)
            .eq("user_id", str(current_user.id))
            .limit(1)
            .execute()
        )
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found",
            )
        return {
            "success": True,
            "transaction": response.data[0],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to fetch transaction detail",
            extra={
                "transaction_id": transaction_id,
                "user_id": str(current_user.id),
                "error": str(exc),
            },
        )
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch transaction",
        )


@router.put("/transactions/{transaction_id}")
async def update_transaction(
    request: Request,
    transaction_id: str,
    transaction_data: dict,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enhanced update that triggers metric recalculation"""
    with SentryTransaction(name="update_transaction", op="database"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "update_transaction")

        try:
            # Preserve original validation logic
            transaction = await run_in_threadpool(
                TransactionService.get_transaction_for_user,
                str(current_user.id), transaction_id
            )
            if not transaction.data:
                logger.info(
                    "Update attempted on non-existent transaction: %s", transaction_id
                )
                raise NotFoundError("Transaction not found", transaction_id)

            # Get statement_id from the transaction before updating
            transaction_statement_id = (
                transaction.data[0].get("statement_id") if transaction.data else None
            )

            updated_transaction = await run_in_threadpool(
                TransactionService.update_transaction,
                transaction_id, transaction_data, user_id=str(current_user.id)
            )
            if updated_transaction is None or not updated_transaction.data:
                raise NotFoundError("Transaction not found", transaction_id)

            # Schedule enhanced recalculation for both per-statement and aggregated metrics
            if transaction_statement_id:
                background_tasks.add_task(
                    calculate_financial_metrics_background,
                    db,
                    str(current_user.id),
                    transaction_statement_id,
                )
            background_tasks.add_task(
                calculate_financial_metrics_background, db, str(current_user.id), None
            )

            # Preserve original response format
            return updated_transaction

        except (NotFoundError, ValidationError, HTTPException):
            raise
        except Exception as e:
            logger.error(
                "Failed to update transaction %s: %s",
                transaction_id,
                e.__class__.__name__,
                exc_info=True,
            )
            sentry_sdk.capture_exception(e)
            raise DatabaseError("Failed to update transaction")


@router.delete("/transactions/{transaction_id}")
async def delete_transaction(
    request: Request,
    transaction_id: str,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enhanced delete that triggers metric recalculation"""
    with SentryTransaction(name="delete_transaction", op="database"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "delete_transaction")

        try:
            # Preserve original validation logic
            transaction = await run_in_threadpool(
                TransactionService.get_transaction_for_user,
                str(current_user.id), transaction_id
            )
            if not transaction.data:
                raise NotFoundError("Transaction not found", transaction_id)

            # Get statement_id from the transaction before deleting
            transaction_statement_id = (
                transaction.data[0].get("statement_id") if transaction.data else None
            )

            result = await run_in_threadpool(
                TransactionService.delete_transaction,
                transaction_id, user_id=str(current_user.id)
            )
            if result is None or not result.data:
                raise NotFoundError("Transaction not found", transaction_id)

            # Schedule enhanced recalculation for both per-statement and aggregated metrics
            if transaction_statement_id:
                background_tasks.add_task(
                    calculate_financial_metrics_background,
                    db,
                    str(current_user.id),
                    transaction_statement_id,
                )
            background_tasks.add_task(
                calculate_financial_metrics_background, db, str(current_user.id), None
            )

            # Preserve original response format
            return {"message": "Transaction deleted successfully"}

        except (NotFoundError, HTTPException):
            raise
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise DatabaseError("Failed to delete transaction")


@router.get("/all-transactions/")
async def get_all_transactions(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get ALL transactions for the user without pagination"""
    try:
        user_id = str(current_user.id)

        # Determine plan limits and total counts
        subscription = await get_user_subscription(user_id)
        plan = subscription.get("plan", "free")
        plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        tx_limit = plan_limits["max_transactions_per_month"]
        tx_limit_int = 999999 if tx_limit == float("inf") else int(tx_limit)

        supabase = get_supabase_client(use_service_role=True)
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
        hidden_transactions = (
            0 if tx_limit_int == 999999 else max(0, total_transactions - tx_limit_int)
        )

        # Get transactions up to the visible cap from Supabase
        all_transactions = TransactionService.get_all_user_transactions(
            user_id, max_records=visible_cap
        )

        # Calculate metrics for all data
        if all_transactions:
            metrics = calculate_metrics(all_transactions, db, user_id)
        else:
            metrics = {
                "total_transactions": 0,
                "total_income": 0,
                "total_expenses": 0,
                "balance": 0,
                "enhanced": False,
            }

        # Get all statements
        statements = TransactionService.get_statement_summary(user_id)

        usage = {
            "plan": plan,
            "transactions_used": visible_cap,
            "transactions_limit": tx_limit_int,
            "transactions_remaining": (
                999999 if tx_limit_int == 999999 else max(0, tx_limit_int - visible_cap)
            ),
            "transactions_percent": (
                0.0
                if tx_limit_int in [0, 999999]
                else round((visible_cap / tx_limit_int) * 100, 1)
            ),
            "is_near_limit": tx_limit_int != 999999
            and tx_limit_int > 0
            and (visible_cap / tx_limit_int) * 100 >= 80,
            "is_at_limit": tx_limit_int != 999999 and visible_cap >= tx_limit_int,
            "visible_transactions": len(all_transactions),
            "hidden_transactions": hidden_transactions,
            "upgrade_message": None,
        }

        if usage["is_at_limit"]:
            usage["upgrade_message"] = (
                f"You've reached your {plan.title()} plan limit of {tx_limit_int} transactions. "
                "Upgrade to view your full history and upload additional statements."
            )
        elif usage["is_near_limit"]:
            usage["upgrade_message"] = (
                f"You're approaching your plan limit ({visible_cap}/{tx_limit_int}). "
                "Upgrade to keep importing statements without interruptions."
            )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": {
                    "transactions": all_transactions,
                    "metrics": metrics,
                    "metadata": {
                        "total_records": visible_cap,
                        "statements": statements,
                        "hidden_transactions": hidden_transactions,
                        "visible_limit": (
                            None if tx_limit_int == 999999 else tx_limit_int
                        ),
                    },
                    "usage": usage,
                },
            },
        )
    except Exception as e:
        logger.error(
            "Failed to fetch all transactions: %s", e.__class__.__name__, exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Failed to fetch all transactions",
                "error": str(e),
            },
        )


# Removed /metrics endpoint - now handled by user_metrics_routes.py
# This avoids route conflicts and uses the comprehensive metrics service


@router.post("/upload-statement/check")
async def check_existing_statement(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Preserve original statement check functionality"""
    filename = file.filename or "statement.pdf"
    max_upload_bytes = max(1, settings.MAX_STATEMENT_UPLOAD_MB) * 1024 * 1024
    try:
        statement_id, size_bytes = await _stream_upload_and_hash(
            file, filename, max_upload_bytes
        )
    except UploadSizeExceededError:
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "message": f"File too large. Maximum size is {settings.MAX_STATEMENT_UPLOAD_MB} MB.",
                "error": "PAYLOAD_TOO_LARGE",
            },
        )
    finally:
        await file.close()

    if size_bytes == 0:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Uploaded file is empty",
                "error": "VALIDATION_ERROR",
            },
        )

    existing_transactions = TransactionService.get_transactions_by_statement_id(
        statement_id
    )

    if existing_transactions.data:
        return {"transactions": existing_transactions.data}

    return {"transactions": None}


@router.get("/all-financial-metrics/")
async def get_all_financial_metrics(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all financial metrics for the user (all statements and overall)."""
    user_id = str(current_user.id)

    # Get all financial metrics from Supabase
    from ..utils.supabase_utils import get_supabase_client

    supabase = get_supabase_client(use_service_role=True)
    metrics_response = (
        supabase.table("user_financial_metrics")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    metrics = metrics_response.data if metrics_response.data else []

    return JSONResponse(status_code=200, content={"success": True, "data": metrics})


@router.get("/user/initialize/")
async def initialize_user_data(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Initialize user data on login - loads all transactions, metrics, and statements"""
    with SentryTransaction(name="initialize_user_data", op="initialization"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "user_initialization")

        try:
            user_id = str(current_user.id)
            logger.info(f"Initializing data for user: {user_id}")

            # Determine plan limits and cap the visible dataset accordingly
            subscription = await get_user_subscription(user_id)
            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits["max_transactions_per_month"]
            tx_limit_int = 999999 if tx_limit == float("inf") else int(tx_limit)

            supabase = get_supabase_client(use_service_role=True)
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
            hidden_transactions = (
                0
                if tx_limit_int == 999999
                else max(0, total_transactions - tx_limit_int)
            )

            # Get all user transactions (main data) up to the visible cap
            all_transactions = TransactionService.get_all_user_transactions(
                user_id, max_records=visible_cap
            )
            logger.info(
                f"Loaded {len(all_transactions)} transactions for user {user_id}"
            )

            # Calculate basic metrics for all data
            if all_transactions:
                try:
                    metrics = calculate_metrics(all_transactions, db, user_id)
                except Exception as e:
                    logger.warning(
                        f"Could not calculate metrics for user {user_id}: {str(e)}"
                    )
                    # Fallback to basic metrics calculation
                    total_income = sum(
                        t["transaction_amount"]
                        for t in all_transactions
                        if t["transaction_type"].lower() == "credit"
                    )
                    total_expenses = sum(
                        t["transaction_amount"]
                        for t in all_transactions
                        if t["transaction_type"].lower() == "debit"
                    )
                    metrics = {
                        "total_transactions": len(all_transactions),
                        "total_income": total_income,
                        "total_expenses": total_expenses,
                        "balance": total_income - total_expenses,
                        "enhanced": False,
                    }
            else:
                metrics = {
                    "total_transactions": 0,
                    "total_income": 0,
                    "total_expenses": 0,
                    "balance": 0,
                    "enhanced": False,
                }

            # Get statements summary (with error handling)
            statements = []
            try:
                statements = TransactionService.get_statement_summary(user_id)
            except Exception as e:
                logger.warning(
                    f"Could not get statement summary for user {user_id}: {str(e)}"
                )

            # Try to get enhanced financial metrics from Supabase (optional)
            try:
                enhanced_response = (
                    supabase.table("user_financial_metrics")
                    .select("*")
                    .eq("user_id", user_id)
                    .is_("statement_id", "null")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if enhanced_response.data:
                    enhanced_metrics = enhanced_response.data[0]
                    metrics.update(enhanced_metrics)
                    metrics["enhanced"] = True
            except Exception as e:
                logger.warning(
                    f"Could not load enhanced metrics for user {user_id}: {str(e)}"
                )

            logger.info(
                f"Successfully initialized user {user_id} with {len(all_transactions)} transactions"
            )

            usage = {
                "plan": plan,
                "transactions_used": visible_cap,
                "transactions_limit": tx_limit_int,
                "transactions_remaining": (
                    999999
                    if tx_limit_int == 999999
                    else max(0, tx_limit_int - visible_cap)
                ),
                "transactions_percent": (
                    0.0
                    if tx_limit_int in [0, 999999]
                    else round((visible_cap / tx_limit_int) * 100, 1)
                ),
                "is_near_limit": tx_limit_int != 999999
                and tx_limit_int > 0
                and (visible_cap / tx_limit_int) * 100 >= 80,
                "is_at_limit": tx_limit_int != 999999 and visible_cap >= tx_limit_int,
                "visible_transactions": len(all_transactions),
                "hidden_transactions": hidden_transactions,
                "upgrade_message": None,
            }

            if usage["is_at_limit"]:
                usage["upgrade_message"] = (
                    f"You've reached your {plan.title()} plan limit of {tx_limit_int} transactions. "
                    "Upgrade to view your full history and upload additional statements."
                )
            elif usage["is_near_limit"]:
                usage["upgrade_message"] = (
                    f"You're approaching your plan limit ({visible_cap}/{tx_limit_int}). "
                    "Upgrade to keep importing statements without interruptions."
                )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "data": {
                        "transactions": all_transactions,
                        "metrics": metrics,
                        "statements": statements,
                        "metadata": {
                            "total_records": visible_cap,
                            "initialization_time": str(datetime.datetime.now()),
                            "has_data": len(all_transactions) > 0,
                            "hidden_transactions": hidden_transactions,
                            "visible_limit": (
                                None if tx_limit_int == 999999 else tx_limit_int
                            ),
                        },
                        "usage": usage,
                    },
                    "message": f"User data initialized successfully with {len(all_transactions)} transactions",
                },
            )

        except Exception as e:
            logger.error(
                f"Failed to initialize user data: {e.__class__.__name__}: {str(e)}",
                exc_info=True,
            )
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "message": "Failed to initialize user data",
                    "error": str(e),
                },
            )


@router.post("/retry-classification/")
async def retry_failed_classifications(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """
    Retry classification for all transactions that failed or are still processing.
    This endpoint allows users to manually retry classification for:
    - Transactions stuck as "Processing..."
    - Transactions marked as "Classification Failed"
    - Transactions marked as "Uncategorized"
    """
    with SentryTransaction(name="retry_failed_classifications", op="classification"):
        try:
            user_id = current_user["id"]
            set_sentry_user(current_user)
            set_sentry_tag("retry_classification", "true")

            logger.info(f"Retrying failed classifications for user {user_id}")

            from ..config.settings import settings
            from ..utils.supabase_utils import get_supabase_client
            from ..utils.transaction_model_endpoint import transaction_model_status

            # Without a working classifier, resetting rows to "Processing..."
            # only cycles them through the failure path — refuse instead.
            if not transaction_model_status()["ready"]:
                logger.warning(
                    "Retry classification refused: no classification backend available"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "success": False,
                        "message": "Classification service is currently unavailable. Please try again later.",
                        "retry_count": 0,
                    },
                )

            # Get all transactions that need retry
            supabase = get_supabase_client(use_service_role=True)
            response = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("id, statement_id, category")
                .eq("user_id", user_id)
                .in_(
                    "category",
                    ["Processing...", "Classification Failed", "Uncategorized"],
                )
                .execute()
            )

            if not response.data:
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "message": "No transactions need re-classification",
                        "retry_count": 0,
                    },
                )

            # Group by statement_id
            statements_to_retry = {}
            for transaction in response.data:
                statement_id = transaction["statement_id"]
                if statement_id not in statements_to_retry:
                    statements_to_retry[statement_id] = []
                statements_to_retry[statement_id].append(transaction["id"])

            # Reset transactions to "Processing..." before retry
            transaction_ids = [t["id"] for t in response.data]
            supabase.table(settings.SUPABASE_TABLE_NAME).update(
                {"category": "Processing..."}
            ).in_("id", transaction_ids).execute()

            # Schedule background classification for each statement
            for statement_id, ids in statements_to_retry.items():
                background_tasks.add_task(
                    classify_transactions_background,
                    user_id,
                    statement_id,
                    ids,
                )

            total_transactions = len(transaction_ids)
            logger.info(
                f"Scheduled retry for {total_transactions} transactions across {len(statements_to_retry)} statements"
            )

            return JSONResponse(
                status_code=202,  # Accepted
                content={
                    "success": True,
                    "message": f"Retry classification started for {total_transactions} transactions",
                    "retry_count": total_transactions,
                    "statements_affected": len(statements_to_retry),
                },
            )

        except Exception as e:
            logger.error(f"Failed to retry classifications: {str(e)}", exc_info=True)
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "message": "Failed to start retry classification",
                    "error": str(e),
                },
            )


# =============================================================================
# Secure Transaction Editing Endpoints (Migration 023)
# Revolut-style editing with full audit trail
# =============================================================================

from ..schemas.transaction import TransactionUpdate


@router.patch("/transactions/{transaction_id}/edit", response_model=dict)
async def edit_transaction_secure(
    request: Request,
    transaction_id: str,
    update_data: TransactionUpdate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Securely edit transaction category and/or merchant_name with full audit logging.

    Following Revolut's best practices:
    - Only category and merchant_name can be edited (not amounts/dates for security)
    - Full audit trail with IP and user agent
    - Original values preserved
    - Edit history available for compliance
    """
    with SentryTransaction(name="edit_transaction_secure", op="database"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "edit_transaction")

        try:
            # Get transaction using Supabase (consistent with existing patterns)
            supabase = get_supabase_client()
            # Use service role for audit logging (bypasses RLS for system operations)
            supabase_admin = get_supabase_client(use_service_role=True)

            response = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("*")
                .eq("id", transaction_id)
                .eq("user_id", str(current_user.id))
                .execute()
            )

            if not response.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Transaction not found or you don't have permission to edit it",
                )

            transaction = response.data[0]
            changes_made = []

            # Get IP address and user agent for security audit
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")

            update_payload = {}

            # Update category if provided
            if update_data.category is not None:
                old_category = transaction.get("category")
                update_payload["category"] = update_data.category

                # Store original category if this is the first edit
                if transaction.get("original_category") is None:
                    update_payload["original_category"] = old_category

                # Log to edit history (use admin client to bypass RLS)
                supabase_admin.table("transaction_edit_history").insert(
                    {
                        "transaction_id": transaction_id,
                        "user_id": str(current_user.id),
                        "field_name": "category",
                        "old_value": old_category,
                        "new_value": update_data.category,
                        "ip_address": str(ip_address) if ip_address else None,
                        "user_agent": user_agent,
                    }
                ).execute()

                changes_made.append("category")

            # Update merchant_name if provided
            if update_data.merchant_name is not None:
                old_merchant = transaction.get("merchant_name")
                update_payload["merchant_name"] = update_data.merchant_name

                # Store original merchant name if this is the first edit
                if transaction.get("original_merchant_name") is None:
                    update_payload["original_merchant_name"] = old_merchant

                # Log to edit history (use admin client to bypass RLS)
                supabase_admin.table("transaction_edit_history").insert(
                    {
                        "transaction_id": transaction_id,
                        "user_id": str(current_user.id),
                        "field_name": "merchant_name",
                        "old_value": old_merchant,
                        "new_value": update_data.merchant_name,
                        "ip_address": str(ip_address) if ip_address else None,
                        "user_agent": user_agent,
                    }
                ).execute()

                changes_made.append("merchant_name")

            if not changes_made:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No valid fields provided for update",
                )

            # Update transaction (trigger will auto-update edit_count and last_edited_at)
            updated = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .update(update_payload)
                .eq("id", transaction_id)
                .execute()
            )

            logger.info(
                f"Transaction {transaction_id} edited by user {current_user.id}. "
                f"Fields changed: {', '.join(changes_made)}"
            )

            # Send notification about transaction update
            try:
                updated_transaction = updated.data[0] if updated.data else transaction
                await notification_service.send_notification(
                    user_id=str(current_user.id),
                    notification_type=NotificationType.TRANSACTION_UPDATED,
                    data={
                        "transaction_id": transaction_id,
                        "description": (
                            f"You updated {', '.join(changes_made)} for "
                            f"{updated_transaction.get('merchant_name') or updated_transaction.get('transaction_description') or 'a transaction'}."
                        ),
                        "changes": changes_made,
                    },
                    priority=NotificationPriority.LOW,
                )
            except Exception as notif_error:
                logger.warning(f"Failed to send notification: {notif_error}")

            # Re-check budget alignment after edits that affect categories
            try:
                await budget_alert_service.check_and_notify_for_user(
                    str(current_user.id)
                )
            except Exception as budget_error:
                logger.warning(f"Failed to check budget alerts: {budget_error}")

            return {
                "success": True,
                "message": "Transaction updated successfully",
                "fields_updated": changes_made,
                "transaction": updated.data[0] if updated.data else None,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to edit transaction {transaction_id}: {str(e)}", exc_info=True
            )
            sentry_sdk.capture_exception(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update transaction",
            )


@router.get("/transactions/{transaction_id}/edit-history", response_model=list)
async def get_transaction_edit_history(
    transaction_id: str,
    current_user=Depends(get_current_user),
):
    """
    Get the edit history for a transaction (Revolut-style).

    Returns all edits made to the transaction's category and merchant_name,
    ordered by most recent first.
    """
    with SentryTransaction(name="get_edit_history", op="database"):
        set_sentry_user(current_user)
        set_sentry_tag("operation", "get_edit_history")

        try:
            # Use regular client for transaction verification
            supabase = get_supabase_client()
            # Use service role for reading edit history (bypasses RLS)
            supabase_admin = get_supabase_client(use_service_role=True)

            # Verify user owns this transaction
            trans_response = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("id")
                .eq("id", transaction_id)
                .eq("user_id", str(current_user.id))
                .execute()
            )

            if not trans_response.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Transaction not found or you don't have permission to view it",
                )

            # Get edit history using service role (bypasses RLS)
            history_response = (
                supabase_admin.table("transaction_edit_history")
                .select("id, field_name, old_value, new_value, edited_at")
                .eq("transaction_id", transaction_id)
                .eq("user_id", str(current_user.id))
                .order("edited_at", desc=True)
                .execute()
            )

            return history_response.data if history_response.data else []

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch edit history for transaction {transaction_id}: {str(e)}",
                exc_info=True,
            )
            sentry_sdk.capture_exception(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch edit history",
            )
