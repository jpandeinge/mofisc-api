"""Add missing notification types to enum

Revision ID: 022_add_notif_types
Revises: 021_fix_billing_rls
Create Date: 2026-01-11

The Python code added new notification types that were never added to the database enum:
- transaction_updated
- subscription_due_tomorrow
- subscription_due_week
- subscription_charged
- system_alert
"""

# revision identifiers
revision = "022_add_notif_types"
down_revision = "021_fix_billing_rls"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    """
    Add missing notification types to the notificationtype enum.
    PostgreSQL allows adding values to enums with ALTER TYPE.
    """

    # Add each missing enum value
    # Using IF NOT EXISTS pattern for idempotency
    missing_types = [
        "transaction_updated",
        "subscription_due_tomorrow",
        "subscription_due_week",
        "subscription_charged",
        "system_alert",
    ]

    for notification_type in missing_types:
        # PostgreSQL 9.1+ supports IF NOT EXISTS for enum values
        op.execute(
            f"""
            DO $$ BEGIN
                ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS '{notification_type}';
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """
        )


def downgrade() -> None:
    """
    Note: PostgreSQL does not support removing values from enums directly.
    To remove enum values, you would need to:
    1. Create a new enum type without the values
    2. Alter all columns using the old enum to use the new one
    3. Drop the old enum
    4. Rename the new enum

    For simplicity, this downgrade is a no-op. The extra enum values
    won't cause issues if not used.
    """
    pass
