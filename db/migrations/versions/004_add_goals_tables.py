# migrations/versions/004_add_goals_tables.py

"""Add goals tables with history tracking

Revision ID: 004_add_goals_tables
Revises: 003_add_rls_policies
Create Date: 2025-03-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "004_add_goals_tables"
down_revision = "003_add_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create goals table
    op.create_table(
        "goals",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("target_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "current_amount", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('active', 'completed', 'deleted')"),
    )

    # Create goals_history table
    op.create_table(
        "goals_history",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("goal_id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("target_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("current_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "event_timestamp",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index("idx_goals_user_id", "goals", ["user_id"])
    op.create_index("idx_goals_user_status", "goals", ["user_id", "status"])
    op.create_index("idx_goals_history_goal_id", "goals_history", ["goal_id"])
    op.create_index("idx_goals_history_user_id", "goals_history", ["user_id"])
    op.create_index("idx_goals_history_event_type", "goals_history", ["event_type"])

    # Enable RLS
    op.execute("ALTER TABLE goals ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE goals_history ENABLE ROW LEVEL SECURITY;")

    # Create RLS policies for goals
    op.execute(
        """
        CREATE POLICY "Service role has full access on goals"
        ON goals USING (true) WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own goals"
        ON goals FOR SELECT USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert their own goals"
        ON goals FOR INSERT WITH CHECK (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can update their own active goals"
        ON goals FOR UPDATE USING (auth.uid() = user_id AND status <> 'deleted');
    """
    )

    # Create RLS policies for goals_history
    op.execute(
        """
        CREATE POLICY "Service role has full access on goals_history"
        ON goals_history USING (true) WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own goals history"
        ON goals_history FOR SELECT USING (auth.uid() = user_id);
    """
    )

    # Create updated_at trigger for goals
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_goals_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """
    )

    op.execute(
        """
        CREATE TRIGGER update_goals_updated_at
        BEFORE UPDATE ON goals
        FOR EACH ROW
        EXECUTE FUNCTION update_goals_updated_at();
    """
    )

    # Create trigger to automatically create history records
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

    op.execute(
        """
        CREATE TRIGGER goals_history_trigger
        AFTER INSERT OR UPDATE ON goals
        FOR EACH ROW
        EXECUTE FUNCTION record_goal_history();
    """
    )

    # Create function for adding goal contributions
    op.execute(
        """
        CREATE OR REPLACE FUNCTION add_goal_contribution(
            p_goal_id UUID,
            p_amount NUMERIC(15, 2)
        )
        RETURNS goals AS $$
        DECLARE
            goal_record goals;
        BEGIN
            -- Update the goal's current amount
            UPDATE goals
            SET
                current_amount = current_amount + p_amount,
                status = CASE
                            WHEN current_amount + p_amount >= target_amount THEN 'completed'
                            ELSE status
                         END
            WHERE id = p_goal_id
            RETURNING * INTO goal_record;

            -- Record the contribution in history
            INSERT INTO goals_history (
                goal_id, user_id, name, target_amount, current_amount,
                deadline, category, color, status, event_type, metadata
            ) VALUES (
                goal_record.id, goal_record.user_id, goal_record.name,
                goal_record.target_amount, goal_record.current_amount,
                goal_record.deadline, goal_record.category, goal_record.color,
                goal_record.status, 'contribution',
                jsonb_build_object('contribution_amount', p_amount)
            );

            RETURN goal_record;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """
    )


def downgrade() -> None:
    # Drop function and triggers
    op.execute("DROP FUNCTION IF EXISTS add_goal_contribution(UUID, NUMERIC);")
    op.execute("DROP TRIGGER IF EXISTS goals_history_trigger ON goals;")
    op.execute("DROP FUNCTION IF EXISTS record_goal_history();")
    op.execute("DROP TRIGGER IF EXISTS update_goals_updated_at ON goals;")
    op.execute("DROP FUNCTION IF EXISTS update_goals_updated_at();")

    # Drop RLS policies for goals_history
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own goals history" ON goals_history;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on goals_history" ON goals_history;'
    )

    # Drop RLS policies for goals
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own active goals" ON goals;'
    )
    op.execute('DROP POLICY IF EXISTS "Users can insert their own goals" ON goals;')
    op.execute('DROP POLICY IF EXISTS "Users can view their own goals" ON goals;')
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on goals" ON goals;'
    )

    # Disable RLS
    op.execute("ALTER TABLE goals_history DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE goals DISABLE ROW LEVEL SECURITY;")

    # Drop indexes
    op.drop_index("idx_goals_history_event_type")
    op.drop_index("idx_goals_history_user_id")
    op.drop_index("idx_goals_history_goal_id")
    op.drop_index("idx_goals_user_status")
    op.drop_index("idx_goals_user_id")

    # Drop tables
    op.drop_table("goals_history")
    op.drop_table("goals")
