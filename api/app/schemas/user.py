# api/app/schemas/user.py

from datetime import datetime
from typing import Dict, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserProfileBase(BaseModel):
    user_type: str = Field(..., pattern="^(individual|organisation)$")
    full_name: str = Field(..., min_length=1)
    email: EmailStr
    phone: Optional[str] = None
    profile_picture: Optional[str] = None
    preferences: Optional[Dict] = Field(default_factory=dict)
    organisation_id: Optional[UUID] = None


class UserProfileCreate(UserProfileBase):
    pass


class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    profile_picture: Optional[str] = None
    preferences: Optional[Dict] = None


class UserProfile(UserProfileBase):
    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class User(BaseModel):
    id: UUID
    email: EmailStr
    is_super_admin: bool
    created_at: datetime
    updated_at: Optional[datetime]
    profile: Optional[UserProfile]

    class Config:
        from_attributes = True
