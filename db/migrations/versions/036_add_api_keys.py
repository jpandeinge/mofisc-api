# migrations/versions/036_add_api_keys.py

"""Add API keys for developer access

Revision ID: 036_add_api_keys
Revises: 035_add_budget_history_and_alerts
Create Date: 2026-02-16

This migration creates the api_keys table used to grant scoped developer
access via API keys. Keys are hashed and never stored in plaintext.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "036_add_api_keys"
down_revision = "035_add_budget_history_and_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("idx_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.create_index("idx_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;")

    op.execute(
        """
        CREATE POLICY "Service role has full access on api_keys"
        ON api_keys USING (true) WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own api_keys"
        ON api_keys FOR SELECT USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert their own api_keys"
        ON api_keys FOR INSERT WITH CHECK (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can update their own api_keys"
        ON api_keys FOR UPDATE USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can delete their own api_keys"
        ON api_keys FOR DELETE USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    op.drop_index("idx_api_keys_key_hash", table_name="api_keys")
    op.drop_index("idx_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("idx_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
