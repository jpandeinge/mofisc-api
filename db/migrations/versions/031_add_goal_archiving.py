# migrations/versions/031_add_goal_archiving.py

"""Add goal archiving support

Revision ID: 031_add_goal_archiving
Revises: 030_enhance_device_trust
Create Date: 2026-02-11

This migration:
1. Adds 'archived' status to goals table
2. Updates CHECK constraint to include archived status
3. Adds index for efficient archived goal filtering
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "031_add_goal_archiving"
down_revision = "030_enhance_device_trust"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing CHECK constraint
    op.execute("ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_status_check;")

    # Add the new CHECK constraint with 'archived' status
    op.execute(
        """
        ALTER TABLE goals
        ADD CONSTRAINT goals_status_check
        CHECK (status IN ('active', 'completed', 'archived', 'deleted'));
    """
    )

    # Add index for efficient filtering of archived goals
    op.create_index(
        "idx_goals_user_archived", "goals", ["user_id", "status"], unique=False
    )

    # Update the history trigger to handle archived status
    op.execute(
        """
        CREATE OR REPLACE FUNCTION record_goal_history()
        RETURNS TRIGGER AS $$
        DECLARE
            event_type VARCHAR;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                event_type := 'created';
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.status <> NEW.status THEN
                    -- Handle all status transitions
                    CASE NEW.status
                        WHEN 'completed' THEN event_type := 'completed';
                        WHEN 'archived' THEN event_type := 'archived';
                        WHEN 'deleted' THEN event_type := 'deleted';
                        WHEN 'active' THEN
                            -- Unarchiving or reactivating
                            IF OLD.status = 'archived' THEN
                                event_type := 'unarchived';
                            ELSE
                                event_type := 'reactivated';
                            END IF;
                        ELSE event_type := 'updated';
                    END CASE;
                ELSE
                    event_type := 'updated';
                END IF;
            END IF;

            INSERT INTO goals_history (
                goal_id, user_id, name, target_amount, current_amount,
                deadline, category, color, status, event_type
            ) VALUES (
                NEW.id, NEW.user_id, NEW.name, NEW.target_amount, NEW.current_amount,
                NEW.deadline, NEW.category, NEW.color, NEW.status, event_type
            );

            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """
    )


def downgrade() -> None:
    # Drop the index
    op.drop_index("idx_goals_user_archived")

    # Restore the original CHECK constraint
    op.execute("ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_status_check;")

    op.execute(
        """
        ALTER TABLE goals
        ADD CONSTRAINT goals_status_check
        CHECK (status IN ('active', 'completed', 'deleted'));
    """
    )

    # Restore the original history trigger
    op.execute(
        """
        CREATE OR REPLACE FUNCTION record_goal_history()
        RETURNS TRIGGER AS $$
        DECLARE
            event_type VARCHAR;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                event_type := 'created';
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.status <> NEW.status THEN
                    event_type := NEW.status; -- 'completed' or 'deleted'
                ELSE
                    event_type := 'updated';
                END IF;
            END IF;

            INSERT INTO goals_history (
                goal_id, user_id, name, target_amount, current_amount,
                deadline, category, color, status, event_type
            ) VALUES (
                NEW.id, NEW.user_id, NEW.name, NEW.target_amount, NEW.current_amount,
                NEW.deadline, NEW.category, NEW.color, NEW.status, event_type
            );

            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """
    )
