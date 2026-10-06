# api/app/schemas/response.py

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

# Generic type variable for response data
DataT = TypeVar("DataT")


class APIResponse(BaseModel, Generic[DataT]):
    """
    Standardized API response wrapper following Revolut-style patterns.

    Ensures consistent response format across all endpoints with:
    - Explicit success/failure indicators
    - Type-safe data payload
    - Standardized error messaging
    - Security through controlled error exposure
    """

    success: bool
    data: Optional[DataT] = None
    message: Optional[str] = None
    error: Optional[str] = None

    class Config:
        from_attributes = True


class SuccessResponse(BaseModel, Generic[DataT]):
    """Success response wrapper."""

    success: bool = True
    data: DataT
    message: Optional[str] = None

    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Error response wrapper."""

    success: bool = False
    message: str
    error: Optional[str] = None

    class Config:
        from_attributes = True
