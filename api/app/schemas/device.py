# api/app/schemas/device.py

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TrustedDeviceBase(BaseModel):
    fingerprint: str = Field(..., min_length=32, max_length=64)
    device_name: Optional[str] = Field(None, max_length=255)
    user_agent: Optional[str] = None
    ip_address: Optional[str] = Field(None, max_length=45)
    browser: Optional[str] = Field(None, max_length=100)
    os: Optional[str] = Field(None, max_length=100)


class TrustedDeviceCreate(TrustedDeviceBase):
    pass


class TrustedDeviceUpdate(BaseModel):
    device_name: Optional[str] = Field(None, max_length=255)
    is_trusted: Optional[bool] = None


class TrustedDevice(TrustedDeviceBase):
    id: UUID
    user_id: UUID
    is_trusted: bool
    friendly_name: Optional[str] = None
    is_current_device: bool = False
    is_suspicious: bool = False

    # Location at initial registration
    city: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    region: Optional[str] = None
    location: Optional[str] = None  # Formatted as "City, Country"

    # Last login info
    last_login_ip: Optional[str] = None
    last_login_city: Optional[str] = None
    last_login_country: Optional[str] = None
    last_login_at: Optional[datetime] = None
    last_login_location: Optional[str] = None  # Formatted as "City, Country"

    # Usage stats
    login_count: int = 1

    # Timestamps
    last_used_at: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceTrustCheckRequest(BaseModel):
    fingerprint: str = Field(..., min_length=32, max_length=64)
    device_name: Optional[str] = Field(None, max_length=255)
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    browser: Optional[str] = None
    os: Optional[str] = None


class DeviceTrustCheckResponse(BaseModel):
    is_trusted: bool
    is_new_device: bool
    is_suspicious: bool = False
    device_id: Optional[UUID] = None
    friendly_name: Optional[str] = None
    location: Optional[str] = None  # Current login location
    message: str
