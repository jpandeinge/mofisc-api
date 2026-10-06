"""Add comprehensive security to feature_notifications table

Revision ID: 018_secure_notifications
Revises: 017_feature_notifications
Create Date: 2025-12-31
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "018_secure_notifications"
down_revision = "017_feature_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add production-grade security to feature_notifications table:
    - Foreign key constraint to auth.users
    - Row Level Security (RLS) policies
    - Following Revolut-level fintech security standards
    """

    # 1. Add foreign key constraint to auth.users (idempotent)
    # First check if constraint exists, drop if it does, then add it
    op.execute(
        """
        DO $$
        BEGIN
            -- Drop constraint if it exists
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_feature_notifications_user_id'
            ) THEN
                ALTER TABLE feature_notifications
                DROP CONSTRAINT fk_feature_notifications_user_id;
            END IF;

            -- Add the constraint
            ALTER TABLE feature_notifications
            ADD CONSTRAINT fk_feature_notifications_user_id
            FOREIGN KEY (user_id)
            REFERENCES auth.users(id)
            ON DELETE CASCADE;
        END $$;
    """
    )

    # 2. Enable Row Level Security
    op.execute("ALTER TABLE feature_notifications ENABLE ROW LEVEL SECURITY;")

    # 3. Drop existing policies if they exist (for idempotency)
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete own notifications" ON feature_notifications;'
    )

    # 4. Create RLS policies following the exact pattern from migration 003

    # Policy 1: Service role has full access (for backend operations)
    op.execute(
        """
        CREATE POLICY "Service role has full access" ON feature_notifications
        USING (true)
        WITH CHECK (true);
    """
    )

    # Policy 2: Users can only view their own notifications
    op.execute(
        """
        CREATE POLICY "Users can view own notifications" ON feature_notifications
        FOR SELECT USING (auth.uid() = user_id);
    """
    )

    # Policy 3: Users can only insert their own notifications
    # With additional check for service_role
    op.execute(
        """
        CREATE POLICY "Users can insert own notifications" ON feature_notifications
        FOR INSERT WITH CHECK (
            CASE
                WHEN current_setting('role') = 'service_role' THEN true
                WHEN auth.uid() = user_id THEN true
                ELSE false
            END
        );
    """
    )

    # Policy 4: Users can only update their own notifications
    op.execute(
        """
        CREATE POLICY "Users can update own notifications" ON feature_notifications
        FOR UPDATE USING (auth.uid() = user_id);
    """
    )

    # Policy 5: Users can only delete their own notifications
    op.execute(
        """
        CREATE POLICY "Users can delete own notifications" ON feature_notifications
        FOR DELETE USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    """
    Remove security features from feature_notifications table.
    WARNING: This will make the table insecure again.
    """

    # Drop RLS policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view own notifications" ON feature_notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access" ON feature_notifications;'
    )

    # Disable RLS
    op.execute("ALTER TABLE feature_notifications DISABLE ROW LEVEL SECURITY;")

    # Drop foreign key constraint
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_feature_notifications_user_id'
            ) THEN
                ALTER TABLE feature_notifications
                DROP CONSTRAINT fk_feature_notifications_user_id;
            END IF;
        END $$;
    """
    )
