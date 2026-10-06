# api/app/services/achievements/__init__.py

"""
Achievements Services Module

Services for managing user achievements, streaks, challenges, and gamification.

Services:
- achievement_service - Achievement tracking and gamification system

Usage:
    from ..services.achievements import achievement_service
"""

from . import achievement_service
from .achievement_service import (
    CATEGORY_ENGAGEMENT,
    CATEGORY_SAVINGS,
    CATEGORY_SPENDING,
    CATEGORY_STREAK,
    CATEGORY_TRACKING,
    CATEGORY_WELLNESS,
    EVENT_BUDGET_CREATED,
    EVENT_BUDGET_FOLLOWED,
    EVENT_CATEGORY_TRACKED,
    EVENT_FEEDBACK_PROVIDED,
    EVENT_FINANCIAL_WELLNESS_CHECK,
    EVENT_GOAL_COMPLETED,
    EVENT_GOAL_CREATED,
    EVENT_LOGIN,
    EVENT_NOTIFICATION_SETTINGS,
    EVENT_PROFILE_COMPLETED,
    EVENT_REFERRAL_SENT,
    EVENT_SAVINGS_ADDED,
    EVENT_STATEMENT_UPLOADED,
    EVENT_SUBSCRIPTION_MANAGED,
    EVENT_TRANSACTION_ADDED,
    AchievementService,
    get_user_achievements,
    update_achievement_progress,
    update_streak,
)

__all__ = [
    # Achievement categories
    "CATEGORY_SAVINGS",
    "CATEGORY_SPENDING",
    "CATEGORY_STREAK",
    "CATEGORY_TRACKING",
    "CATEGORY_WELLNESS",
    "CATEGORY_ENGAGEMENT",
    # Event types
    "EVENT_LOGIN",
    "EVENT_TRANSACTION_ADDED",
    "EVENT_STATEMENT_UPLOADED",
    "EVENT_GOAL_CREATED",
    "EVENT_GOAL_COMPLETED",
    "EVENT_SAVINGS_ADDED",
    "EVENT_BUDGET_CREATED",
    "EVENT_BUDGET_FOLLOWED",
    "EVENT_CATEGORY_TRACKED",
    "EVENT_SUBSCRIPTION_MANAGED",
    "EVENT_FINANCIAL_WELLNESS_CHECK",
    "EVENT_NOTIFICATION_SETTINGS",
    "EVENT_PROFILE_COMPLETED",
    "EVENT_FEEDBACK_PROVIDED",
    "EVENT_REFERRAL_SENT",
    # Classes
    "AchievementService",
    "achievement_service",
    # Functions
    "update_achievement_progress",
    "update_streak",
    "get_user_achievements",
]
