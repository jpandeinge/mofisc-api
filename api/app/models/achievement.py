# api/app/models/achievement.py
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class Achievement(Base):
    __tablename__ = "achievements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    category = Column(String, nullable=False)
    level = Column(Integer, nullable=False, default=1)
    points = Column(Integer, nullable=False)
    icon = Column(String, nullable=False)
    target_value = Column(Float, nullable=False)
    trigger_event = Column(String, nullable=False)
    is_repeatable = Column(Boolean, nullable=False, default=False)
    is_hidden = Column(Boolean, nullable=False, default=False)
    requires_achievement_id = Column(
        UUID(as_uuid=True), ForeignKey("achievements.id"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user_achievements = relationship("UserAchievement", back_populates="achievement")

    def to_dict(self):
        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "level": self.level,
            "points": self.points,
            "icon": self.icon,
            "target_value": float(self.target_value),
            "trigger_event": self.trigger_event,
            "is_repeatable": self.is_repeatable,
            "is_hidden": self.is_hidden,
            "requires_achievement_id": (
                str(self.requires_achievement_id)
                if self.requires_achievement_id
                else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    achievement_id = Column(
        UUID(as_uuid=True), ForeignKey("achievements.id"), nullable=False
    )
    progress = Column(Float, nullable=False, default=0)
    current_value = Column(Float, nullable=False, default=0)
    is_completed = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    achievement = relationship("Achievement", back_populates="user_achievements")
    user = relationship("User", back_populates="user_achievements")

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "achievement_id": str(self.achievement_id),
            "progress": float(self.progress),
            "current_value": float(self.current_value),
            "is_completed": self.is_completed,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserStreak(Base):
    __tablename__ = "user_streaks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    streak_type = Column(String, nullable=False)
    current_count = Column(Integer, nullable=False, default=0)
    highest_count = Column(Integer, nullable=False, default=0)
    last_tracked_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="user_streaks")

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "streak_type": self.streak_type,
            "current_count": self.current_count,
            "highest_count": self.highest_count,
            "last_tracked_date": (
                self.last_tracked_date.isoformat() if self.last_tracked_date else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DailyChallenge(Base):
    __tablename__ = "daily_challenges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    challenge_type = Column(String, nullable=False)
    points = Column(Integer, nullable=False)
    target_value = Column(Float, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user_challenges = relationship("UserChallenge", back_populates="challenge")

    def to_dict(self):
        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "challenge_type": self.challenge_type,
            "points": self.points,
            "target_value": float(self.target_value),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserChallenge(Base):
    __tablename__ = "user_challenges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    challenge_id = Column(
        UUID(as_uuid=True), ForeignKey("daily_challenges.id"), nullable=False
    )
    progress = Column(Float, nullable=False, default=0)
    is_completed = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    challenge = relationship("DailyChallenge", back_populates="user_challenges")
    user = relationship("User", back_populates="user_challenges")

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "challenge_id": str(self.challenge_id),
            "progress": float(self.progress),
            "is_completed": self.is_completed,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserPoints(Base):
    __tablename__ = "user_points"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    points = Column(Integer, nullable=False, default=0)
    level = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="user_points", uselist=False)

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "points": self.points,
            "level": self.level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
