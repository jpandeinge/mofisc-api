# api/app/models/transaction.py
import uuid

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    statement_id = Column(String, nullable=False)
    bank_name = Column(String, nullable=False)
    transaction_date = Column(Date, nullable=False)
    transaction_description = Column(Text, nullable=False)
    transaction_amount = Column(Numeric(15, 2), nullable=False)
    transaction_type = Column(String, nullable=False)
    balance = Column(Numeric(15, 2), nullable=False)
    category = Column(String)
    confidence = Column(Numeric(5, 4), nullable=True)

    # Merchant name fields (migration 023)
    merchant_name = Column(Text, nullable=True)
    original_merchant_name = Column(Text, nullable=True)
    original_category = Column(String, nullable=True)

    # Edit tracking fields (migration 023)
    user_edited_fields = Column(
        JSONB, nullable=False, server_default=func.cast("[]", JSONB)
    )
    last_edited_at = Column(DateTime(timezone=True), nullable=True)
    edit_count = Column(Integer, nullable=False, server_default="0")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship to edit history
    edit_history = relationship(
        "TransactionEditHistory",
        back_populates="transaction",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_transactions_statement_id", "statement_id"),
        Index("idx_transactions_date", "transaction_date"),
        Index("idx_transactions_user_date", "user_id", "transaction_date"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "statement_id": self.statement_id,
            "user_id": str(self.user_id),
            "bank_name": self.bank_name,
            "transaction_date": self.transaction_date.isoformat(),
            "transaction_description": self.transaction_description,
            "transaction_amount": float(self.transaction_amount),
            "transaction_type": self.transaction_type,
            "balance": float(self.balance),
            "category": self.category,
            "confidence": (
                float(self.confidence) if self.confidence is not None else None
            ),
            "merchant_name": self.merchant_name,
            "original_merchant_name": self.original_merchant_name,
            "original_category": self.original_category,
            "user_edited_fields": self.user_edited_fields or [],
            "last_edited_at": (
                self.last_edited_at.isoformat() if self.last_edited_at else None
            ),
            "edit_count": self.edit_count or 0,
        }

    def is_credit(self):
        return self.transaction_type.lower() == "credit"

    def is_debit(self):
        return self.transaction_type.lower() == "debit"
