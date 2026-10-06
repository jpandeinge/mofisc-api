# migrations/versions/016_add_confidence_column.py

"""Add confidence column to transactions table for ML model assessment

Revision ID: 016_add_confidence_column
Revises: 015_fix_statement_metadata_column_names
Create Date: 2025-12-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "016_add_confidence_column"
down_revision = (
    "015_fix_statement_names"  # Must match the actual revision ID in migration 015
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add confidence column to store ML classification confidence scores"""
    # Add confidence column to transactions table
    # NUMERIC(5,4) allows values from 0.0000 to 1.0000 (e.g., 0.9567)
    op.add_column(
        "transactions",
        sa.Column(
            "confidence",
            sa.Numeric(5, 4),
            nullable=True,  # Nullable for existing records
            comment="ML model confidence score for category classification (0.0 to 1.0)",
        ),
    )

    # Create index for confidence to enable queries like "low confidence transactions"
    op.create_index(
        "idx_transactions_confidence",
        "transactions",
        ["confidence"],
        postgresql_where=sa.text(
            "confidence IS NOT NULL"
        ),  # Partial index for better performance
    )

    # Create composite index for category + confidence for model assessment queries
    op.create_index(
        "idx_transactions_category_confidence",
        "transactions",
        ["category", "confidence"],
        postgresql_where=sa.text("confidence IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove confidence column and related indexes"""
    # Drop indexes first
    op.drop_index("idx_transactions_category_confidence", table_name="transactions")
    op.drop_index("idx_transactions_confidence", table_name="transactions")

    # Drop column
    op.drop_column("transactions", "confidence")
