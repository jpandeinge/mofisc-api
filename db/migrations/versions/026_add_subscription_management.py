"""Add subscription management columns for pause/resume/delete/rename

Revision ID: 026_add_subscription_management
Revises: 025_batch_edit_enhance
Create Date: 2026-01-24

Adds comprehensive subscription management features:
- user_display_name: Custom name that overrides auto-sync
- is_paused: Pause/resume state
- paused_at, paused_by_user_id: Audit trail for pause actions
- resume_date: Optional auto-resume date
- soft_deleted_at: 30-day recovery window for deleted subscriptions
- last_synced_merchant_name: Latest merchant name from edited transactions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "026_add_subscription_management"
down_revision = "025_batch_edit_enhance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add subscription management columns to user_subscriptions table.
    Enables pause/resume, soft delete, custom naming, and merchant name sync.
    """

    # Add new columns for subscription management
    op.execute(
        """
        ALTER TABLE user_subscriptions
        ADD COLUMN IF NOT EXISTS user_display_name TEXT,
        ADD COLUMN IF NOT EXISTS is_paused BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS paused_by_user_id UUID REFERENCES auth.users(id),
        ADD COLUMN IF NOT EXISTS resume_date DATE,
        ADD COLUMN IF NOT EXISTS soft_deleted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS last_synced_merchant_name TEXT;
    """
    )

    # Create indexes for performance
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_subscriptions_paused
        ON user_subscriptions(user_id, is_paused)
        WHERE is_active = true;
    """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_subscriptions_soft_deleted
        ON user_subscriptions(soft_deleted_at)
        WHERE soft_deleted_at IS NOT NULL;
    """
    )

    # Update RLS policy to exclude soft-deleted subscriptions
    # Drop existing SELECT policy and recreate with soft delete check
    op.execute(
        """
        DROP POLICY IF EXISTS "Users can view their own user_subscriptions" ON user_subscriptions;
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own user_subscriptions"
        ON user_subscriptions FOR SELECT
        USING (
            auth.uid() = user_id
            AND (soft_deleted_at IS NULL OR soft_deleted_at > NOW())
        );
    """
    )


def downgrade() -> None:
    """Remove subscription management columns."""

    # Restore original RLS policy
    op.execute(
        """
        DROP POLICY IF EXISTS "Users can view their own user_subscriptions" ON user_subscriptions;
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own user_subscriptions"
        ON user_subscriptions FOR SELECT
        USING (auth.uid() = user_id);
    """
    )

    # Drop indexes
    op.execute("DROP INDEX IF EXISTS idx_subscriptions_paused;")
    op.execute("DROP INDEX IF EXISTS idx_subscriptions_soft_deleted;")

    # Remove columns
    op.execute(
        """
        ALTER TABLE user_subscriptions
        DROP COLUMN IF EXISTS user_display_name,
        DROP COLUMN IF EXISTS is_paused,
        DROP COLUMN IF EXISTS paused_at,
        DROP COLUMN IF EXISTS paused_by_user_id,
        DROP COLUMN IF EXISTS resume_date,
        DROP COLUMN IF EXISTS soft_deleted_at,
        DROP COLUMN IF EXISTS last_synced_merchant_name;
    """
    )
