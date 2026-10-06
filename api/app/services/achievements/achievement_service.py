# api/app/services/achievements/achievement_service.py

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ...errors.exceptions import DatabaseError, NotFoundError, ValidationError
from ...models.achievement import (
    Achievement,
    DailyChallenge,
    UserAchievement,
    UserChallenge,
    UserPoints,
    UserStreak,
)
from ...models.transaction import Transaction
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client
from ..notifications.base import NotificationPriority, NotificationType
from ..notifications.notification_service import notification_service
from .activity_calculations import (
    calculate_activity_points_from_components,
)
from .activity_calculations import (
    calculate_level_from_points as _calculate_level_from_points,
)

logger = get_logger(__name__)

# Achievement categories
CATEGORY_SAVINGS = "savings"
CATEGORY_SPENDING = "spending"
CATEGORY_STREAK = "streak"
CATEGORY_TRACKING = "tracking"
CATEGORY_WELLNESS = "wellness"
CATEGORY_ENGAGEMENT = "engagement"

# Event types
EVENT_LOGIN = "login"
EVENT_TRANSACTION_ADDED = "transaction_added"
EVENT_STATEMENT_UPLOADED = "statement_uploaded"
EVENT_GOAL_CREATED = "goal_created"
EVENT_GOAL_COMPLETED = "goal_completed"
EVENT_SAVINGS_ADDED = "savings_added"
EVENT_BUDGET_CREATED = "budget_created"
EVENT_BUDGET_FOLLOWED = "budget_followed"
EVENT_CATEGORY_TRACKED = "category_tracked"
EVENT_SUBSCRIPTION_MANAGED = "subscription_managed"
EVENT_FINANCIAL_WELLNESS_CHECK = "financial_wellness_check"
EVENT_NOTIFICATION_SETTINGS = "notification_settings"
EVENT_PROFILE_COMPLETED = "profile_completed"
EVENT_FEEDBACK_PROVIDED = "feedback_provided"
EVENT_REFERRAL_SENT = "referral_sent"
EVENT_APP_RATED = "app_rated"
LEVEL_CALCULATION_BASE = 100


def _completed_at_sort_key(item: Dict[str, Any]) -> float:
    """Return a comparable timestamp for mixed legacy datetime values."""
    completed_at = item.get("completed_at")
    if completed_at is None:
        return float("-inf")

    if isinstance(completed_at, str):
        try:
            completed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError:
            return float("-inf")

    if not isinstance(completed_at, datetime):
        return float("-inf")

    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return completed_at.timestamp()


def calculate_level_from_points(total_points: int) -> int:
    """Calculate user level from total XP.

    Formula: level = floor(sqrt(points / 100)) + 1.
    """
    return _calculate_level_from_points(total_points)


def calculate_points_to_next_level(total_points: int, current_level: int) -> int:
    """Calculate remaining XP required for the next level."""
    points = max(0, int(total_points or 0))
    level = max(1, int(current_level or 1))
    next_level_points = (level**2) * LEVEL_CALCULATION_BASE
    return max(0, next_level_points - points)


def calculate_earned_points(db: Session, user_id: str) -> int:
    """Calculate authoritative XP from completed achievements and challenges."""
    achievement_points = (
        db.query(func.coalesce(func.sum(Achievement.points), 0))
        .join(UserAchievement, UserAchievement.achievement_id == Achievement.id)
        .filter(
            UserAchievement.user_id == user_id,
            UserAchievement.is_completed.is_(True),
        )
        .scalar()
        or 0
    )
    challenge_points = (
        db.query(func.coalesce(func.sum(DailyChallenge.points), 0))
        .join(UserChallenge, UserChallenge.challenge_id == DailyChallenge.id)
        .filter(
            UserChallenge.user_id == user_id,
            UserChallenge.is_completed.is_(True),
        )
        .scalar()
        or 0
    )
    return int(achievement_points or 0) + int(challenge_points or 0)


def calculate_activity_points(
    db: Session,
    user_id: str,
    current_savings_streak: int = 0,
    financial_score: float = 0,
) -> int:
    """Calculate Activity XP from rewards, savings streak, and health score."""
    reward_points = calculate_earned_points(db, user_id)
    return calculate_activity_points_from_components(
        reward_points,
        current_savings_streak=current_savings_streak,
        financial_score=financial_score,
    )


def sync_user_points_with_earned_points(
    db: Session,
    user_id: str,
    earned_points: Optional[int] = None,
) -> UserPoints:
    """Keep user_points aligned with completed reward records.

    Database triggers are the write path for reward XP. This repair path
    backfills missing rewards while preserving Activity-specific bonus XP.
    """
    user_points = get_or_create_user_points(db, user_id)
    completed_reward_points = (
        calculate_earned_points(db, user_id)
        if earned_points is None
        else max(0, int(earned_points or 0))
    )
    # Reward-only reads must never erase streak/health bonuses written by the
    # Activity snapshot. They only repair a missing or lagging reward balance.
    authoritative_points = max(
        int(user_points.points or 0),
        completed_reward_points,
    )
    authoritative_level = calculate_level_from_points(authoritative_points)

    if (
        int(user_points.points or 0) != authoritative_points
        or int(user_points.level or 1) != authoritative_level
    ):
        user_points.points = authoritative_points
        user_points.level = authoritative_level
        db.commit()
        db.refresh(user_points)

    return user_points


def sync_user_points_with_activity_points(
    db: Session,
    user_id: str,
    current_savings_streak: int = 0,
    financial_score: float = 0,
    activity_points: Optional[int] = None,
) -> UserPoints:
    """Keep user_points aligned with Activity XP inputs."""
    user_points = get_or_create_user_points(db, user_id)
    calculated_activity_points = (
        calculate_activity_points(
            db,
            user_id,
            current_savings_streak=current_savings_streak,
            financial_score=financial_score,
        )
        if activity_points is None
        else max(0, int(activity_points or 0))
    )
    activity_level = calculate_level_from_points(calculated_activity_points)

    if (
        int(user_points.points or 0) != calculated_activity_points
        or int(user_points.level or 1) != activity_level
    ):
        user_points.points = calculated_activity_points
        user_points.level = activity_level
        db.commit()
        db.refresh(user_points)

    return user_points


def get_achievement(db: Session, achievement_id: str) -> Achievement:
    """Get a specific achievement by ID"""
    achievement = db.query(Achievement).filter(Achievement.id == achievement_id).first()
    if not achievement:
        raise HTTPException(status_code=404, detail="Achievement not found")
    return achievement


def get_achievements(db: Session, skip: int = 0, limit: int = 100) -> List[Achievement]:
    """Get all achievements"""
    return db.query(Achievement).offset(skip).limit(limit).all()


def get_achievements_by_category(db: Session, category: str) -> List[Achievement]:
    """Get achievements by category"""
    return db.query(Achievement).filter(Achievement.category == category).all()


def reconcile_completed_achievements(db: Session, user_id: str) -> int:
    """Atomically finalize legacy rows with 100% progress.

    The guarded update is safe under concurrent reads: only the request that
    transitions a row from incomplete to complete fires the reward trigger.
    """
    completed_at = datetime.now(timezone.utc)
    try:
        repaired_count = (
            db.query(UserAchievement)
            .filter(
                UserAchievement.user_id == user_id,
                UserAchievement.is_completed.is_(False),
                UserAchievement.progress >= 100,
            )
            .update(
                {
                    UserAchievement.is_completed: True,
                    UserAchievement.completed_at: func.coalesce(
                        UserAchievement.completed_at, completed_at
                    ),
                },
                synchronize_session=False,
            )
        )
        if repaired_count:
            db.commit()
            logger.info(
                "Finalized %s completed achievement records for user %s",
                repaired_count,
                user_id,
            )
        return int(repaired_count or 0)
    except Exception:
        db.rollback()
        logger.exception(
            "Could not reconcile completed achievements for user %s", user_id
        )
        return 0


def get_user_achievements(db: Session, user_id: str) -> List[UserAchievement]:
    """Get all achievements for a specific user"""
    reconcile_completed_achievements(db, user_id)
    return (
        db.query(UserAchievement)
        .options(joinedload(UserAchievement.achievement))
        .filter(UserAchievement.user_id == user_id)
        .all()
    )


def get_user_completed_achievements(db: Session, user_id: str) -> List[UserAchievement]:
    """Get completed achievements for a specific user"""
    return (
        db.query(UserAchievement)
        .filter(
            UserAchievement.user_id == user_id, UserAchievement.is_completed.is_(True)
        )
        .all()
    )


def get_user_achievement(
    db: Session, user_id: str, achievement_id: str
) -> Optional[UserAchievement]:
    """Get a specific user achievement"""
    return (
        db.query(UserAchievement)
        .filter(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement_id == achievement_id,
        )
        .first()
    )


def create_user_achievement(
    db: Session, user_id: str, achievement_id: str
) -> UserAchievement:
    """Create a new user achievement record"""
    # Check if it already exists
    existing = get_user_achievement(db, user_id, achievement_id)
    if existing:
        return existing

    # Get the achievement details
    get_achievement(db, achievement_id)

    # Create new user achievement record
    user_achievement = UserAchievement(
        id=uuid4(),  # UUID object, not string
        user_id=user_id,
        achievement_id=achievement_id,
        progress=0,
        current_value=0,
        is_completed=False,
    )

    db.add(user_achievement)
    db.commit()
    db.refresh(user_achievement)
    return user_achievement


def update_achievement_progress(
    db: Session, user_id: str, event_type: str, value: float = 1
) -> List[UserAchievement]:
    """
    Update progress for all achievements triggered by an event type
    Returns list of updated user achievements
    """
    # Find all achievements triggered by this event
    achievements = (
        db.query(Achievement).filter(Achievement.trigger_event == event_type).all()
    )

    if not achievements:
        return []

    updated_achievements = []
    for achievement in achievements:
        # Get or create user achievement
        user_achievement = get_user_achievement(db, user_id, achievement.id)
        if not user_achievement:
            user_achievement = create_user_achievement(db, user_id, achievement.id)

        # Skip if already completed and not repeatable
        if user_achievement.is_completed and not achievement.is_repeatable:
            continue

        # Update progress
        current_value = user_achievement.current_value + value
        progress = min(100, int((current_value / achievement.target_value) * 100))

        user_achievement.current_value = current_value
        user_achievement.progress = progress

        # Check if newly completed
        newly_completed = False
        if progress >= 100 and not user_achievement.is_completed:
            user_achievement.is_completed = True
            user_achievement.completed_at = datetime.utcnow()
            newly_completed = True

            # Send achievement unlocked notification
            import asyncio

            try:
                asyncio.create_task(
                    notification_service.send_achievement_notification(
                        user_id=user_id,
                        achievement_data={
                            "achievement_id": str(achievement.id),
                            "achievement_name": achievement.title,
                            "achievement_description": achievement.description,
                            "points": achievement.points,
                            "rarity": getattr(achievement, "rarity", None) or "common",
                            "category": achievement.category,
                            "icon": achievement.icon,
                            "completed_at": datetime.utcnow().isoformat(),
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to send achievement notification: {e}")

        db.commit()
        db.refresh(user_achievement)
        updated_achievements.append(user_achievement)

        # If newly completed, trigger any dependent achievements
        if newly_completed and achievement.is_repeatable:
            # For repeatable achievements, reset progress
            user_achievement.current_value = 0
            user_achievement.progress = 0
            user_achievement.is_completed = False
            user_achievement.completed_at = None
            db.commit()

    return updated_achievements


def get_or_create_user_points(db: Session, user_id: str) -> UserPoints:
    """Get or create user points record"""
    user_points = db.query(UserPoints).filter(UserPoints.user_id == user_id).first()
    if not user_points:
        user_points = UserPoints(
            id=uuid4(), user_id=user_id, points=0, level=1
        )  # UUID object, not string
        db.add(user_points)
        db.commit()
        db.refresh(user_points)
    return user_points


def award_points(db: Session, user_id: str, points: int) -> UserPoints:
    """Award points to a user and update level if necessary"""
    user_points = get_or_create_user_points(db, user_id)
    previous_level = user_points.level
    user_points.points += points

    new_level = calculate_level_from_points(user_points.points)
    leveled_up = False
    if new_level > user_points.level:
        user_points.level = new_level
        leveled_up = True

    db.commit()
    db.refresh(user_points)

    # Send level up notification
    if leveled_up:
        import asyncio

        try:
            # Calculate XP needed for next level using the same formula
            # Points for level n = (n-1)^2 * LEVEL_CALCULATION_BASE
            # So points for next level (new_level + 1) = new_level^2 * 100
            xp_to_next = calculate_points_to_next_level(user_points.points, new_level)
            asyncio.create_task(
                notification_service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.LEVEL_UP,
                    data={
                        "level": new_level,
                        "total_xp": user_points.points,
                        "xp_to_next_level": xp_to_next,
                        "total_achievements": 0,  # Could be calculated
                        "previous_level": previous_level,
                    },
                    priority=NotificationPriority.HIGH,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to send level up notification: {e}")

    return user_points


def get_or_create_user_streak(
    db: Session, user_id: str, streak_type: str
) -> UserStreak:
    """Get or create a user streak record"""
    user_streak = (
        db.query(UserStreak)
        .filter(UserStreak.user_id == user_id, UserStreak.streak_type == streak_type)
        .first()
    )

    if not user_streak:
        user_streak = UserStreak(
            id=uuid4(),  # UUID object, not string
            user_id=user_id,
            streak_type=streak_type,
            current_count=0,
            highest_count=0,
            # Column is a DATE; always store a date object to avoid driver casting issues.
            last_tracked_date=(
                datetime.utcnow() - timedelta(days=2)
            ).date(),  # Start with no active streak
        )
        db.add(user_streak)
        db.commit()
        db.refresh(user_streak)

    return user_streak


def get_or_create_user_streaks(
    db: Session, user_id: str, streak_types: List[str]
) -> Dict[str, UserStreak]:
    """Load a user's streaks and create missing rows in one transaction."""
    unique_types = list(dict.fromkeys(streak_types))
    rows = (
        db.query(UserStreak)
        .filter(
            UserStreak.user_id == user_id,
            UserStreak.streak_type.in_(unique_types),
        )
        .all()
    )
    streaks = {row.streak_type: row for row in rows}
    missing_types = [
        streak_type for streak_type in unique_types if streak_type not in streaks
    ]

    if missing_types:
        initial_date = (datetime.utcnow() - timedelta(days=2)).date()
        db.add_all(
            [
                UserStreak(
                    id=uuid4(),
                    user_id=user_id,
                    streak_type=streak_type,
                    current_count=0,
                    highest_count=0,
                    last_tracked_date=initial_date,
                )
                for streak_type in missing_types
            ]
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()

        rows = (
            db.query(UserStreak)
            .filter(
                UserStreak.user_id == user_id,
                UserStreak.streak_type.in_(unique_types),
            )
            .all()
        )
        streaks = {row.streak_type: row for row in rows}

    return streaks


def update_streak(db: Session, user_id: str, streak_type: str) -> UserStreak:
    """Update a user's streak"""
    user_streak = get_or_create_user_streak(db, user_id, streak_type)

    # Check if already updated today
    today = datetime.utcnow().date()
    last_tracked = (
        user_streak.last_tracked_date if user_streak.last_tracked_date else None
    )

    if last_tracked == today:
        # Already tracked today
        return user_streak

    # Check if streak is continuous
    is_continuous = False
    if last_tracked:
        days_since_last = (today - last_tracked).days
        is_continuous = days_since_last == 1

    # Update streak count
    if is_continuous:
        user_streak.current_count += 1
    else:
        user_streak.current_count = 1  # Reset streak but count today

    # Update highest count if needed
    if user_streak.current_count > user_streak.highest_count:
        user_streak.highest_count = user_streak.current_count

    # Update last tracked date
    user_streak.last_tracked_date = today

    db.commit()
    db.refresh(user_streak)

    # Check for streak milestones (3, 7, 14, 30, 60, 90, 365 days)
    streak_milestones = [3, 7, 14, 30, 60, 90, 365]
    current_count = user_streak.current_count

    if current_count in streak_milestones:
        import asyncio

        try:
            # Calculate bonus points based on milestone
            bonus_points = current_count * 5  # 5 points per day of streak
            asyncio.create_task(
                notification_service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.STREAK_MILESTONE,
                    data={
                        "streak_days": current_count,
                        "streak_type": streak_type.replace("_", " ").title(),
                        "bonus_points": bonus_points,
                        "start_date": (
                            datetime.utcnow() - timedelta(days=current_count)
                        ).strftime("%Y-%m-%d"),
                    },
                    priority=NotificationPriority.HIGH,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to send streak milestone notification: {e}")

    # Check if any streak achievements have been unlocked
    check_streak_achievements(db, user_id, streak_type, user_streak.current_count)

    return user_streak


def check_streak_achievements(
    db: Session, user_id: str, streak_type: str, streak_count: int
):
    """Check and update any streak-based achievements"""
    # Find streak achievements for this streak type
    event_type = f"{streak_type}_streak"

    achievements = (
        db.query(Achievement).filter(Achievement.trigger_event == event_type).all()
    )

    for achievement in achievements:
        user_achievement = get_user_achievement(db, user_id, achievement.id)
        if not user_achievement:
            user_achievement = create_user_achievement(db, user_id, achievement.id)

        # Skip if already completed and not repeatable
        if user_achievement.is_completed and not achievement.is_repeatable:
            continue

        # Update progress based on streak count
        progress = min(100, int((streak_count / achievement.target_value) * 100))
        user_achievement.current_value = streak_count
        user_achievement.progress = progress

        # Check if completed
        if progress >= 100 and not user_achievement.is_completed:
            user_achievement.is_completed = True
            user_achievement.completed_at = datetime.utcnow()

            # Send streak achievement notification
            import asyncio

            try:
                asyncio.create_task(
                    notification_service.send_achievement_notification(
                        user_id=user_id,
                        achievement_data={
                            "achievement_id": str(achievement.id),
                            "achievement_name": achievement.title,
                            "achievement_description": achievement.description,
                            "points": achievement.points,
                            "rarity": getattr(achievement, "rarity", None) or "common",
                            "category": achievement.category,
                            "icon": achievement.icon,
                            "streak_count": streak_count,
                            "completed_at": datetime.utcnow().isoformat(),
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to send streak achievement notification: {e}")

        db.commit()
        db.refresh(user_achievement)


def generate_daily_challenges(db: Session, user_id: str) -> List[UserChallenge]:
    """Generate daily challenges for a user"""
    # Multiple page providers can request challenges at the same time. A
    # transaction-scoped advisory lock makes the check-and-create sequence
    # idempotent across API workers without holding an application mutex.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"daily-challenges:{user_id}"},
        )

    # Check if user already has active challenges for today
    today = datetime.utcnow().date()
    tomorrow = today + timedelta(days=1)
    tomorrow_midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day)

    existing_challenges = (
        db.query(UserChallenge)
        .join(DailyChallenge, UserChallenge.challenge_id == DailyChallenge.id)
        .filter(
            UserChallenge.user_id == user_id,
            DailyChallenge.expires_at > datetime.utcnow(),
        )
        .all()
    )

    if existing_challenges:
        return existing_challenges

    # Get user stats to personalize challenges
    user_stats = get_user_stats(db, user_id)

    # Create new challenges based on user's activity level
    challenge_templates = create_challenge_templates(user_stats, tomorrow_midnight)

    # Save challenges to database
    created_challenges = []
    for template in challenge_templates:
        # Create the challenge
        challenge = DailyChallenge(
            id=uuid4(),  # UUID object, not string
            title=template["title"],
            description=template["description"],
            challenge_type=template["challenge_type"],
            points=template["points"],
            target_value=template["target_value"],
            expires_at=template["expires_at"],
            is_active=True,
        )
        db.add(challenge)
        db.flush()

        # Create user challenge record
        user_challenge = UserChallenge(
            id=uuid4(),  # UUID object, not string
            user_id=user_id,
            challenge_id=challenge.id,
            progress=0,
            is_completed=False,
        )
        db.add(user_challenge)
        created_challenges.append(user_challenge)

    db.commit()
    for challenge in created_challenges:
        db.refresh(challenge)

    return created_challenges


def get_user_stats(db: Session, user_id: str) -> Dict[str, Any]:
    """Get user statistics for personalizing challenges"""
    # Aggregate in the database instead of materialising every transaction.
    transactions_count = int(
        db.query(func.count(Transaction.id))
        .filter(Transaction.user_id == user_id)
        .scalar()
        or 0
    )
    achievements_count = int(
        db.query(func.count(UserAchievement.id))
        .filter(
            UserAchievement.user_id == user_id,
            UserAchievement.is_completed.is_(True),
        )
        .scalar()
        or 0
    )
    streaks = get_or_create_user_streaks(
        db, user_id, [CATEGORY_SAVINGS, "login", CATEGORY_TRACKING]
    )
    savings_streak = streaks[CATEGORY_SAVINGS]
    login_streak = streaks["login"]
    tracking_streak = streaks[CATEGORY_TRACKING]

    # Calculate user activity level
    activity_level = calculate_activity_level(
        transactions_count, achievements_count, login_streak.current_count
    )

    total_expenses, expense_days = (
        db.query(
            func.coalesce(func.sum(func.abs(Transaction.transaction_amount)), 0),
            func.count(func.distinct(Transaction.transaction_date)),
        )
        .filter(
            Transaction.user_id == user_id,
            func.lower(Transaction.transaction_type) == "debit",
        )
        .one()
    )
    avg_daily_spending = (
        float(total_expenses or 0) / max(1, int(expense_days or 0))
        if total_expenses
        else 50
    )

    return {
        "transactions_count": transactions_count,
        "achievements_count": achievements_count,
        "login_streak": login_streak.current_count,
        "savings_streak": savings_streak.current_count,
        "tracking_streak": tracking_streak.current_count,
        "activity_level": activity_level,
        "avg_daily_spending": avg_daily_spending,
    }


def calculate_activity_level(
    transactions_count: int, achievements_count: int, login_streak: int
) -> str:
    """Calculate user activity level for personalizing challenges"""
    score = transactions_count * 0.3 + achievements_count * 0.4 + login_streak * 0.3

    if score < 5:
        return "beginner"
    elif score < 20:
        return "intermediate"
    else:
        return "advanced"


def create_challenge_templates(
    user_stats: Dict[str, Any], expiry: datetime
) -> List[Dict[str, Any]]:
    """Create personalized daily challenges based on user stats"""
    activity_level = user_stats["activity_level"]
    avg_daily_spending = user_stats["avg_daily_spending"]

    # Adjust challenge difficulty based on activity level
    difficulty_multiplier = {"beginner": 0.7, "intermediate": 1.0, "advanced": 1.5}.get(
        activity_level, 1.0
    )

    # Base savings target as percentage of average daily spending
    savings_percentage = {
        "beginner": 0.1,  # 10% of average daily spending
        "intermediate": 0.2,  # 20% of average daily spending
        "advanced": 0.3,  # 30% of average daily spending
    }.get(activity_level, 0.2)

    # Calculate savings target amount
    savings_target = max(
        5, round(float(avg_daily_spending) * savings_percentage, -1)
    )  # Round to nearest 10

    # Generate 3-5 challenges across different categories
    challenges = []

    # At least one savings challenge
    challenges.append(
        {
            "title": "Daily Savings Goal",
            "description": f"Save ${savings_target} today",
            "challenge_type": CATEGORY_SAVINGS,
            "points": int(30 * difficulty_multiplier),
            "target_value": savings_target,
            "expires_at": expiry,
        }
    )

    # Add a spending challenge
    spending_target = round(
        float(avg_daily_spending) * 0.8, -1
    )  # 80% of avg daily spending, rounded to nearest 10
    challenges.append(
        {
            "title": "Spending Challenge",
            "description": f"Keep today's spending under ${spending_target}",
            "challenge_type": CATEGORY_SPENDING,
            "points": int(25 * difficulty_multiplier),
            "target_value": spending_target,
            "expires_at": expiry,
        }
    )

    # Add a tracking challenge
    tracking_target = {"beginner": 3, "intermediate": 5, "advanced": 8}.get(
        activity_level, 5
    )

    challenges.append(
        {
            "title": "Category Master",
            "description": f"Categorize {tracking_target} transactions today",
            "challenge_type": CATEGORY_TRACKING,
            "points": int(20 * difficulty_multiplier),
            "target_value": tracking_target,
            "expires_at": expiry,
        }
    )

    # Add engagement challenge
    challenges.append(
        {
            "title": "App Explorer",
            "description": "Visit 3 different sections of the app today",
            "challenge_type": CATEGORY_ENGAGEMENT,
            "points": 15,
            "target_value": 3,
            "expires_at": expiry,
        }
    )

    # Add a wellness challenge (for more advanced users)
    if activity_level in ["intermediate", "advanced"]:
        challenges.append(
            {
                "title": "Financial Wellness Check",
                "description": "Complete your weekly financial wellness assessment",
                "challenge_type": CATEGORY_WELLNESS,
                "points": 40,
                "target_value": 1,
                "expires_at": expiry,
            }
        )

    return challenges


def get_active_challenges(db: Session, user_id: str) -> List[Dict[str, Any]]:
    """Get active challenges for a user with full challenge details"""
    user_challenges = (
        db.query(UserChallenge)
        .options(joinedload(UserChallenge.challenge))
        .join(DailyChallenge, UserChallenge.challenge_id == DailyChallenge.id)
        .filter(
            UserChallenge.user_id == user_id,
            DailyChallenge.expires_at > datetime.utcnow(),
            DailyChallenge.is_active.is_(True),
        )
        .all()
    )

    # If no active challenges, generate new ones
    if not user_challenges:
        generate_daily_challenges(db, user_id)
        user_challenges = (
            db.query(UserChallenge)
            .options(joinedload(UserChallenge.challenge))
            .join(DailyChallenge, UserChallenge.challenge_id == DailyChallenge.id)
            .filter(
                UserChallenge.user_id == user_id,
                DailyChallenge.expires_at > datetime.utcnow(),
                DailyChallenge.is_active.is_(True),
            )
            .all()
        )

    selected_challenges: Dict[Tuple[str, str], Tuple[UserChallenge, DailyChallenge]] = (
        {}
    )
    for uc in user_challenges:
        challenge = uc.challenge
        if challenge:
            key = (challenge.challenge_type, challenge.title)
            current = selected_challenges.get(key)
            candidate_score = (
                bool(uc.is_completed),
                float(uc.progress or 0),
                uc.created_at.timestamp() if uc.created_at else float("-inf"),
            )
            current_score = (
                (
                    bool(current[0].is_completed),
                    float(current[0].progress or 0),
                    (
                        current[0].created_at.timestamp()
                        if current[0].created_at
                        else float("-inf")
                    ),
                )
                if current
                else None
            )
            if current_score is None or candidate_score > current_score:
                selected_challenges[key] = (uc, challenge)

    return [
        {
            "id": uc.id,
            "user_id": uc.user_id,
            "challenge_id": uc.challenge_id,
            "progress": uc.progress,
            "is_completed": uc.is_completed,
            "completed_at": uc.completed_at,
            "created_at": uc.created_at,
            "challenge": challenge,
        }
        for uc, challenge in selected_challenges.values()
    ]


def update_challenge_progress(
    db: Session, user_id: str, challenge_id: str, value: float = 1
) -> Optional[Dict[str, Any]]:
    """Update progress for a specific challenge"""
    user_challenge = (
        db.query(UserChallenge)
        .filter(UserChallenge.id == challenge_id, UserChallenge.user_id == user_id)
        .first()
    )

    if not user_challenge:
        return None

    # Get challenge details
    challenge = (
        db.query(DailyChallenge)
        .filter(DailyChallenge.id == user_challenge.challenge_id)
        .first()
    )
    if not challenge:
        return None

    # Check if challenge is expired
    if challenge.expires_at < datetime.utcnow():
        return {
            "id": user_challenge.id,
            "user_id": user_challenge.user_id,
            "challenge_id": user_challenge.challenge_id,
            "progress": user_challenge.progress,
            "is_completed": user_challenge.is_completed,
            "completed_at": user_challenge.completed_at,
            "created_at": user_challenge.created_at,
            "challenge": challenge,
            "error": "Challenge has expired",
        }

    # Update progress
    user_challenge.progress += value

    # Check if completed
    if (
        user_challenge.progress >= challenge.target_value
        and not user_challenge.is_completed
    ):
        user_challenge.is_completed = True
        user_challenge.completed_at = datetime.utcnow()

        # Send challenge completed notification
        import asyncio

        try:
            asyncio.create_task(
                notification_service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.CHALLENGE_COMPLETED,
                    data={
                        "challenge_id": str(challenge.id),
                        "challenge_name": challenge.title,
                        "reward_points": challenge.points,
                        "completion_time": user_challenge.completed_at.strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "difficulty": getattr(challenge, "difficulty", None)
                        or "medium",
                    },
                    priority=NotificationPriority.MEDIUM,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to send challenge completion notification: {e}")

    db.commit()
    db.refresh(user_challenge)

    return {
        "id": user_challenge.id,
        "user_id": user_challenge.user_id,
        "challenge_id": user_challenge.challenge_id,
        "progress": user_challenge.progress,
        "is_completed": user_challenge.is_completed,
        "completed_at": user_challenge.completed_at,
        "created_at": user_challenge.created_at,
        "challenge": challenge,
    }


def complete_challenge(
    db: Session, user_id: str, challenge_id: str
) -> Optional[Dict[str, Any]]:
    """Mark a challenge as complete and award points"""
    user_challenge = (
        db.query(UserChallenge)
        .filter(UserChallenge.id == challenge_id, UserChallenge.user_id == user_id)
        .first()
    )

    if not user_challenge:
        return None

    # Get challenge details
    challenge = (
        db.query(DailyChallenge)
        .filter(DailyChallenge.id == user_challenge.challenge_id)
        .first()
    )
    if not challenge:
        return None

    # Check if challenge is expired
    if challenge.expires_at < datetime.utcnow():
        return {
            "id": user_challenge.id,
            "user_id": user_challenge.user_id,
            "challenge_id": user_challenge.challenge_id,
            "progress": user_challenge.progress,
            "is_completed": user_challenge.is_completed,
            "completed_at": user_challenge.completed_at,
            "created_at": user_challenge.created_at,
            "challenge": challenge,
            "error": "Challenge has expired",
        }

    # Check if already completed
    if user_challenge.is_completed:
        return {
            "id": user_challenge.id,
            "user_id": user_challenge.user_id,
            "challenge_id": user_challenge.challenge_id,
            "progress": user_challenge.progress,
            "is_completed": user_challenge.is_completed,
            "completed_at": user_challenge.completed_at,
            "created_at": user_challenge.created_at,
            "challenge": challenge,
            "error": "Challenge already completed",
        }

    # Check if requirements met
    if user_challenge.progress < challenge.target_value:
        return {
            "id": user_challenge.id,
            "user_id": user_challenge.user_id,
            "challenge_id": user_challenge.challenge_id,
            "progress": user_challenge.progress,
            "is_completed": user_challenge.is_completed,
            "completed_at": user_challenge.completed_at,
            "created_at": user_challenge.created_at,
            "challenge": challenge,
            "error": "Challenge requirements not met",
        }

    # Mark as completed
    user_challenge.is_completed = True
    user_challenge.completed_at = datetime.utcnow()

    db.commit()
    db.refresh(user_challenge)

    # Send challenge completed notification
    import asyncio

    try:
        asyncio.create_task(
            notification_service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.CHALLENGE_COMPLETED,
                data={
                    "challenge_id": str(challenge.id),
                    "challenge_name": challenge.title,
                    "reward_points": challenge.points,
                    "completion_time": user_challenge.completed_at.strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "difficulty": getattr(challenge, "difficulty", None) or "medium",
                },
                priority=NotificationPriority.MEDIUM,
            )
        )
    except Exception as e:
        logger.warning(f"Failed to send challenge completed notification: {e}")

    return {
        "id": user_challenge.id,
        "user_id": user_challenge.user_id,
        "challenge_id": user_challenge.challenge_id,
        "progress": user_challenge.progress,
        "is_completed": user_challenge.is_completed,
        "completed_at": user_challenge.completed_at,
        "created_at": user_challenge.created_at,
        "challenge": challenge,
    }


def get_user_game_stats(db: Session, user_id: str) -> Dict[str, Any]:
    """Get comprehensive game stats for a user"""

    achievements = db.query(Achievement).all()
    if not achievements:
        initialize_default_achievements(db)
        achievements = db.query(Achievement).all()

    # Assign newly introduced achievements in one transaction. The unique
    # constraint handles concurrent first loads safely; after a race, reload
    # the rows inserted by the winning request.
    existing_ids = {
        achievement_id
        for (achievement_id,) in (
            db.query(UserAchievement.achievement_id)
            .filter(UserAchievement.user_id == user_id)
            .all()
        )
    }
    missing_ids = [
        achievement.id
        for achievement in achievements
        if achievement.id not in existing_ids
    ]
    if missing_ids:
        db.add_all(
            [
                UserAchievement(
                    id=uuid4(),
                    user_id=user_id,
                    achievement_id=achievement_id,
                    progress=0,
                    current_value=0,
                    is_completed=False,
                )
                for achievement_id in missing_ids
            ]
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()

    # Get achievements summary
    user_achievements = get_user_achievements(db, user_id)
    completed_achievements = [ua for ua in user_achievements if ua.is_completed]

    total_points = calculate_earned_points(db, user_id)
    # Repair missing reward points without downgrading Activity bonus XP.
    points = sync_user_points_with_earned_points(
        db, user_id, earned_points=total_points
    )

    # Get recent unlocks with full achievement details
    recent_unlocks = []
    for ua in user_achievements:
        achievement = ua.achievement
        if achievement:
            recent_unlocks.append(
                {
                    "id": ua.id,
                    "user_id": ua.user_id,
                    "achievement_id": ua.achievement_id,
                    "progress": ua.progress,
                    "current_value": ua.current_value,
                    "is_completed": ua.is_completed,
                    "completed_at": ua.completed_at,
                    "created_at": ua.created_at,
                    "achievement": achievement,
                }
            )

    # Sort by completion date, most recent first
    recent_unlocks.sort(
        key=_completed_at_sort_key,
        reverse=True,
    )

    # Limit to 10 most recent
    recent_unlocks = recent_unlocks[:10]

    # Get active challenges
    active_challenges = get_active_challenges(db, user_id)

    streaks = get_or_create_user_streaks(
        db, user_id, [CATEGORY_SAVINGS, "login", CATEGORY_TRACKING]
    )

    return {
        "points": points,
        "achievements": {
            "total_achievements": len(achievements),
            "completed_achievements": len(completed_achievements),
            "total_points": total_points,
            "current_level": points.level,
            "recent_unlocks": recent_unlocks,
        },
        "active_challenges": active_challenges,
        "streaks": streaks,
        # Internal reuse for the aggregate Activity endpoint. Pydantic ignores
        # this key on the public /user/stats response model.
        "_user_achievements": user_achievements,
        "_reward_points": total_points,
    }


def get_default_achievements() -> List[Dict[str, Any]]:
    """Get the default achievement data for initialization"""
    return [
        # Savings Achievements
        {
            "id": "savings-beginner",
            "title": "Savings Novice",
            "description": "Save your first $100",
            "category": CATEGORY_SAVINGS,
            "level": 1,
            "points": 50,
            "icon": "PiggyBank",
            "target_value": 100,
            "trigger_event": "savings_added",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "savings-intermediate",
            "title": "Savings Enthusiast",
            "description": "Save a total of $1,000",
            "category": CATEGORY_SAVINGS,
            "level": 2,
            "points": 200,
            "icon": "PiggyBank",
            "target_value": 1000,
            "trigger_event": "savings_added",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "savings-advanced",
            "title": "Savings Master",
            "description": "Save a total of $10,000",
            "category": CATEGORY_SAVINGS,
            "level": 3,
            "points": 500,
            "icon": "PiggyBank",
            "target_value": 10000,
            "trigger_event": "savings_added",
            "is_repeatable": False,
            "is_hidden": False,
        },
        # Tracking Achievements
        {
            "id": "tracking-beginner",
            "title": "Tracking Novice",
            "description": "Track your first 10 transactions",
            "category": CATEGORY_TRACKING,
            "level": 1,
            "points": 50,
            "icon": "FileText",
            "target_value": 10,
            "trigger_event": "transaction_added",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "tracking-intermediate",
            "title": "Tracking Enthusiast",
            "description": "Track 100 transactions",
            "category": CATEGORY_TRACKING,
            "level": 2,
            "points": 150,
            "icon": "FileText",
            "target_value": 100,
            "trigger_event": "transaction_added",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "tracking-advanced",
            "title": "Tracking Master",
            "description": "Track 1,000 transactions",
            "category": CATEGORY_TRACKING,
            "level": 3,
            "points": 500,
            "icon": "FileText",
            "target_value": 1000,
            "trigger_event": "transaction_added",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "statement-uploader",
            "title": "Statement Uploader",
            "description": "Upload your first bank statement",
            "category": CATEGORY_TRACKING,
            "level": 1,
            "points": 100,
            "icon": "FileText",
            "target_value": 1,
            "trigger_event": "statement_uploaded",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "categorization-master",
            "title": "Category Master",
            "description": "Categorize 50 transactions",
            "category": CATEGORY_TRACKING,
            "level": 2,
            "points": 200,
            "icon": "Tag",
            "target_value": 50,
            "trigger_event": "category_tracked",
            "is_repeatable": False,
            "is_hidden": False,
        },
        # Spending Achievements
        {
            "id": "budget-creator",
            "title": "Budget Creator",
            "description": "Create your first budget",
            "category": CATEGORY_SPENDING,
            "level": 1,
            "points": 100,
            "icon": "TrendingDown",
            "target_value": 1,
            "trigger_event": "budget_created",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "budget-master",
            "title": "Budget Master",
            "description": "Stay under budget for 30 days",
            "category": CATEGORY_SPENDING,
            "level": 2,
            "points": 150,
            "icon": "TrendingDown",
            "target_value": 30,
            "trigger_event": "budget_followed",
            "is_repeatable": False,
            "is_hidden": False,
        },
        # Streak Achievements
        {
            "id": "login-streak-1",
            "title": "Regular User",
            "description": "Login 3 days in a row",
            "category": CATEGORY_STREAK,
            "level": 1,
            "points": 20,
            "icon": "Zap",
            "target_value": 3,
            "trigger_event": "login_streak",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "login-streak-2",
            "title": "Dedicated User",
            "description": "Login 7 days in a row",
            "category": CATEGORY_STREAK,
            "level": 2,
            "points": 50,
            "icon": "Zap",
            "target_value": 7,
            "trigger_event": "login_streak",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "login-streak-3",
            "title": "FISC Enthusiast",
            "description": "Login 30 days in a row",
            "category": CATEGORY_STREAK,
            "level": 3,
            "points": 200,
            "icon": "Zap",
            "target_value": 30,
            "trigger_event": "login_streak",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "savings-streak-1",
            "title": "Savings Streak: Beginner",
            "description": "Add to savings 7 days in a row",
            "category": CATEGORY_STREAK,
            "level": 1,
            "points": 100,
            "icon": "Zap",
            "target_value": 7,
            "trigger_event": "savings_streak",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "savings-streak-2",
            "title": "Savings Streak: Advanced",
            "description": "Add to savings 30 days in a row",
            "category": CATEGORY_STREAK,
            "level": 3,
            "points": 300,
            "icon": "Zap",
            "target_value": 30,
            "trigger_event": "savings_streak",
            "is_repeatable": False,
            "is_hidden": False,
        },
        # Wellness Achievements
        {
            "id": "first-goal",
            "title": "Goal Setter",
            "description": "Create your first financial goal",
            "category": CATEGORY_WELLNESS,
            "level": 1,
            "points": 40,
            "icon": "Shield",
            "target_value": 1,
            "trigger_event": "goal_created",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "goal-achieved",
            "title": "Goal Achiever",
            "description": "Complete a financial goal",
            "category": CATEGORY_WELLNESS,
            "level": 2,
            "points": 150,
            "icon": "Shield",
            "target_value": 1,
            "trigger_event": "goal_completed",
            "is_repeatable": True,
            "is_hidden": False,
        },
        {
            "id": "financial-wellness",
            "title": "Financial Health Check",
            "description": "Complete 5 financial wellness assessments",
            "category": CATEGORY_WELLNESS,
            "level": 2,
            "points": 100,
            "icon": "Shield",
            "target_value": 5,
            "trigger_event": "financial_wellness_check",
            "is_repeatable": False,
            "is_hidden": False,
        },
        # Engagement Achievements
        {
            "id": "profile-complete",
            "title": "Profile Complete",
            "description": "Complete your user profile",
            "category": CATEGORY_ENGAGEMENT,
            "level": 1,
            "points": 20,
            "icon": "UserCheck",
            "target_value": 1,
            "trigger_event": "profile_completed",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "feedback-provider",
            "title": "Feedback Provider",
            "description": "Provide feedback on the app",
            "category": CATEGORY_ENGAGEMENT,
            "level": 1,
            "points": 30,
            "icon": "UserCheck",
            "target_value": 1,
            "trigger_event": "feedback_provided",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "app-promoter",
            "title": "App Promoter",
            "description": "Refer 3 friends to FISC",
            "category": CATEGORY_ENGAGEMENT,
            "level": 2,
            "points": 100,
            "icon": "UserCheck",
            "target_value": 3,
            "trigger_event": "referral_sent",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "app-rater",
            "title": "App Rater",
            "description": "Rate the app in the app store",
            "category": CATEGORY_ENGAGEMENT,
            "level": 1,
            "points": 50,
            "icon": "UserCheck",
            "target_value": 1,
            "trigger_event": "app_rated",
            "is_repeatable": False,
            "is_hidden": False,
        },
        {
            "id": "notification-settings",
            "title": "Stay Informed",
            "description": "Configure your notification preferences",
            "category": CATEGORY_ENGAGEMENT,
            "level": 1,
            "points": 15,
            "icon": "UserCheck",
            "target_value": 1,
            "trigger_event": "notification_settings",
            "is_repeatable": False,
            "is_hidden": False,
        },
    ]


def initialize_default_achievements(db: Session):
    """Initialize default achievements in the database"""
    default_achievements = get_default_achievements()

    # Add achievements that don't exist yet
    for achievement_data in default_achievements:
        existing = (
            db.query(Achievement)
            .filter(Achievement.id == achievement_data["id"])
            .first()
        )
        if not existing:
            achievement = Achievement(**achievement_data)
            db.add(achievement)

    db.commit()


class AchievementService:
    @staticmethod
    def get_achievements():
        """Get all achievements."""
        try:
            supabase = get_supabase_client()
            result = supabase.table("achievements").select("*").execute()
            return result.data
        except Exception as e:
            logger.warning(f"Failed to retrieve achievements: {str(e)}")
            raise

    @staticmethod
    def get_achievement(achievement_id: str):
        """Get a specific achievement."""
        try:
            supabase = get_supabase_client()

            result = (
                supabase.table("achievements")
                .select("*")
                .eq("id", achievement_id)
                .execute()
            )

            if not result.data:
                raise NotFoundError("Achievement", achievement_id)

            return result.data[0]
        except Exception as e:
            logger.warning(f"Failed to retrieve achievement: {str(e)}")
            raise

    @staticmethod
    def create_achievement(achievement_data: Dict[str, Any]):
        """Create a new achievement."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Prepare achievement data
            achievement = {
                "id": str(uuid4()),
                "title": achievement_data.get("title"),
                "description": achievement_data.get("description"),
                "category": achievement_data.get("category"),
                "level": achievement_data.get("level", 1),
                "points": achievement_data.get("points"),
                "icon": achievement_data.get("icon"),
                "target_value": achievement_data.get("target_value"),
                "trigger_event": achievement_data.get("trigger_event"),
                "is_repeatable": achievement_data.get("is_repeatable", False),
                "is_hidden": achievement_data.get("is_hidden", False),
                "requires_achievement_id": achievement_data.get(
                    "requires_achievement_id"
                ),
            }

            # Validate required fields
            required_fields = [
                "title",
                "description",
                "category",
                "points",
                "icon",
                "target_value",
                "trigger_event",
            ]
            for field in required_fields:
                if not achievement.get(field):
                    raise ValidationError(f"Missing required field: {field}")

            # Insert into database
            result = supabase.table("achievements").insert(achievement).execute()

            if not result.data:
                raise DatabaseError("Failed to create achievement")

            return result.data[0]
        except Exception as e:
            logger.warning(f"Achievement creation failed: {str(e)}")
            raise

    @staticmethod
    def update_achievement(achievement_id: str, updates: Dict[str, Any]):
        """Update an achievement."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify achievement exists
            AchievementService.get_achievement(achievement_id)

            # Apply updates
            result = (
                supabase.table("achievements")
                .update(updates)
                .eq("id", achievement_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to update achievement")

            return result.data[0]
        except Exception as e:
            logger.warning(f"Failed to update achievement: {str(e)}")
            raise

    @staticmethod
    def delete_achievement(achievement_id: str):
        """Delete an achievement."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify achievement exists
            AchievementService.get_achievement(achievement_id)

            # Delete achievement
            result = (
                supabase.table("achievements")
                .delete()
                .eq("id", achievement_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to delete achievement")

            return {"success": True, "message": "Achievement deleted successfully"}
        except Exception as e:
            logger.warning(f"Failed to delete achievement: {str(e)}")
            raise

    @staticmethod
    def get_user_achievements(user_id: str):
        """Get all achievements for a user with progress."""
        try:
            supabase = get_supabase_client()

            # Get user's achievement progress
            user_achievements_result = (
                supabase.table("user_achievements")
                .select("*, achievement:achievements(*)")
                .eq("user_id", user_id)
                .execute()
            )

            # Get all achievements
            all_achievements_result = (
                supabase.table("achievements")
                .select("*")
                .not_.eq("is_hidden", True)
                .execute()
            )

            user_achievements = user_achievements_result.data
            all_achievements = all_achievements_result.data

            # Map user achievements by achievement_id for easy lookup
            user_achievements_map = {
                ua["achievement_id"]: ua for ua in user_achievements
            }

            # For each achievement, add progress info or create with zero progress
            complete_achievements = []
            for achievement in all_achievements:
                achievement_id = achievement["id"]
                if achievement_id in user_achievements_map:
                    # User has this achievement in progress
                    user_ach = user_achievements_map[achievement_id]
                    complete_achievements.append(
                        {**user_ach, "achievement": achievement}
                    )
                else:
                    # User does not have this achievement yet, create a placeholder
                    complete_achievements.append(
                        {
                            "id": None,
                            "user_id": user_id,
                            "achievement_id": achievement_id,
                            "progress": 0,
                            "current_value": 0,
                            "is_completed": False,
                            "completed_at": None,
                            "achievement": achievement,
                        }
                    )

            return complete_achievements
        except Exception as e:
            logger.warning(f"Failed to retrieve user achievements: {str(e)}")
            raise

    @staticmethod
    def get_user_achievement(user_id: str, achievement_id: str):
        """Get specific achievement progress for a user."""
        try:
            supabase = get_supabase_client()

            # Get user's achievement progress
            result = (
                supabase.table("user_achievements")
                .select("*, achievement:achievements(*)")
                .eq("user_id", user_id)
                .eq("achievement_id", achievement_id)
                .execute()
            )

            if not result.data:
                # Check if achievement exists
                achievement = AchievementService.get_achievement(achievement_id)

                # Return placeholder with zero progress
                return {
                    "id": None,
                    "user_id": user_id,
                    "achievement_id": achievement_id,
                    "progress": 0,
                    "current_value": 0,
                    "is_completed": False,
                    "completed_at": None,
                    "achievement": achievement,
                }

            return result.data[0]
        except Exception as e:
            logger.warning(f"Failed to retrieve user achievement: {str(e)}")
            raise

    @staticmethod
    def track_achievement_progress(user_id: str, event_type: str, value: float = 1.0):
        """Track progress for achievements triggered by specific events."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Find all achievements triggered by this event
            achievements_result = (
                supabase.table("achievements")
                .select("*")
                .eq("trigger_event", event_type)
                .execute()
            )

            achievements = achievements_result.data
            if not achievements:
                # No achievements for this event type
                return []

            updated_achievements = []

            for achievement in achievements:
                # Get current progress for this achievement
                user_achievement_result = (
                    supabase.table("user_achievements")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("achievement_id", achievement["id"])
                    .execute()
                )

                # Check if there's a dependency achievement required
                if achievement.get("requires_achievement_id"):
                    # Check if the required achievement is completed
                    prereq_result = (
                        supabase.table("user_achievements")
                        .select("*")
                        .eq("user_id", user_id)
                        .eq("achievement_id", achievement["requires_achievement_id"])
                        .eq("is_completed", True)
                        .execute()
                    )

                    if not prereq_result.data:
                        # Skip this achievement, prerequisite not met
                        continue

                if user_achievement_result.data:
                    # Update existing progress
                    user_achievement = user_achievement_result.data[0]

                    # Skip if already completed and not repeatable
                    if (
                        user_achievement["is_completed"]
                        and not achievement["is_repeatable"]
                    ):
                        continue

                    # Update progress
                    current_value = user_achievement["current_value"] + value
                    target_value = achievement["target_value"]
                    progress_percent = min(100, (current_value / target_value) * 100)
                    is_completed = current_value >= target_value
                    completed_at = (
                        datetime.now().isoformat()
                        if is_completed and not user_achievement["is_completed"]
                        else user_achievement["completed_at"]
                    )

                    update_data = {
                        "current_value": current_value,
                        "progress": progress_percent,
                        "is_completed": is_completed,
                    }

                    if completed_at:
                        update_data["completed_at"] = completed_at

                    update_result = (
                        supabase.table("user_achievements")
                        .update(update_data)
                        .eq("id", user_achievement["id"])
                        .execute()
                    )

                    if update_result.data:
                        updated_achievement = {
                            **update_result.data[0],
                            "achievement": achievement,
                        }
                        updated_achievements.append(updated_achievement)
                else:
                    # Create new progress entry
                    current_value = value
                    target_value = achievement["target_value"]
                    progress_percent = min(100, (current_value / target_value) * 100)
                    is_completed = current_value >= target_value
                    completed_at = datetime.now().isoformat() if is_completed else None

                    new_user_achievement = {
                        "id": str(uuid4()),
                        "user_id": user_id,
                        "achievement_id": achievement["id"],
                        "current_value": current_value,
                        "progress": progress_percent,
                        "is_completed": is_completed,
                        "completed_at": completed_at,
                    }

                    insert_result = (
                        supabase.table("user_achievements")
                        .insert(new_user_achievement)
                        .execute()
                    )

                    if insert_result.data:
                        new_achievement = {
                            **insert_result.data[0],
                            "achievement": achievement,
                        }
                        updated_achievements.append(new_achievement)

            return updated_achievements
        except Exception as e:
            logger.warning(f"Failed to track achievement progress: {str(e)}")
            raise

    @staticmethod
    def get_user_game_stats(user_id: str):
        """Get comprehensive gamification stats for a user."""
        try:
            supabase = get_supabase_client()

            # Check if any achievements exist in the database
            achievements_result = (
                supabase.table("achievements").select("id").limit(1).execute()
            )

            # Initialize default achievements if none exist
            if not achievements_result.data:
                logger.info(
                    "No achievements found in the database. Initializing default achievements..."
                )
                # Create default achievements
                for achievement_data in [
                    a
                    for a in get_default_achievements()
                    if not a.get("is_hidden", False)
                ]:
                    try:
                        supabase.table("achievements").insert(
                            achievement_data
                        ).execute()
                    except Exception as e:
                        logger.warning(
                            f"Failed to create achievement {achievement_data['id']}: {str(e)}"
                        )

            # Check if user has any achievements
            user_achievements_result = (
                supabase.table("user_achievements")
                .select("id")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

            # If user has no achievements, initialize them
            if not user_achievements_result.data:
                logger.info(
                    f"No achievements found for user {user_id}. Initializing user achievements..."
                )
                # Get all achievements
                all_achievements = (
                    supabase.table("achievements").select("id").execute().data
                )

                # Assign all achievements to user with 0 progress
                for achievement in all_achievements:
                    try:
                        new_user_achievement = {
                            "id": str(uuid4()),
                            "user_id": user_id,
                            "achievement_id": achievement["id"],
                            "progress": 0,
                            "current_value": 0,
                            "is_completed": False,
                            "created_at": datetime.now().isoformat(),
                        }
                        supabase.table("user_achievements").insert(
                            new_user_achievement
                        ).execute()
                    except Exception as e:
                        logger.warning(
                            f"Failed to create user achievement for {achievement['id']}: {str(e)}"
                        )

            # Get user points
            points_result = (
                supabase.table("user_points")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )

            if not points_result.data:
                # Create default points record if not exists
                points = {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "points": 0,
                    "level": 1,
                    "created_at": datetime.now().isoformat(),
                }
                supabase.table("user_points").insert(points).execute()
            else:
                points = points_result.data[0]

            # Get user achievements summary
            achievements_result = (
                supabase.table("user_achievements")
                .select("*, achievement:achievements(*)")
                .eq("user_id", user_id)
                .execute()
            )

            completed_achievements = [
                a for a in achievements_result.data if a["is_completed"]
            ]

            # Get recent unlocks (last 5 completed achievements)
            recent_unlocks = sorted(
                completed_achievements,
                key=lambda a: a["completed_at"] if a["completed_at"] else "0",
                reverse=True,
            )[:5]

            # Get active challenges
            challenges_result = (
                supabase.table("user_challenges")
                .select("*, challenge:daily_challenges(*)")
                .eq("user_id", user_id)
                .eq("is_completed", False)
                .execute()
            )

            active_challenges = challenges_result.data

            # Get streaks
            streaks_result = (
                supabase.table("user_streaks")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )

            streaks = {s["streak_type"]: s for s in streaks_result.data}

            # Compile stats
            achievement_summary = {
                "total_achievements": len(achievements_result.data),
                "completed_achievements": len(completed_achievements),
                "total_points": points["points"],
                "current_level": points["level"],
                "recent_unlocks": recent_unlocks,
            }

            game_stats = {
                "points": points,
                "achievements": achievement_summary,
                "active_challenges": active_challenges,
                "streaks": streaks,
            }

            return game_stats
        except Exception as e:
            logger.warning(f"Failed to retrieve user game stats: {str(e)}")
            raise

    @staticmethod
    def update_user_streak(user_id: str, streak_type: str):
        """Update a user's streak for a specific activity."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            today = date.today()

            # Get current streak
            streak_result = (
                supabase.table("user_streaks")
                .select("*")
                .eq("user_id", user_id)
                .eq("streak_type", streak_type)
                .execute()
            )

            if streak_result.data:
                streak = streak_result.data[0]
                last_date = datetime.fromisoformat(streak["last_tracked_date"]).date()

                # If last tracked was yesterday, increment streak
                if today - last_date == timedelta(days=1):
                    current_count = streak["current_count"] + 1
                    highest_count = max(current_count, streak["highest_count"])

                    update_data = {
                        "current_count": current_count,
                        "highest_count": highest_count,
                        "last_tracked_date": today.isoformat(),
                    }

                    update_result = (
                        supabase.table("user_streaks")
                        .update(update_data)
                        .eq("id", streak["id"])
                        .execute()
                    )

                    if not update_result.data:
                        raise DatabaseError("Failed to update streak")

                    updated_streak = update_result.data[0]

                    # Check for streak-based achievements
                    if streak_type == "login":
                        AchievementService.track_achievement_progress(
                            user_id, "login_streak", value=current_count
                        )
                    elif streak_type == "savings":
                        AchievementService.track_achievement_progress(
                            user_id, "savings_streak", value=current_count
                        )
                    elif streak_type == "tracking":
                        AchievementService.track_achievement_progress(
                            user_id, "tracking_streak", value=current_count
                        )

                    return updated_streak
                # If last tracked was today, no change needed
                elif today == last_date:
                    return streak
                # If more than one day has passed, reset streak
                else:
                    update_data = {
                        "current_count": 1,
                        "last_tracked_date": today.isoformat(),
                    }

                    update_result = (
                        supabase.table("user_streaks")
                        .update(update_data)
                        .eq("id", streak["id"])
                        .execute()
                    )

                    if not update_result.data:
                        raise DatabaseError("Failed to reset streak")

                    return update_result.data[0]
            else:
                # Create new streak
                new_streak = {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "streak_type": streak_type,
                    "current_count": 1,
                    "highest_count": 1,
                    "last_tracked_date": today.isoformat(),
                }

                insert_result = (
                    supabase.table("user_streaks").insert(new_streak).execute()
                )

                if not insert_result.data:
                    raise DatabaseError("Failed to create streak")

                return insert_result.data[0]
        except Exception as e:
            logger.warning(f"Failed to update user streak: {str(e)}")
            raise

    @staticmethod
    def get_daily_challenges(user_id: str):
        """Get active daily challenges for a user."""
        try:
            supabase = get_supabase_client()

            now = datetime.now().isoformat()

            # Get all active challenges
            challenges_result = (
                supabase.table("daily_challenges")
                .select("*")
                .eq("is_active", True)
                .gt("expires_at", now)
                .execute()
            )

            challenges = challenges_result.data

            # Get user's progress on these challenges
            user_challenges_result = (
                supabase.table("user_challenges")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )

            user_challenges = {
                uc["challenge_id"]: uc for uc in user_challenges_result.data
            }

            # Combine challenge data with user progress
            complete_challenges = []
            for challenge in challenges:
                challenge_id = challenge["id"]
                if challenge_id in user_challenges:
                    user_challenge = user_challenges[challenge_id]
                    complete_challenges.append(
                        {**user_challenge, "challenge": challenge}
                    )
                else:
                    # User hasn't started this challenge yet
                    complete_challenges.append(
                        {
                            "id": None,
                            "user_id": user_id,
                            "challenge_id": challenge_id,
                            "progress": 0,
                            "is_completed": False,
                            "completed_at": None,
                            "challenge": challenge,
                        }
                    )

            return complete_challenges
        except Exception as e:
            logger.warning(f"Failed to retrieve daily challenges: {str(e)}")
            raise

    @staticmethod
    def create_daily_challenge(challenge_data: Dict[str, Any]):
        """Create a new daily challenge."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Prepare challenge data
            challenge = {
                "id": str(uuid4()),
                "title": challenge_data.get("title"),
                "description": challenge_data.get("description"),
                "challenge_type": challenge_data.get("challenge_type"),
                "points": challenge_data.get("points"),
                "target_value": challenge_data.get("target_value"),
                "expires_at": challenge_data.get("expires_at"),
                "is_active": challenge_data.get("is_active", True),
            }

            # Validate required fields
            required_fields = [
                "title",
                "description",
                "challenge_type",
                "points",
                "target_value",
                "expires_at",
            ]
            for field in required_fields:
                if not challenge.get(field):
                    raise ValidationError(f"Missing required field: {field}")

            # Insert into database
            result = supabase.table("daily_challenges").insert(challenge).execute()

            if not result.data:
                raise DatabaseError("Failed to create daily challenge")

            return result.data[0]
        except Exception as e:
            logger.warning(f"Daily challenge creation failed: {str(e)}")
            raise

    @staticmethod
    def update_challenge_progress(user_id: str, challenge_id: str, value: float = 1.0):
        """Update progress for a specific challenge."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Get the challenge
            challenge_result = (
                supabase.table("daily_challenges")
                .select("*")
                .eq("id", challenge_id)
                .eq("is_active", True)
                .execute()
            )

            if not challenge_result.data:
                raise NotFoundError("Daily challenge", challenge_id)

            challenge = challenge_result.data[0]

            # Check if challenge has expired
            if datetime.fromisoformat(challenge["expires_at"]) < datetime.now():
                raise ValidationError("Challenge has expired")

            # Get user's progress on this challenge
            user_challenge_result = (
                supabase.table("user_challenges")
                .select("*")
                .eq("user_id", user_id)
                .eq("challenge_id", challenge_id)
                .execute()
            )

            if user_challenge_result.data:
                # Update existing progress
                user_challenge = user_challenge_result.data[0]

                # Skip if already completed
                if user_challenge["is_completed"]:
                    return user_challenge

                # Update progress
                current_value = user_challenge["progress"] + value
                target_value = challenge["target_value"]
                is_completed = current_value >= target_value
                completed_at = datetime.now().isoformat() if is_completed else None

                update_data = {"progress": current_value, "is_completed": is_completed}

                if completed_at:
                    update_data["completed_at"] = completed_at

                update_result = (
                    supabase.table("user_challenges")
                    .update(update_data)
                    .eq("id", user_challenge["id"])
                    .execute()
                )

                if not update_result.data:
                    raise DatabaseError("Failed to update challenge progress")

                updated_challenge = {**update_result.data[0], "challenge": challenge}

                return updated_challenge
            else:
                # Create new progress entry
                current_value = value
                target_value = challenge["target_value"]
                is_completed = current_value >= target_value
                completed_at = datetime.now().isoformat() if is_completed else None

                new_user_challenge = {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "challenge_id": challenge_id,
                    "progress": current_value,
                    "is_completed": is_completed,
                    "completed_at": completed_at,
                }

                insert_result = (
                    supabase.table("user_challenges")
                    .insert(new_user_challenge)
                    .execute()
                )

                if not insert_result.data:
                    raise DatabaseError("Failed to create challenge progress")

                return {**insert_result.data[0], "challenge": challenge}
        except Exception as e:
            logger.warning(f"Failed to update challenge progress: {str(e)}")
            raise

    @staticmethod
    def complete_challenge(user_id: str, challenge_id: str):
        """Mark a challenge as completed and award points."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Get user's progress on this challenge
            user_challenge_result = (
                supabase.table("user_challenges")
                .select("*, challenge:daily_challenges(*)")
                .eq("user_id", user_id)
                .eq("challenge_id", challenge_id)
                .execute()
            )

            if not user_challenge_result.data:
                raise NotFoundError("User challenge", challenge_id)

            user_challenge = user_challenge_result.data[0]
            challenge = user_challenge["challenge"]

            # Verify challenge can be completed
            if user_challenge["is_completed"]:
                raise ValidationError("Challenge already completed")

            if datetime.fromisoformat(challenge["expires_at"]) < datetime.now():
                raise ValidationError("Challenge has expired")

            if user_challenge["progress"] < challenge["target_value"]:
                raise ValidationError("Challenge requirements not met")

            # Mark as completed
            update_data = {
                "is_completed": True,
                "completed_at": datetime.now().isoformat(),
            }

            update_result = (
                supabase.table("user_challenges")
                .update(update_data)
                .eq("id", user_challenge["id"])
                .execute()
            )

            if not update_result.data:
                raise DatabaseError("Failed to complete challenge")

            # Points will be awarded via database trigger

            return {**update_result.data[0], "challenge": challenge}
        except Exception as e:
            logger.warning(f"Failed to complete challenge: {str(e)}")
            raise
