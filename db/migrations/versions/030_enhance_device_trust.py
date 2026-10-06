# migrations/versions/030_enhance_device_trust.py

"""Enhance device trust system with atomic upserts and location tracking

Revision ID: 030_enhance_device_trust
Revises: 029_grant_security_tables_access
Create Date: 2026-02-08

This migration:
1. Cleans up duplicate devices (keeps most recently used)
2. Adds unique constraint on (user_id, fingerprint) to prevent duplicates
3. Adds location tracking columns for initial registration and last login
4. Adds friendly_name, login_count, is_suspicious fields
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "030_enhance_device_trust"
down_revision = "029_grant_security_tables_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Clean up duplicate devices (keep the most recently used one per user/fingerprint)
    # Uses COALESCE to handle NULL last_used_at values by falling back to created_at
    op.execute(
        """
        DELETE FROM trusted_devices
        WHERE id NOT IN (
            SELECT DISTINCT ON (user_id, fingerprint) id
            FROM trusted_devices
            ORDER BY user_id, fingerprint,
                     COALESCE(last_used_at, created_at, '1970-01-01'::timestamptz) DESC
        );
    """
    )

    # Step 2: Add new columns for location tracking
    # Initial registration location
    op.add_column(
        "trusted_devices", sa.Column("city", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "trusted_devices", sa.Column("country", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "trusted_devices",
        sa.Column("country_code", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "trusted_devices", sa.Column("region", sa.String(length=100), nullable=True)
    )

    # Last login location
    op.add_column(
        "trusted_devices",
        sa.Column("last_login_ip", sa.String(length=45), nullable=True),
    )
    op.add_column(
        "trusted_devices",
        sa.Column("last_login_city", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "trusted_devices",
        sa.Column("last_login_country", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "trusted_devices",
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Additional fields
    op.add_column(
        "trusted_devices",
        sa.Column("friendly_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "trusted_devices",
        sa.Column(
            "login_count", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "trusted_devices",
        sa.Column(
            "is_suspicious",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Step 3: Add unique constraint to prevent future duplicates
    # This enables atomic upsert using ON CONFLICT
    op.create_unique_constraint(
        "uq_trusted_devices_user_fingerprint",
        "trusted_devices",
        ["user_id", "fingerprint"],
    )


def downgrade() -> None:
    # Remove unique constraint
    op.drop_constraint(
        "uq_trusted_devices_user_fingerprint", "trusted_devices", type_="unique"
    )

    # Remove new columns
    op.drop_column("trusted_devices", "is_suspicious")
    op.drop_column("trusted_devices", "login_count")
    op.drop_column("trusted_devices", "friendly_name")
    op.drop_column("trusted_devices", "last_login_at")
    op.drop_column("trusted_devices", "last_login_country")
    op.drop_column("trusted_devices", "last_login_city")
    op.drop_column("trusted_devices", "last_login_ip")
    op.drop_column("trusted_devices", "region")
    op.drop_column("trusted_devices", "country_code")
    op.drop_column("trusted_devices", "country")
    op.drop_column("trusted_devices", "city")
