# migrations/versions/005_add_calculator_history.py

"""Add calculator history tracking

Revision ID: 005_add_calculator_history
Revises: 004_add_goals_tables
Create Date: 2025-03-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "005_add_calculator_history"
down_revision = "004_add_goals_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create calculator_history table
    op.create_table(
        "calculator_history",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("calculator_type", sa.String(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("outputs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index("idx_calculator_history_user_id", "calculator_history", ["user_id"])
    op.create_index(
        "idx_calculator_history_calculator_type",
        "calculator_history",
        ["calculator_type"],
    )
    op.create_index(
        "idx_calculator_history_created_at", "calculator_history", ["created_at"]
    )

    # Enable RLS
    op.execute("ALTER TABLE calculator_history ENABLE ROW LEVEL SECURITY;")

    # Create RLS policies
    op.execute(
        """
        CREATE POLICY "Service role has full access on calculator_history"
        ON calculator_history USING (true) WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own calculator history"
        ON calculator_history FOR SELECT USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert their own calculator history"
        ON calculator_history FOR INSERT WITH CHECK (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    # Drop RLS policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own calculator history" ON calculator_history;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own calculator history" ON calculator_history;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on calculator_history" ON calculator_history;'
    )

    # Disable RLS
    op.execute("ALTER TABLE calculator_history DISABLE ROW LEVEL SECURITY;")

    # Drop indexes
    op.drop_index("idx_calculator_history_created_at")
    op.drop_index("idx_calculator_history_calculator_type")
    op.drop_index("idx_calculator_history_user_id")

    # Drop table
    op.drop_table("calculator_history")
