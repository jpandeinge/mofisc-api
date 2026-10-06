# api/app/models/user.py
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}  # Map to Supabase auth.users table

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    profile = relationship("UserProfile", back_populates="user", uselist=False)
    # Remove: transactions = relationship("Transaction", back_populates="user")

    # Achievement system relationships
    user_achievements = relationship("UserAchievement", back_populates="user")
    user_streaks = relationship("UserStreak", back_populates="user")
    user_challenges = relationship("UserChallenge", back_populates="user")
    user_points = relationship("UserPoints", back_populates="user", uselist=False)

    # Financial metrics relationships
    financial_metrics = relationship("UserFinancialMetrics", back_populates="user")
    cash_flow_data = relationship("UserCashFlowData", back_populates="user")
    subscriptions = relationship("UserSubscriptions", back_populates="user")
    category_spending = relationship("UserCategorySpending", back_populates="user")
    monthly_trends = relationship("UserMonthlyTrends", back_populates="user")

    # Security relationships
    trusted_devices = relationship("TrustedDevice", back_populates="user")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), primary_key=True)
    user_type = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    phone = Column(String)
    profile_picture = Column(String)
    preferences = Column(JSONB, server_default="{}")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    organisation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="SET NULL"),
        nullable=True,
    )

    user = relationship("User", back_populates="profile")
