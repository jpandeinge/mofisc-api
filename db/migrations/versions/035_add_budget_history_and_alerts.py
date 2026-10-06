"""Add budget history and alert state tracking

Revision ID: 035_add_budget_history_and_alerts
Revises: 034_expand_alembic_version
Create Date: 2026-02-16

This migration adds:
1. budgets_history table for auditing budget changes
2. budget_alert_states table for tracking budget alert notifications
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "035_add_budget_history_and_alerts"
down_revision = "034_expand_alembic_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create budgets_history table
    op.create_table(
        "budgets_history",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("budget_id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("alert_threshold", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column(
            "event_timestamp",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")
        ),
        sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_budgets_history_budget_id", "budgets_history", ["budget_id"])
    op.create_index("idx_budgets_history_user_id", "budgets_history", ["user_id"])
    op.create_index("idx_budgets_history_event_type", "budgets_history", ["event_type"])

    # Enable RLS for budgets_history
    op.execute("ALTER TABLE budgets_history ENABLE ROW LEVEL SECURITY;")

    # Policies for budgets_history
    op.execute(
        """
        CREATE POLICY "Service role has full access on budgets_history"
        ON budgets_history USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own budgets history"
        ON budgets_history FOR SELECT USING (auth.uid() = user_id);
        """
    )

    # Create trigger to record budget history
    op.execute(
        """
        CREATE OR REPLACE FUNCTION record_budget_history()
        RETURNS TRIGGER AS $$
        DECLARE
            event_type VARCHAR;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                event_type := 'created';
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.is_active = true AND NEW.is_active = false THEN
                    event_type := 'deleted';
                ELSE
                    event_type := 'updated';
                END IF;
            END IF;

            INSERT INTO budgets_history (
                budget_id, user_id, category, amount, period, start_date, end_date,
                alert_threshold, is_active, event_type, metadata
            ) VALUES (
                NEW.id, NEW.user_id, NEW.category, NEW.amount, NEW.period, NEW.start_date,
                NEW.end_date, NEW.alert_threshold, NEW.is_active, event_type,
                CASE
                    WHEN TG_OP = 'UPDATE' THEN jsonb_build_object('old', to_jsonb(OLD), 'new', to_jsonb(NEW))
                    ELSE jsonb_build_object('new', to_jsonb(NEW))
                END
            );

            RETURN NEW;
        END;
        $$ language 'plpgsql';
        """
    )

    op.execute(
        """
        CREATE TRIGGER budgets_history_trigger
        AFTER INSERT OR UPDATE ON budgets
        FOR EACH ROW
        EXECUTE FUNCTION record_budget_history();
        """
    )

    # Create budget_alert_states table
    op.create_table(
        "budget_alert_states",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("budget_id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("last_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column(
            "last_alert_threshold", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_alerted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "last_status IN ('ok', 'warning', 'exceeded')",
            name="budget_alert_status_check",
        ),
    )

    op.create_index(
        "idx_budget_alert_states_user_id", "budget_alert_states", ["user_id"]
    )
    op.create_index(
        "idx_budget_alert_states_budget_id",
        "budget_alert_states",
        ["budget_id"],
        unique=True,
    )

    # Enable RLS for budget_alert_states
    op.execute("ALTER TABLE budget_alert_states ENABLE ROW LEVEL SECURITY;")

    op.execute(
        """
        CREATE POLICY "Service role has full access on budget_alert_states"
        ON budget_alert_states USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own budget alert states"
        ON budget_alert_states FOR SELECT USING (auth.uid() = user_id);
        """
    )

    # Create updated_at trigger for budget_alert_states
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_budget_alert_states_updated_at()
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
        CREATE TRIGGER update_budget_alert_states_updated_at
        BEFORE UPDATE ON budget_alert_states
        FOR EACH ROW
        EXECUTE FUNCTION update_budget_alert_states_updated_at();
        """
    )


def downgrade() -> None:
    # Drop budget_alert_states triggers and functions
    op.execute(
        "DROP TRIGGER IF EXISTS update_budget_alert_states_updated_at ON budget_alert_states;"
    )
    op.execute("DROP FUNCTION IF EXISTS update_budget_alert_states_updated_at();")

    # Drop RLS policies for budget_alert_states
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own budget alert states" ON budget_alert_states;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on budget_alert_states" ON budget_alert_states;'
    )

    op.execute("ALTER TABLE budget_alert_states DISABLE ROW LEVEL SECURITY;")
    op.drop_index("idx_budget_alert_states_budget_id")
    op.drop_index("idx_budget_alert_states_user_id")
    op.drop_table("budget_alert_states")

    # Drop budgets_history trigger and function
    op.execute("DROP TRIGGER IF EXISTS budgets_history_trigger ON budgets;")
    op.execute("DROP FUNCTION IF EXISTS record_budget_history();")

    # Drop RLS policies for budgets_history
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own budgets history" ON budgets_history;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on budgets_history" ON budgets_history;'
    )

    op.execute("ALTER TABLE budgets_history DISABLE ROW LEVEL SECURITY;")
    op.drop_index("idx_budgets_history_event_type")
    op.drop_index("idx_budgets_history_user_id")
    op.drop_index("idx_budgets_history_budget_id")
    op.drop_table("budgets_history")
