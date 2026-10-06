# api/app/routes/achievement_routes.py

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config.settings import settings
from ..schemas.achievement import (
    AchievementCreate,
    AchievementResponse,
    AchievementUpdate,
    ActivitySnapshotResponse,
    ChallengeProgressRequest,
    DailyChallengeCreate,
    UserAchievementProgressRequest,
    UserAchievementResponse,
    UserChallengeResponse,
    UserGameStatsResponse,
    UserStreakResponse,
)
from ..schemas.response import SuccessResponse
from ..sentry import SentryTransaction, set_sentry_context, set_sentry_user
from ..services.achievements import achievement_service
from ..services.achievements.activity_calculations import (
    calculate_activity_points_from_components,
    calculate_level_snapshot,
    finite_float,
    health_status,
    next_streak_milestone,
    savings_rate_percentage,
    streak_milestone_progress,
)
from ..services.user_metrics.metrics_service import UserMetricsSupabaseService
from ..services.user_metrics.utils import get_default_metrics
from ..utils.auth import get_current_user
from ..utils.db import get_db, run_db_task
from ..utils.logging_config import get_logger
from ..utils.subscription import PLAN_LIMITS, get_user_subscription
from ..utils.upstream import UpstreamUnavailable

router = APIRouter(tags=["achievements"])  # Prefix added in main.py
logger = get_logger(__name__)
user_metrics_service = UserMetricsSupabaseService()


def _read_field(source: Any, field: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(field, default)
    return getattr(source, field, default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0) -> float:
    return finite_float(value, default)


def _savings_rate_percentage(metrics: Dict[str, Any]) -> float:
    return savings_rate_percentage(metrics)


def _health_status(financial_score: int) -> str:
    return health_status(financial_score)


def _serialise_streaks(streaks: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    serialised: Dict[str, Dict[str, Any]] = {}
    for streak_type, streak in (streaks or {}).items():
        serialised[streak_type] = {
            "current_count": _to_int(_read_field(streak, "current_count", 0)),
            "highest_count": _to_int(_read_field(streak, "highest_count", 0)),
            "last_tracked_date": _read_field(streak, "last_tracked_date"),
        }
    return serialised


def _activity_level_snapshot(total_points: int) -> Dict[str, Any]:
    return calculate_level_snapshot(total_points)


def _next_streak_milestone(current_streak: int) -> int:
    return next_streak_milestone(current_streak)


# Achievement endpoints
@router.get("/", response_model=SuccessResponse[List[AchievementResponse]])
async def get_achievements(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get all achievements or filter by category"""
    if category:
        achievements = await run_in_threadpool(
            achievement_service.get_achievements_by_category, db, category
        )
    else:
        achievements = await run_in_threadpool(
            achievement_service.get_achievements, db, skip, limit
        )
    return SuccessResponse(success=True, data=achievements)


@router.get(
    "/user/achievements",
    response_model=SuccessResponse[List[UserAchievementResponse]],
)
async def get_user_achievements(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Get all achievements for the current user"""
    user_achievements = await run_in_threadpool(
        achievement_service.get_user_achievements, db, current_user.id
    )
    return SuccessResponse(success=True, data=user_achievements)


@router.get(
    "/user/achievements/{achievement_id}",
    response_model=SuccessResponse[UserAchievementResponse],
)
async def get_user_achievement(
    achievement_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get a specific user achievement"""
    user_achievement = achievement_service.get_user_achievement(
        db, current_user.id, achievement_id
    )
    if not user_achievement:
        raise HTTPException(status_code=404, detail="User achievement not found")
    return SuccessResponse(success=True, data=user_achievement)


@router.get("/challenges", response_model=SuccessResponse[List[UserChallengeResponse]])
async def get_daily_challenges(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Get active daily challenges for the current user"""
    try:
        challenges = await run_in_threadpool(
            run_db_task, db, achievement_service.get_active_challenges, current_user.id
        )
        try:
            validated = [
                UserChallengeResponse.model_validate(c) for c in (challenges or [])
            ]
        except ValidationError as ve:
            sentry_sdk.capture_exception(ve)
            validated = []
        return SuccessResponse(success=True, data=validated)
    except HTTPException:
        raise
    except Exception as e:
        # Gamification must never take down the core app; return empty state and capture for investigation.
        sentry_sdk.capture_exception(e)
        return SuccessResponse(success=True, data=[])


@router.get("/user/stats", response_model=SuccessResponse[UserGameStatsResponse])
async def get_user_game_stats(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Get comprehensive game stats for the current user"""
    try:
        stats = await run_in_threadpool(
            run_db_task, db, achievement_service.get_user_game_stats, current_user.id
        )
        try:
            validated = UserGameStatsResponse.model_validate(stats)
        except ValidationError as ve:
            sentry_sdk.capture_exception(ve)
            raise
        return SuccessResponse(success=True, data=validated)
    except HTTPException:
        raise
    except Exception as e:
        # Gamification must never take down the core app; return safe defaults and capture for investigation.
        sentry_sdk.capture_exception(e)
        now = datetime.utcnow()
        today = now.date()
        user_id = str(getattr(current_user, "id", ""))
        return SuccessResponse(
            success=True,
            data={
                "points": {
                    "id": "fallback-points",
                    "user_id": user_id,
                    "points": 0,
                    "level": 1,
                    "created_at": now,
                },
                "achievements": {
                    "total_achievements": 0,
                    "completed_achievements": 0,
                    "total_points": 0,
                    "current_level": 1,
                    "recent_unlocks": [],
                },
                "active_challenges": [],
                "streaks": {
                    "login": {
                        "id": "fallback-streak-login",
                        "user_id": user_id,
                        "streak_type": "login",
                        "current_count": 0,
                        "highest_count": 0,
                        "last_tracked_date": today,
                        "created_at": now,
                    },
                    "savings": {
                        "id": "fallback-streak-savings",
                        "user_id": user_id,
                        "streak_type": "savings",
                        "current_count": 0,
                        "highest_count": 0,
                        "last_tracked_date": today,
                        "created_at": now,
                    },
                    "tracking": {
                        "id": "fallback-streak-tracking",
                        "user_id": user_id,
                        "streak_type": "tracking",
                        "current_count": 0,
                        "highest_count": 0,
                        "last_tracked_date": today,
                        "created_at": now,
                    },
                },
            },
        )


@router.get("/user/activity", response_model=SuccessResponse[ActivitySnapshotResponse])
async def get_user_activity_snapshot(
    force_recalculate: bool = Query(
        False, description="Force recalculation of financial/activity metrics"
    ),
    bank_name: Optional[str] = Query(
        None,
        max_length=120,
        description="Filter financial/activity metrics by bank name",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get a single page-ready ActivityPage snapshot.

    This endpoint intentionally joins financial metrics, XP/level, streaks,
    achievements, and challenges server-side so the frontend does not render
    inconsistent slices from multiple requests.
    """
    user_id = str(getattr(current_user, "id", ""))
    bank_name = bank_name.strip() if bank_name and bank_name.strip() else None
    started_at = perf_counter()
    warnings: List[str] = []

    with SentryTransaction(name="get_activity_snapshot", op="aggregation"):
        set_sentry_user(current_user)
        set_sentry_context(
            "activity_snapshot",
            {
                "user_id": user_id,
                "force_recalculate": force_recalculate,
                "bank_name": bank_name,
            },
        )

        try:
            try:
                subscription = await get_user_subscription(user_id)
            except UpstreamUnavailable:
                raise
            except Exception as subscription_error:
                sentry_sdk.capture_exception(subscription_error)
                subscription = {"plan": "free"}
                warnings.append("subscription_limits_unavailable")

            plan = subscription.get("plan", "free")
            plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
            tx_limit = plan_limits.get("max_transactions_per_month", float("inf"))
            max_transactions = None if tx_limit == float("inf") else int(tx_limit)

            try:
                # Supabase and SQLAlchemy clients are synchronous. Running them
                # in FastAPI's worker pool prevents one heavy account from
                # blocking every other request on the async event loop.
                metrics = await run_in_threadpool(
                    user_metrics_service.calculate_comprehensive_user_metrics,
                    user_id,
                    force_recalculate=force_recalculate,
                    bank_name=bank_name,
                    max_transactions=max_transactions,
                )
            except Exception as metrics_error:
                logger.exception("Activity financial metrics aggregation failed")
                sentry_sdk.capture_exception(metrics_error)
                metrics = get_default_metrics()
                warnings.append("financial_metrics_unavailable")

            game_stats_available = True
            try:
                game_stats = await run_in_threadpool(
                    run_db_task,
                    db,
                    achievement_service.get_user_game_stats,
                    current_user.id,
                )
            except UpstreamUnavailable:
                raise
            except Exception as game_stats_error:
                logger.exception("Activity gamification aggregation failed")
                sentry_sdk.capture_exception(game_stats_error)
                game_stats_available = False
                warnings.append("gamification_unavailable")
                now = datetime.now(timezone.utc)
                game_stats = {
                    "points": {
                        "points": _to_int(metrics.get("total_points"), 0),
                        "level": _to_int(metrics.get("current_level"), 1),
                    },
                    "active_challenges": [],
                    "streaks": {},
                    "achievements": {
                        "total_achievements": _to_int(
                            (metrics.get("achievement_progress") or {}).get("total"),
                            0,
                        ),
                        "completed_achievements": _to_int(
                            metrics.get("completed_achievements"), 0
                        ),
                        "total_points": _to_int(metrics.get("total_points"), 0),
                        "current_level": _to_int(metrics.get("current_level"), 1),
                        "recent_unlocks": [],
                    },
                    "generated_at": now,
                    "_user_achievements": [],
                    "_reward_points": 0,
                }

            # Reuse the eager-loaded rows from game stats instead of issuing a
            # second achievements query (and a relationship query per item).
            user_achievements = game_stats.get("_user_achievements") or []

            active_challenges = game_stats.get("active_challenges") or []
            streaks = _serialise_streaks(game_stats.get("streaks") or {})
            metrics_streaks = metrics.get("streaks") or {}
            if metrics_streaks and not streaks:
                streaks = _serialise_streaks(metrics_streaks)

            completed_achievements = sum(
                1
                for achievement in user_achievements
                if bool(_read_field(achievement, "is_completed", False))
            )
            total_achievements = len(user_achievements)
            achievement_progress = metrics.get("achievement_progress") or {}
            if total_achievements == 0:
                total_achievements = _to_int(achievement_progress.get("total"), 0)
            if completed_achievements == 0:
                completed_achievements = _to_int(
                    metrics.get("completed_achievements"), 0
                )

            current_savings_streak = max(
                _to_int(metrics.get("current_savings_streak"), 0),
                _to_int(metrics.get("savings_streak"), 0),
                _to_int(streaks.get("savings", {}).get("current_count"), 0),
            )
            longest_streak = max(
                _to_int(metrics.get("longest_streak"), 0),
                max(
                    (
                        _to_int(streak.get("highest_count"), 0)
                        for streak in streaks.values()
                    ),
                    default=0,
                ),
            )
            next_milestone = _next_streak_milestone(current_savings_streak)
            next_milestone_progress_percent = streak_milestone_progress(
                current_savings_streak, next_milestone
            )

            financial_score = max(
                0, min(100, int(round(_to_float(metrics.get("financial_score"), 50))))
            )
            savings_rate_percentage = _savings_rate_percentage(metrics)
            transaction_count = _to_int(metrics.get("transaction_count"), 0)

            if game_stats_available:
                total_points = calculate_activity_points_from_components(
                    game_stats.get("_reward_points", 0),
                    current_savings_streak=current_savings_streak,
                    financial_score=financial_score,
                )
                # Bank-filtered metrics are a view, not the user's global XP
                # state. Persist only the aggregate Activity balance.
                if bank_name is None and not warnings:
                    try:
                        await run_in_threadpool(
                            run_db_task,
                            db,
                            achievement_service.sync_user_points_with_activity_points,
                            current_user.id,
                            current_savings_streak,
                            financial_score,
                            total_points,
                        )
                    except UpstreamUnavailable:
                        warnings.append("points_sync_deferred")
                    except Exception as points_error:
                        sentry_sdk.capture_exception(points_error)
                        warnings.append("points_sync_deferred")
            else:
                # A fallback metric may already include Activity bonuses, so do
                # not add them a second time or persist an inferred balance.
                total_points = max(0, _to_int(metrics.get("total_points"), 0))

            level_snapshot = _activity_level_snapshot(total_points)

            snapshot = {
                "level": level_snapshot,
                "streak": {
                    "current_savings_streak": current_savings_streak,
                    "longest_streak": longest_streak,
                    "next_milestone": next_milestone,
                    "next_milestone_progress_percent": round(
                        next_milestone_progress_percent, 2
                    ),
                    "streaks": streaks,
                },
                "health": {
                    "financial_score": financial_score,
                    "status": _health_status(financial_score),
                    "component_scores": metrics.get("component_scores") or {},
                },
                "quick_stats": {
                    "completed_achievements": completed_achievements,
                    "total_achievements": total_achievements,
                    "savings_rate": savings_rate_percentage / 100,
                    "savings_rate_percentage": round(savings_rate_percentage, 2),
                    "transaction_count": transaction_count,
                    "spending_trend": metrics.get("spending_trend") or "stable",
                    "net_flow": _to_float(
                        metrics.get("total_savings", metrics.get("net_cash_flow")), 0
                    ),
                    "avg_daily_spending": _to_float(
                        metrics.get("avg_daily_spending"), 0
                    ),
                    "total_income": _to_float(metrics.get("total_income"), 0),
                    "total_expenses": _to_float(metrics.get("total_expenses"), 0),
                    "days_active": _to_int(metrics.get("days_active"), 0),
                    "avg_transactions_per_day": _to_float(
                        metrics.get("avg_transactions_per_day"), 0
                    ),
                    "most_active_day": metrics.get("most_active_day"),
                    "most_active_day_count": _to_int(
                        metrics.get("most_active_day_count"), 0
                    ),
                    "recent_activity": bool(metrics.get("recent_activity", False)),
                },
                "achievements": user_achievements,
                "active_challenges": active_challenges,
                "metrics": {
                    **metrics,
                    "total_points": total_points,
                    "current_level": level_snapshot["current_level"],
                    "points_to_next_level": level_snapshot["points_to_next_level"],
                    "current_savings_streak": current_savings_streak,
                    "completed_achievements": completed_achievements,
                    "total_real_achievements": completed_achievements,
                    "achievement_progress": {
                        **achievement_progress,
                        "completed": completed_achievements,
                        "total": total_achievements,
                    },
                },
                "generated_at": datetime.now(timezone.utc),
                "last_calculated": metrics.get("last_calculated"),
                "calculation_source": metrics.get("calculation_source"),
                "bank_name": bank_name,
                "is_partial": bool(warnings),
                "warnings": warnings,
                "calculation_duration_ms": round(
                    (perf_counter() - started_at) * 1000, 2
                ),
            }

            return SuccessResponse(success=True, data=snapshot)
        except HTTPException:
            raise
        except Exception as e:
            sentry_sdk.capture_exception(e)
            if settings.ENVIRONMENT == "production":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to load activity snapshot",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{e.__class__.__name__}: {str(e)}",
            )


@router.get("/{achievement_id}", response_model=SuccessResponse[AchievementResponse])
async def get_achievement(
    achievement_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get a specific achievement by ID"""
    achievement = achievement_service.get_achievement(db, achievement_id)
    if not achievement:
        raise HTTPException(status_code=404, detail="Achievement not found")
    return SuccessResponse(success=True, data=achievement)


@router.post("/", response_model=SuccessResponse[AchievementResponse])
async def create_achievement(
    request: Request,
    achievement_data: AchievementCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a new achievement (admin only)."""
    with SentryTransaction(name="create_achievement", op="database"):
        set_sentry_user(current_user)

        # Check if user is admin (simplified for example)
        if current_user.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: Admin access required",
            )

        try:
            result = achievement_service.create_achievement(
                db, achievement_data.model_dump()
            )
            return SuccessResponse(success=True, data=result)
        except Exception as e:
            # Log error securely
            sentry_sdk.capture_exception(e)
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create achievement",
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"{e.__class__.__name__}: {str(e)}",
                )


@router.put("/{achievement_id}", response_model=SuccessResponse[AchievementResponse])
async def update_achievement(
    request: Request,
    achievement_id: str,
    updates: AchievementUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update an achievement (admin only)."""
    with SentryTransaction(name="update_achievement", op="database"):
        set_sentry_user(current_user)

        # Check if user is admin (simplified for example)
        if current_user.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: Admin access required",
            )

        try:
            # Filter out None values
            update_data = {
                k: v for k, v in updates.model_dump().items() if v is not None
            }
            result = achievement_service.update_achievement(
                db, achievement_id, update_data
            )
            return SuccessResponse(success=True, data=result)
        except Exception as e:
            # Log error securely
            sentry_sdk.capture_exception(e)
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update achievement",
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"{e.__class__.__name__}: {str(e)}",
                )


@router.delete("/{achievement_id}", response_model=SuccessResponse[dict])
async def delete_achievement(
    request: Request,
    achievement_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete an achievement (admin only)."""
    with SentryTransaction(name="delete_achievement", op="database"):
        set_sentry_user(current_user)

        # Check if user is admin (simplified for example)
        if current_user.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: Admin access required",
            )

        try:
            achievement_service.delete_achievement(db, achievement_id)
            return SuccessResponse(
                success=True, data={"message": "Achievement deleted successfully"}
            )
        except Exception as e:
            # Log error securely
            sentry_sdk.capture_exception(e)
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to delete achievement",
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"{e.__class__.__name__}: {str(e)}",
                )


# User Achievement endpoints
@router.post(
    "/user/progress", response_model=SuccessResponse[List[UserAchievementResponse]]
)
async def update_achievement_progress(
    progress_data: UserAchievementProgressRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update progress for achievements triggered by an event"""
    updated = achievement_service.update_achievement_progress(
        db, current_user.id, progress_data.event_type, progress_data.value
    )
    return SuccessResponse(success=True, data=updated)


@router.post("/challenges", response_model=SuccessResponse[dict])
async def create_daily_challenge(
    request: Request,
    challenge_data: DailyChallengeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a new daily challenge (admin only)."""
    with SentryTransaction(name="create_daily_challenge", op="database"):
        set_sentry_user(current_user)

        # Check if user is admin (simplified for example)
        if current_user.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: Admin access required",
            )

        try:
            result = achievement_service.create_daily_challenge(
                db, challenge_data.model_dump()
            )
            return SuccessResponse(success=True, data=result)
        except Exception as e:
            # Log error securely
            sentry_sdk.capture_exception(e)
            # Only return generic error message in production
            if settings.ENVIRONMENT == "production":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create daily challenge",
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"{e.__class__.__name__}: {str(e)}",
                )


@router.post(
    "/challenges/{challenge_id}/progress",
    response_model=SuccessResponse[UserChallengeResponse],
)
async def update_challenge_progress(
    challenge_id: str,
    progress_data: ChallengeProgressRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update progress for a specific challenge"""
    result = await run_in_threadpool(
        achievement_service.update_challenge_progress,
        db,
        current_user.id,
        challenge_id,
        progress_data.value,
    )

    if not result:
        raise HTTPException(status_code=404, detail="Challenge not found")

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return SuccessResponse(success=True, data=result)


@router.post(
    "/challenges/{challenge_id}/complete",
    response_model=SuccessResponse[UserChallengeResponse],
)
async def complete_challenge(
    challenge_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Mark a challenge as complete and claim points"""
    result = await run_in_threadpool(
        achievement_service.complete_challenge, db, current_user.id, challenge_id
    )

    if not result:
        raise HTTPException(status_code=404, detail="Challenge not found")

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return SuccessResponse(success=True, data=result)


@router.post(
    "/streaks/{streak_type}", response_model=SuccessResponse[UserStreakResponse]
)
async def update_user_streak(
    streak_type: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update a user's streak for a specific activity"""
    # Validate streak type
    if streak_type not in ["login", "savings", "tracking"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid streak type. Must be one of: login, savings, tracking",
        )

    try:
        updated_streak = await run_in_threadpool(
            achievement_service.update_streak, db, current_user.id, streak_type
        )
        try:
            validated = UserStreakResponse.model_validate(updated_streak)
        except ValidationError as ve:
            sentry_sdk.capture_exception(ve)
            raise
        return SuccessResponse(success=True, data=validated)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        now = datetime.utcnow()
        return SuccessResponse(
            success=True,
            data={
                "id": f"fallback-streak-{streak_type}",
                "user_id": str(getattr(current_user, "id", "")),
                "streak_type": streak_type,
                "current_count": 0,
                "highest_count": 0,
                "last_tracked_date": now.date(),
                "created_at": now,
            },
        )


@router.post("/initialize", status_code=status.HTTP_200_OK)
async def initialize_achievements(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Initialize default achievements in the database - admin only"""
    # Check if user is admin
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can perform this action",
        )

    achievement_service.initialize_default_achievements(db)
    return {"status": "success", "message": "Default achievements initialized"}
