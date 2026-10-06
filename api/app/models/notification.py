# api/app/models/notification.py
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base
from ..services.notifications.base import NotificationType


class UserDevice(Base):
    """Store user device tokens for push notifications."""

    __tablename__ = "user_devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    device_token = Column(String, nullable=False, unique=True)
    device_type = Column(String)  # ios, android, web
    device_name = Column(String)  # e.g., "iPhone 14", "Chrome on MacOS"
    is_active = Column(Boolean, default=True)
    last_used = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", backref="devices")


class Notification(Base):
    """Store notification history."""

    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    notification_type = Column(SQLEnum(NotificationType), nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    data = Column(JSONB, server_default="{}")

    # Delivery tracking
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime(timezone=True))

    # Channel delivery status
    delivered_via = Column(
        JSONB, server_default="{}"
    )  # {email: true, push: false, sms: true}

    # Metadata
    action_url = Column(String)  # Optional URL for notification action
    icon = Column(String)  # Icon name or URL
    dedupe_key = Column(String)  # Optional dedupe key for idempotent sends
    priority = Column(String, default="medium")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", backref="notifications")
