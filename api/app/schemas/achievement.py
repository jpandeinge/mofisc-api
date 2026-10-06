# api/app/schemas/achievement.py

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AchievementBase(BaseModel):
    """Base achievement schema."""

    title: str
    description: str
    category: str
    level: int
    points: int
    icon: str
    target_value: float
    trigger_event: str
    is_repeatable: bool = False
    is_hidden: bool = False
    requires_achievement_id: Optional[str] = None


class AchievementCreate(AchievementBase):
    """Schema for creating a new achievement."""

    id: str


class AchievementUpdate(BaseModel):
    """Schema for updating an achievement."""

    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    level: Optional[int] = None
    points: Optional[int] = None
    icon: Optional[str] = None
    target_value: Optional[float] = None
    trigger_event: Optional[str] = None
    is_repeatable: Optional[bool] = None
    is_hidden: Optional[bool] = None
    requires_achievement_id: Optional[str] = None


class AchievementResponse(AchievementBase):
    """Full achievement schema with all fields."""

    id: str
    created_at: datetime

    @field_validator("id", mode="before")
    @classmethod
    def convert_uuid_to_str(cls, v):
        return str(v) if isinstance(v, UUID) else v

    class Config:
        from_attributes = True


class UserAchievementBase(BaseModel):
    """Base user achievement schema."""

    user_id: str
    achievement_id: str
    progress: float = 0
    current_value: float = 0
    is_completed: bool = False
    completed_at: Optional[datetime] = None


class UserAchievementCreate(UserAchievementBase):
    """Schema for creating a user achievement progress record."""

    id: Optional[str] = None


class UserAchievementUpdate(BaseModel):
    """Schema for updating user achievement progress."""

    progress: Optional[float] = None
    current_value: Optional[float] = None
    is_completed: Optional[bool] = None
    completed_at: Optional[datetime] = None


class UserAchievementResponse(UserAchievementBase):
    """Full user achievement schema."""

    id: str
    created_at: Optional[datetime] = None
    achievement: Optional[AchievementResponse] = None

    @field_validator("id", "user_id", "achievement_id", mode="before")
    @classmethod
    def convert_uuid_to_str(cls, v):
        return str(v) if isinstance(v, UUID) else v

    class Config:
        from_attributes = True


class UserAchievementProgressRequest(BaseModel):
    """Schema for tracking achievement progress."""

    event_type: str
    value: float = 1.0


class DailyChallengeBase(BaseModel):
    """Base daily challenge schema."""

    title: str
    description: str
    challenge_type: str
    points: int
    target_value: float
    expires_at: datetime
    is_active: bool = True


class DailyChallengeCreate(DailyChallengeBase):
    """Schema for creating a new daily challenge."""

    id: Optional[str] = None


class DailyChallengeUpdate(BaseModel):
    """Schema for updating a daily challenge."""

    title: Optional[str] = None
    description: Optional[str] = None
    challenge_type: Optional[str] = None
    points: Optional[int] = None
    target_value: Optional[float] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None


class DailyChallengeResponse(DailyChallengeBase):
    """Full daily challenge schema."""

    id: str
    created_at: datetime

    @field_validator("id", mode="before")
    @classmethod
    def convert_uuid_to_str(cls, v):
        return str(v) if isinstance(v, UUID) else v

    class Config:
        from_attributes = True


class UserChallengeBase(BaseModel):
    """Base user challenge schema."""

    user_id: str
    challenge_id: str
    progress: float = 0
    is_completed: bool = False
    completed_at: Optional[datetime] = None


class UserChallengeCreate(UserChallengeBase):
    """Schema for creating a user challenge progress record."""

    id: Optional[str] = None


class UserChallengeUpdate(BaseModel):
    """Schema for updating user challenge progress."""

    progress: Optional[float] = None
    is_completed: Optional[bool] = None
    completed_at: Optional[datetime] = None


class UserChallengeResponse(UserChallengeBase):
    """Full user challenge schema."""

    id: str
    created_at: Optional[datetime] = None
    challenge: Optional[DailyChallengeResponse] = None

    @field_validator("id", "user_id", "challenge_id", mode="before")
    @classmethod
    def convert_uuid_to_str(cls, v):
        return str(v) if isinstance(v, UUID) else v

    class Config:
        from_attributes = True


class ChallengeProgressRequest(BaseModel):
    """Schema for tracking challenge progress."""

    value: float = 1.0


class UserStreakBase(BaseModel):
    """Base user streak schema."""

    user_id: str
    streak_type: str
    current_count: int = 0
    highest_count: int = 0
    # Stored as DATE in Postgres (see migrations + model), so expose as date.
    last_tracked_date: date


class UserStreakCreate(UserStreakBase):
    """Schema for creating a user streak record."""

    id: Optional[str] = None


class UserStreakUpdate(BaseModel):
    """Schema for updating a user streak."""

    current_count: Optional[int] = None
    highest_count: Optional[int] = None
    last_tracked_date: Optional[datetime] = None


class UserStreakResponse(UserStreakBase):
    """Full user streak schema."""

    id: str
    created_at: Optional[datetime] = None

    @field_validator("id", "user_id", mode="before")
    @classmethod
    def convert_uuid_to_str(cls, v):
        return str(v) if isinstance(v, UUID) else v

    class Config:
        from_attributes = True


class UserPointsBase(BaseModel):
    """Base user points schema."""

    user_id: str
    points: int = 0
    level: int = 1


class UserPointsCreate(UserPointsBase):
    """Schema for creating a user points record."""

    id: Optional[str] = None


class UserPointsUpdate(BaseModel):
    """Schema for updating user points."""

    points: Optional[int] = None
    level: Optional[int] = None


class UserPointsResponse(UserPointsBase):
    """Full user points schema."""

    id: str
    created_at: Optional[datetime] = None

    @field_validator("id", "user_id", mode="before")
    @classmethod
    def convert_uuid_to_str(cls, v):
        return str(v) if isinstance(v, UUID) else v

    class Config:
        from_attributes = True


class AchievementSummary(BaseModel):
    """Summary of user's achievement status."""

    total_achievements: int
    completed_achievements: int
    total_points: int
    current_level: int
    recent_unlocks: List[UserAchievementResponse]


class UserGameStatsResponse(BaseModel):
    """Combined gamification stats for a user."""

    points: UserPointsResponse
    achievements: AchievementSummary
    active_challenges: List[UserChallengeResponse]
    streaks: Dict[str, UserStreakResponse]

    class Config:
        from_attributes = True


class ActivityLevelSnapshot(BaseModel):
    """Level and XP state for the activity page."""

    total_points: int
    current_level: int
    points_to_next_level: int
    next_level_points: int
    current_level_start_points: int
    points_within_level: int
    points_needed_this_level: int
    level_progress_percent: float


class ActivityStreakSnapshot(BaseModel):
    """Savings streak state for the activity page."""

    current_savings_streak: int
    longest_streak: int
    next_milestone: int
    next_milestone_progress_percent: float
    streaks: Dict[str, Any]


class ActivityHealthSnapshot(BaseModel):
    """Financial health score state for the activity page."""

    financial_score: int
    status: str
    component_scores: Dict[str, Any] = Field(default_factory=dict)


class ActivityQuickStatsSnapshot(BaseModel):
    """Core quick stats rendered by ActivityPage."""

    completed_achievements: int
    total_achievements: int
    savings_rate: float
    savings_rate_percentage: float
    transaction_count: int
    spending_trend: str
    net_flow: float
    avg_daily_spending: float
    total_income: float
    total_expenses: float
    days_active: int
    avg_transactions_per_day: float
    most_active_day: Optional[str] = None
    most_active_day_count: int = 0
    recent_activity: bool = False


class ActivitySnapshotResponse(BaseModel):
    """Single page-ready contract for ActivityPage."""

    level: ActivityLevelSnapshot
    streak: ActivityStreakSnapshot
    health: ActivityHealthSnapshot
    quick_stats: ActivityQuickStatsSnapshot
    achievements: List[UserAchievementResponse]
    active_challenges: List[UserChallengeResponse]
    metrics: Dict[str, Any]
    generated_at: datetime
    last_calculated: Optional[str] = None
    calculation_source: Optional[str] = None
    bank_name: Optional[str] = None
    is_partial: bool = False
    warnings: List[str] = Field(default_factory=list)
    calculation_duration_ms: float = 0
