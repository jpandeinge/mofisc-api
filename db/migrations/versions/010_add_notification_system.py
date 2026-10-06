"""Add notification system with user devices and notification history

Revision ID: 010_add_notification_system
Revises: 009_challenges_rls
Create Date: 2025-11-07
"""

# revision identifiers, used by Alembic.
revision = "010_add_notification_system"
down_revision = "009_challenges_rls"
branch_labels = None
depends_on = None

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


def upgrade():
    # Create user_devices table for FCM tokens
    op.create_table(
        "user_devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("device_token", sa.String(), nullable=False),
        sa.Column("device_type", sa.String(), nullable=True),
        sa.Column("device_name", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column(
            "last_used", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"]),
        sa.UniqueConstraint("device_token", name="uq_device_token"),
    )

    # Create index for user_id lookups
    op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"])

    # Create notification type enum if it doesn't exist
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE notificationtype AS ENUM (
                'transaction_created',
                'budget_exceeded',
                'budget_warning',
                'goal_milestone',
                'goal_completed',
                'payment_due',
                'security_alert',
                'account_verification',
                'password_changed',
                'login_alert',
                'achievement_unlocked',
                'level_up',
                'streak_milestone',
                'badge_earned',
                'challenge_completed',
                'leaderboard_position',
                'daily_challenge_available',
                'reward_earned'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """
    )

    # Create notifications table (using notificationtype column directly instead of sa.Enum)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
            notification_type notificationtype NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            data JSONB DEFAULT '{}'::jsonb,
            is_read BOOLEAN NOT NULL DEFAULT false,
            read_at TIMESTAMPTZ,
            delivered_via JSONB DEFAULT '{}'::jsonb,
            action_url TEXT,
            icon TEXT,
            priority VARCHAR(20) NOT NULL DEFAULT 'medium',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );
    """
    )

    # Create indexes for efficient querying
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index(
        "ix_notifications_user_read", "notifications", ["user_id", "is_read"]
    )

    # Enable RLS on both tables
    op.execute("ALTER TABLE user_devices ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;")

    # Add RLS policies for user_devices
    op.execute(
        """
        -- Service role has full access to user_devices
        CREATE POLICY "Service role has full access on user_devices"
        ON user_devices USING (true) WITH CHECK (true);

        -- Users can view their own devices
        CREATE POLICY "Users can view their own devices"
        ON user_devices FOR SELECT USING (auth.uid() = user_id);

        -- Users can insert their own devices
        CREATE POLICY "Users can insert their own devices"
        ON user_devices FOR INSERT WITH CHECK (auth.uid() = user_id);

        -- Users can update their own devices
        CREATE POLICY "Users can update their own devices"
        ON user_devices FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

        -- Users can delete their own devices
        CREATE POLICY "Users can delete their own devices"
        ON user_devices FOR DELETE USING (auth.uid() = user_id);
    """
    )

    # Add RLS policies for notifications
    op.execute(
        """
        -- Service role has full access to notifications
        CREATE POLICY "Service role has full access on notifications"
        ON notifications USING (true) WITH CHECK (true);

        -- Users can view their own notifications
        CREATE POLICY "Users can view their own notifications"
        ON notifications FOR SELECT USING (auth.uid() = user_id);

        -- Users can update their own notifications (for marking as read)
        CREATE POLICY "Users can update their own notifications"
        ON notifications FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    """
    )


def downgrade():
    # Drop RLS policies for notifications
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own notifications" ON notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own notifications" ON notifications;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on notifications" ON notifications;'
    )

    # Drop RLS policies for user_devices
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own devices" ON user_devices;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own devices" ON user_devices;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own devices" ON user_devices;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own devices" ON user_devices;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_devices" ON user_devices;'
    )

    # Drop indexes for notifications
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")

    # Drop notifications table
    op.drop_table("notifications")

    # Drop notification type enum
    op.execute("DROP TYPE notificationtype;")

    # Drop index for user_devices
    op.drop_index("ix_user_devices_user_id", table_name="user_devices")

    # Drop user_devices table
    op.drop_table("user_devices")
