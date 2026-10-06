# api/app/services/user_metrics/config.py


class UserMetricsConfig:
    """Configuration constants for user metrics calculations"""

    SAVINGS_STREAK_MAX_DAYS = 30
    SPENDING_TREND_DAYS = 14
    FINANCIAL_HEALTH_BASE_SCORE = 50
    FINANCIAL_HEALTH_MAX_SCORE = 100
    LEVEL_CALCULATION_BASE = 100
    AUTO_REFRESH_INTERVAL_MINUTES = 5
    METRICS_CACHE_THRESHOLD_MINUTES = 30
