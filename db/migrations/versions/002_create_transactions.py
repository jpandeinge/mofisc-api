# migrations/versions/002_create_transactions.py

"""Create transactions table

Revision ID: 002_create_transactions
Revises: 001_initial_setup
Create Date: 2025-02-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "002_create_transactions"
down_revision = "001_initial_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create transactions table
    op.create_table(
        "transactions",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("statement_id", sa.String(), nullable=False),
        sa.Column("bank_name", sa.String(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("transaction_description", sa.Text(), nullable=False),
        sa.Column("transaction_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=False),
        sa.Column("balance", sa.Numeric(15, 2), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("transaction_type IN ('credit', 'debit')"),
    )

    # Create indexes
    op.create_index("idx_transactions_user_id", "transactions", ["user_id"])
    op.create_index("idx_transactions_statement_id", "transactions", ["statement_id"])
    op.create_index("idx_transactions_date", "transactions", ["transaction_date"])
    op.create_index(
        "idx_transactions_user_date", "transactions", ["user_id", "transaction_date"]
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_transactions_user_date")
    op.drop_index("idx_transactions_date")
    op.drop_index("idx_transactions_statement_id")
    op.drop_index("idx_transactions_user_id")

    # Drop table
    op.drop_table("transactions")
