# api/app/models/__init__.py

from sqlalchemy.orm import relationship

from ..database import Base
from .achievement import (
    Achievement,
    DailyChallenge,
    UserAchievement,
    UserChallenge,
    UserPoints,
    UserStreak,
)
from .device import TrustedDevice
from .financial_metrics import (
    UserCashFlowData,
    UserCategorySpending,
    UserFinancialMetrics,
    UserMonthlyTrends,
    UserSubscriptions,
)
from .notification import Notification, UserDevice
from .transaction import Transaction
from .transaction_edit_history import TransactionEditHistory
from .user import User, UserProfile

__all__ = [
    "Base",
    "User",
    "UserProfile",
    "Transaction",
    "TransactionEditHistory",
    "Achievement",
    "UserAchievement",
    "UserStreak",
    "DailyChallenge",
    "UserChallenge",
    "UserPoints",
    "UserFinancialMetrics",
    "UserCashFlowData",
    "UserSubscriptions",
    "UserCategorySpending",
    "UserMonthlyTrends",
    "UserDevice",
    "Notification",
    "TrustedDevice",
]

# Note: Relationships between Transaction and User are not set up here
# because User is managed by Supabase auth schema
# The Transaction.user and User.transactions relationships should not be defined

# Achievement relationships
User.user_achievements = relationship("UserAchievement", back_populates="user")
User.user_streaks = relationship("UserStreak", back_populates="user")
User.user_challenges = relationship("UserChallenge", back_populates="user")
User.user_points = relationship("UserPoints", back_populates="user", uselist=False)

UserAchievement.user = relationship("User", back_populates="user_achievements")
UserAchievement.achievement = relationship(
    "Achievement", back_populates="user_achievements"
)

UserStreak.user = relationship("User", back_populates="user_streaks")

UserChallenge.user = relationship("User", back_populates="user_challenges")
UserChallenge.challenge = relationship(
    "DailyChallenge", back_populates="user_challenges"
)

UserPoints.user = relationship("User", back_populates="user_points", uselist=False)

Achievement.user_achievements = relationship(
    "UserAchievement", back_populates="achievement"
)
DailyChallenge.user_challenges = relationship(
    "UserChallenge", back_populates="challenge"
)

# Financial metrics relationships
User.financial_metrics = relationship("UserFinancialMetrics", back_populates="user")
User.cash_flow_data = relationship("UserCashFlowData", back_populates="user")
User.subscriptions = relationship("UserSubscriptions", back_populates="user")
User.category_spending = relationship("UserCategorySpending", back_populates="user")
User.monthly_trends = relationship("UserMonthlyTrends", back_populates="user")

# Security relationships are already defined in user.py
# TrustedDevice.user is defined in the device model file
