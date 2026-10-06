# api/app/schemas/user_metrics.py

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class FinancialMetrics(BaseModel):
    """Financial metrics data schema"""

    savings_streak: int = Field(..., description="Current savings streak in days")
    total_savings: float = Field(..., description="Total net cash flow")
    total_income: float = Field(..., description="Total income amount")
    total_expenses: float = Field(..., description="Total expenses amount")
    avg_daily_spending: float = Field(..., description="Average daily spending")
    savings_rate: float = Field(..., description="Savings rate as percentage")
    spending_trend: str = Field(..., description="Spending trend: up, down, or stable")
    financial_score: int = Field(..., description="Financial health score (0-100)")
    transaction_count: int = Field(..., description="Total number of transactions")


class RealAchievements(BaseModel):
    """Real achievements based on transaction data"""

    transaction_milestones: int = Field(
        ..., description="Transaction milestone achievements"
    )
    savings_achievements: int = Field(..., description="Savings-related achievements")
    streak_achievements: int = Field(..., description="Streak-related achievements")
    balance_achievements: int = Field(..., description="Balance-related achievements")


class AchievementProgress(BaseModel):
    """Achievement progress summary"""

    completed: int = Field(..., description="Number of completed achievements")
    in_progress: int = Field(..., description="Number of achievements in progress")
    total: int = Field(..., description="Total number of achievements")


class AchievementMetrics(BaseModel):
    """Achievement metrics data schema"""

    real_achievements: RealAchievements
    total_real_achievements: int = Field(
        ..., description="Total real achievements earned"
    )
    completed_achievements: int = Field(..., description="Total completed achievements")
    achievement_progress: AchievementProgress
    current_savings_streak: int = Field(..., description="Current savings streak")
    streaks: Dict[str, Any] = Field(..., description="All user streaks data")
    longest_streak: int = Field(..., description="Longest streak achieved")
    total_points: int = Field(..., description="Total points earned")
    current_level: int = Field(..., description="Current user level")
    points_to_next_level: int = Field(..., description="Points needed for next level")


class ActivityMetrics(BaseModel):
    """User activity metrics data schema"""

    days_active: int = Field(..., description="Total days with activity")
    avg_transactions_per_day: float = Field(
        ..., description="Average transactions per day"
    )
    most_active_day: Optional[str] = Field(None, description="Most active day")
    most_active_day_count: int = Field(
        0, description="Transaction count on most active day"
    )
    recent_activity: bool = Field(..., description="Whether user has recent activity")
    transaction_count: int = Field(..., description="Total transaction count")


class ComprehensiveUserMetrics(BaseModel):
    """Comprehensive user metrics combining all metric types"""

    # Financial metrics
    savings_streak: int
    total_savings: float
    total_income: float
    total_expenses: float
    avg_daily_spending: float
    savings_rate: float
    spending_trend: str
    financial_score: int
    transaction_count: int

    # Achievement metrics
    real_achievements: RealAchievements
    total_real_achievements: int
    completed_achievements: int
    achievement_progress: AchievementProgress
    current_savings_streak: int
    streaks: Dict[str, Any]
    longest_streak: int
    total_points: int
    current_level: int
    points_to_next_level: int

    # Activity metrics
    days_active: int
    avg_transactions_per_day: float
    most_active_day: Optional[str]
    most_active_day_count: int = 0
    recent_activity: bool

    # Metadata
    last_calculated: str
    calculation_source: str

    # Subscriptions
    subscriptions: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list, description="User's active subscriptions"
    )


class UserMetricsResponse(BaseModel):
    """Response schema for comprehensive user metrics"""

    success: bool
    data: ComprehensiveUserMetrics
    calculated_at: str
    user_id: str


class UserFinancialMetricsResponse(BaseModel):
    """Response schema for financial metrics only"""

    success: bool
    data: FinancialMetrics
    calculated_at: str
    user_id: str


class UserAchievementMetricsResponse(BaseModel):
    """Response schema for achievement metrics only"""

    success: bool
    data: AchievementMetrics
    calculated_at: str
    user_id: str


class UserActivityMetricsResponse(BaseModel):
    """Response schema for activity metrics only"""

    success: bool
    data: ActivityMetrics
    calculated_at: str
    user_id: str


class MetricsSummary(BaseModel):
    """Quick metrics summary for dashboard"""

    financial_health_score: int
    current_level: int
    total_points: int
    savings_streak: int
    total_achievements: int
    savings_rate: float
    spending_trend: str
    recent_activity: bool
    last_calculated: Optional[str]


class UserMetricsSummaryResponse(BaseModel):
    """Response schema for metrics summary"""

    success: bool
    data: MetricsSummary
    user_id: str


class RecalculateMetricsResponse(BaseModel):
    """Response schema for recalculation endpoint"""

    success: bool
    message: str
    calculated_at: str
    metrics_summary: Dict[str, Union[int, float]]


# Request schemas for future use
class UpdateMetricsRequest(BaseModel):
    """Request schema for updating specific metrics"""

    force_recalculate: bool = Field(False, description="Force full recalculation")
    metrics_to_update: Optional[List[str]] = Field(
        None, description="Specific metrics to update"
    )


class MetricsFilterRequest(BaseModel):
    """Request schema for filtering metrics by date range"""

    start_date: Optional[datetime] = Field(
        None, description="Start date for metrics calculation"
    )
    end_date: Optional[datetime] = Field(
        None, description="End date for metrics calculation"
    )
    include_projections: bool = Field(False, description="Include projected metrics")
