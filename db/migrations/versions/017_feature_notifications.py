"""Add feature notifications table for tracking user notification preferences for upcoming features

Revision ID: 017_feature_notifications
Revises: 016_add_confidence_column
Create Date: 2025-12-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "017_feature_notifications"
down_revision = "016_add_confidence_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create feature_notifications table for tracking user interest in upcoming features.
    Simplified version without RLS - get basic table working first.
    """

    # Create table using raw SQL with IF NOT EXISTS for idempotency
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            email VARCHAR(255) NOT NULL,
            feature_type VARCHAR(100) NOT NULL,
            feature_identifier VARCHAR(255),
            metadata JSONB DEFAULT '{}'::jsonb,
            is_notified BOOLEAN NOT NULL DEFAULT false,
            notified_at TIMESTAMPTZ,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_feature_notifications_user_feature
                UNIQUE (user_id, feature_type, feature_identifier)
        );
    """
    )

    # Create indexes idempotently
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feature_notifications_user_id
        ON feature_notifications(user_id);
    """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feature_notifications_feature_type
        ON feature_notifications(feature_type, feature_identifier);
    """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feature_notifications_user_feature
        ON feature_notifications(user_id, feature_type, is_active);
    """
    )

    # Create trigger function for updated_at (idempotent with CREATE OR REPLACE)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_feature_notifications_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """
    )

    # Create trigger (idempotent)
    op.execute(
        """
        DROP TRIGGER IF EXISTS update_feature_notifications_updated_at ON feature_notifications;
        CREATE TRIGGER update_feature_notifications_updated_at
        BEFORE UPDATE ON feature_notifications
        FOR EACH ROW
        EXECUTE FUNCTION update_feature_notifications_updated_at();
    """
    )


def downgrade() -> None:
    """
    Remove feature_notifications table and all related objects.
    """

    # Drop trigger and function
    op.execute(
        "DROP TRIGGER IF EXISTS update_feature_notifications_updated_at ON feature_notifications;"
    )
    op.execute("DROP FUNCTION IF EXISTS update_feature_notifications_updated_at();")

    # Drop indexes
    op.execute("DROP INDEX IF EXISTS idx_feature_notifications_user_feature;")
    op.execute("DROP INDEX IF EXISTS idx_feature_notifications_feature_type;")
    op.execute("DROP INDEX IF EXISTS idx_feature_notifications_user_id;")

    # Drop table (CASCADE will handle constraints)
    op.execute("DROP TABLE IF EXISTS feature_notifications CASCADE;")
