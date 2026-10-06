# migrations/versions/027_add_device_fingerprinting.py

"""Add device fingerprinting and trust management

Revision ID: 027_add_device_fingerprinting
Revises: 026_add_subscription_management
Create Date: 2026-01-31
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "027_add_device_fingerprinting"
down_revision = "026_add_subscription_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create trusted_devices table
    op.create_table(
        "trusted_devices",
        sa.Column(
            "id",
            postgresql.UUID(),
            nullable=False,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("browser", sa.String(length=100), nullable=True),
        sa.Column("os", sa.String(length=100), nullable=True),
        sa.Column("is_trusted", sa.Boolean(), server_default="false"),
        sa.Column(
            "last_used_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create index for faster lookups
    op.create_index("idx_trusted_devices_user_id", "trusted_devices", ["user_id"])
    op.create_index(
        "idx_trusted_devices_fingerprint", "trusted_devices", ["fingerprint"]
    )
    op.create_index(
        "idx_trusted_devices_user_fingerprint",
        "trusted_devices",
        ["user_id", "fingerprint"],
    )

    # Add RLS (Row Level Security) policies
    op.execute(
        """
        ALTER TABLE trusted_devices ENABLE ROW LEVEL SECURITY;

        -- Policy: Users can only see their own devices
        CREATE POLICY users_select_own_devices ON trusted_devices
            FOR SELECT
            USING (auth.uid() = user_id);

        -- Policy: Users can insert their own devices
        CREATE POLICY users_insert_own_devices ON trusted_devices
            FOR INSERT
            WITH CHECK (auth.uid() = user_id);

        -- Policy: Users can update their own devices
        CREATE POLICY users_update_own_devices ON trusted_devices
            FOR UPDATE
            USING (auth.uid() = user_id);

        -- Policy: Users can delete their own devices
        CREATE POLICY users_delete_own_devices ON trusted_devices
            FOR DELETE
            USING (auth.uid() = user_id);
    """
    )

    # Create function to update updated_at timestamp
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_trusted_devices_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER update_trusted_devices_timestamp
            BEFORE UPDATE ON trusted_devices
            FOR EACH ROW
            EXECUTE FUNCTION update_trusted_devices_updated_at();
    """
    )


def downgrade() -> None:
    # Drop trigger and function
    op.execute(
        "DROP TRIGGER IF EXISTS update_trusted_devices_timestamp ON trusted_devices;"
    )
    op.execute("DROP FUNCTION IF EXISTS update_trusted_devices_updated_at();")

    # Drop RLS policies
    op.execute(
        """
        DROP POLICY IF EXISTS users_select_own_devices ON trusted_devices;
        DROP POLICY IF EXISTS users_insert_own_devices ON trusted_devices;
        DROP POLICY IF EXISTS users_update_own_devices ON trusted_devices;
        DROP POLICY IF EXISTS users_delete_own_devices ON trusted_devices;
    """
    )

    # Drop indexes
    op.drop_index("idx_trusted_devices_user_fingerprint", "trusted_devices")
    op.drop_index("idx_trusted_devices_fingerprint", "trusted_devices")
    op.drop_index("idx_trusted_devices_user_id", "trusted_devices")

    # Drop table
    op.drop_table("trusted_devices")
