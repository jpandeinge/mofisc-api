# api/app/schemas/api_key.py

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scopes: List[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: List[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str
