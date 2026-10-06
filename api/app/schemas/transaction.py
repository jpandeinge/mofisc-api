# api/app/schemas/transaction.py

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ..utils.text_formatting import to_title_case


class TransactionBase(BaseModel):
    transaction_date: date
    description: str = Field(..., min_length=1)
    amount: Decimal = Field(..., decimal_places=2)
    type: str = Field(..., pattern="^(credit|debit)$")
    balance: Decimal = Field(..., decimal_places=2)
    category: Optional[str] = None


class TransactionCreate(TransactionBase):
    statement_id: Optional[str] = None


class TransactionUpdate(BaseModel):
    """Schema for updating transaction fields (category and merchant_name only)"""

    category: Optional[str] = Field(None, max_length=100)
    merchant_name: Optional[str] = Field(None, max_length=500)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        if v is not None and len(v.strip()) == 0:
            raise ValueError("Category cannot be empty")
        # Apply Title Case formatting
        return to_title_case(v) if v else v

    @field_validator("merchant_name")
    @classmethod
    def validate_merchant_name(cls, v):
        if v is not None and len(v.strip()) == 0:
            raise ValueError("Merchant name cannot be empty")
        # Apply Title Case formatting
        return to_title_case(v) if v else v


class BatchUpdateRequest(BaseModel):
    """Schema for batch updating multiple transactions"""

    transaction_ids: List[str] = Field(..., min_length=1, max_length=100)
    updates: TransactionUpdate = Field(...)


class TransactionEditHistoryResponse(BaseModel):
    """Schema for transaction edit history entries"""

    id: UUID
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    edited_at: datetime

    class Config:
        from_attributes = True


class Transaction(TransactionBase):
    id: UUID
    user_id: UUID
    statement_id: Optional[str]
    created_at: date
    updated_at: Optional[date]
    confidence: Optional[float] = None

    # Merchant name fields (migration 023)
    merchant_name: Optional[str] = None
    original_merchant_name: Optional[str] = None
    original_category: Optional[str] = None

    # Edit tracking fields (migration 023)
    user_edited_fields: List[str] = Field(default_factory=list)
    last_edited_at: Optional[datetime] = None
    edit_count: int = 0

    class Config:
        from_attributes = True
