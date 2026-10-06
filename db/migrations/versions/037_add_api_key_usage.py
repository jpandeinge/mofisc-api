# migrations/versions/037_add_api_key_usage.py

"""Add API key usage tracking

Revision ID: 037_add_api_key_usage
Revises: 036_add_api_keys
Create Date: 2026-02-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "037_add_api_key_usage"
down_revision = "036_add_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key_usage",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("api_key_id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("request_path", sa.String(200), nullable=False),
        sa.Column("request_method", sa.String(10), nullable=False),
        sa.Column("endpoint_group", sa.String(60), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=False),
        sa.Column(
            "success", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_api_key_usage_api_key", "api_key_usage", ["api_key_id"])
    op.create_index("idx_api_key_usage_user", "api_key_usage", ["user_id"])
    op.create_index("idx_api_key_usage_created_at", "api_key_usage", ["created_at"])
    op.create_index("idx_api_key_usage_endpoint", "api_key_usage", ["endpoint_group"])

    op.execute("ALTER TABLE api_key_usage ENABLE ROW LEVEL SECURITY;")

    op.execute(
        """
        CREATE POLICY "Service role has full access on api_key_usage"
        ON api_key_usage USING (true) WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own api_key_usage"
        ON api_key_usage FOR SELECT USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    op.drop_index("idx_api_key_usage_endpoint", table_name="api_key_usage")
    op.drop_index("idx_api_key_usage_created_at", table_name="api_key_usage")
    op.drop_index("idx_api_key_usage_user", table_name="api_key_usage")
    op.drop_index("idx_api_key_usage_api_key", table_name="api_key_usage")
    op.drop_table("api_key_usage")
