# api/app/routes/user_achievement_metrics_routes.py

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..schemas.user_metrics import UserAchievementMetricsResponse
from ..sentry import SentryTransaction, set_sentry_user
from ..services.user_metrics.metrics_service import UserMetricsSupabaseService
from ..utils.auth import get_current_user
from ..utils.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/user-achievement-metrics", tags=["user-metrics"])


@router.get("", response_model=UserAchievementMetricsResponse)
async def get_achievements(request: Request, current_user=Depends(get_current_user)):
    """Get all achievements for the authenticated user"""
    with SentryTransaction(name="get_achievements", op="calculation"):
        set_sentry_user(current_user)

        try:
            user_metrics_service = UserMetricsSupabaseService()
            metrics = user_metrics_service.calculate_comprehensive_user_metrics(
                str(current_user.id)
            )

            achievement_metrics = {
                "real_achievements": metrics.get("real_achievements", {}),
                "total_real_achievements": metrics.get("total_real_achievements", 0),
                "completed_achievements": metrics.get("completed_achievements", 0),
                "achievement_progress": metrics.get("achievement_progress", {}),
                "current_savings_streak": metrics.get("current_savings_streak", 0),
                "streaks": metrics.get("streaks", {}),
                "longest_streak": metrics.get("longest_streak", 0),
                "total_points": metrics.get("total_points", 0),
                "current_level": metrics.get("current_level", 1),
                "points_to_next_level": metrics.get("points_to_next_level", 100),
            }

            return {
                "success": True,
                "data": achievement_metrics,
                "calculated_at": datetime.utcnow().isoformat(),
                "user_id": str(current_user.id),
            }

        except Exception as e:
            logger.error(f"Error getting achievements for {current_user.id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get achievements",
            )
