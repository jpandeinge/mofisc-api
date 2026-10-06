# api/app/models/transaction_edit_history.py
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class TransactionEditHistory(Base):
    """
    Audit trail for all user edits to transaction fields.
    Follows Revolut-style transaction edit tracking for compliance and UX.
    """

    __tablename__ = "transaction_edit_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(UUID(as_uuid=True), nullable=False)
    field_name = Column(Text, nullable=False)  # 'category' or 'merchant_name'
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    edited_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Security audit fields
    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)

    # Relationship to transaction
    transaction = relationship("Transaction", back_populates="edit_history")

    __table_args__ = (
        Index("idx_edit_history_transaction", "transaction_id"),
        Index("idx_edit_history_user", "user_id", "edited_at"),
        Index("idx_edit_history_edited_at", "edited_at"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "transaction_id": str(self.transaction_id),
            "user_id": str(self.user_id),
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "edited_at": self.edited_at.isoformat() if self.edited_at else None,
        }
