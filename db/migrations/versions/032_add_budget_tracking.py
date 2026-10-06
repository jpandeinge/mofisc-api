# migrations/versions/032_add_budget_tracking.py

"""Add budget tracking system

Revision ID: 032_add_budget_tracking
Revises: 031_add_goal_archiving
Create Date: 2026-02-11

This migration creates a comprehensive budget tracking system:
1. budgets table - Store user budgets by category
2. Budget alerts and notifications
3. RLS policies and indexes for performance
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "032_add_budget_tracking"
down_revision = "031_add_goal_archiving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create budgets table
    op.create_table(
        "budgets",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "period",
            sa.String(20),
            nullable=False,
            server_default="monthly",
            comment="Budget period: daily, weekly, monthly, yearly",
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "alert_threshold",
            sa.Integer(),
            nullable=False,
            server_default="80",
            comment="Alert when spending reaches this percentage",
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "period IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="budget_period_check",
        ),
        sa.CheckConstraint("amount > 0", name="budget_amount_positive"),
        sa.CheckConstraint(
            "alert_threshold >= 0 AND alert_threshold <= 100",
            name="budget_threshold_valid",
        ),
    )

    # Create indexes for budgets
    op.create_index("idx_budgets_user_id", "budgets", ["user_id"])
    op.create_index("idx_budgets_user_category", "budgets", ["user_id", "category"])
    op.create_index(
        "idx_budgets_user_active",
        "budgets",
        ["user_id", "is_active"],
        unique=False,
    )

    # Enable RLS on budgets
    op.execute("ALTER TABLE budgets ENABLE ROW LEVEL SECURITY;")

    # Create RLS policies for budgets
    op.execute(
        """
        CREATE POLICY "Service role has full access on budgets"
        ON budgets USING (true) WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own budgets"
        ON budgets FOR SELECT USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert their own budgets"
        ON budgets FOR INSERT WITH CHECK (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can update their own budgets"
        ON budgets FOR UPDATE USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can delete their own budgets"
        ON budgets FOR DELETE USING (auth.uid() = user_id);
    """
    )

    # Create updated_at trigger for budgets
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_budgets_updated_at()
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
        CREATE TRIGGER update_budgets_updated_at
        BEFORE UPDATE ON budgets
        FOR EACH ROW
        EXECUTE FUNCTION update_budgets_updated_at();
    """
    )

    # Create function to calculate budget spending
    op.execute(
        """
        CREATE OR REPLACE FUNCTION get_budget_spending(
            p_user_id UUID,
            p_category VARCHAR,
            p_start_date DATE,
            p_end_date DATE
        )
        RETURNS NUMERIC AS $$
        DECLARE
            total_spending NUMERIC;
        BEGIN
            SELECT COALESCE(SUM(amount), 0)
            INTO total_spending
            FROM transactions
            WHERE user_id = p_user_id
              AND category = p_category
              AND transaction_date >= p_start_date
              AND transaction_date <= p_end_date
              AND amount < 0;  -- Only expenses (negative amounts)

            RETURN ABS(total_spending);
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """
    )

    # Create function to check all budget alerts for a user
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_budget_alerts(p_user_id UUID)
        RETURNS TABLE(
            budget_id UUID,
            category VARCHAR,
            budget_amount NUMERIC,
            spent_amount NUMERIC,
            percentage INTEGER,
            threshold_exceeded BOOLEAN
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT
                b.id as budget_id,
                b.category,
                b.amount as budget_amount,
                get_budget_spending(
                    b.user_id,
                    b.category,
                    CASE b.period
                        WHEN 'daily' THEN CURRENT_DATE
                        WHEN 'weekly' THEN CURRENT_DATE - INTERVAL '7 days'
                        WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                        WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                    END,
                    CURRENT_DATE
                ) as spent_amount,
                (
                    (get_budget_spending(
                        b.user_id,
                        b.category,
                        CASE b.period
                            WHEN 'daily' THEN CURRENT_DATE
                            WHEN 'weekly' THEN CURRENT_DATE - INTERVAL '7 days'
                            WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                            WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                        END,
                        CURRENT_DATE
                    ) / b.amount) * 100
                )::INTEGER as percentage,
                (
                    (get_budget_spending(
                        b.user_id,
                        b.category,
                        CASE b.period
                            WHEN 'daily' THEN CURRENT_DATE
                            WHEN 'weekly' THEN CURRENT_DATE - INTERVAL '7 days'
                            WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                            WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                        END,
                        CURRENT_DATE
                    ) / b.amount) * 100
                ) >= b.alert_threshold as threshold_exceeded
            FROM budgets b
            WHERE b.user_id = p_user_id
              AND b.is_active = true;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """
    )


def downgrade() -> None:
    # Drop functions
    op.execute("DROP FUNCTION IF EXISTS check_budget_alerts(UUID);")
    op.execute(
        "DROP FUNCTION IF EXISTS get_budget_spending(UUID, VARCHAR, DATE, DATE);"
    )

    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS update_budgets_updated_at ON budgets;")
    op.execute("DROP FUNCTION IF EXISTS update_budgets_updated_at();")

    # Drop RLS policies
    op.execute('DROP POLICY IF EXISTS "Users can delete their own budgets" ON budgets;')
    op.execute('DROP POLICY IF EXISTS "Users can update their own budgets" ON budgets;')
    op.execute('DROP POLICY IF EXISTS "Users can insert their own budgets" ON budgets;')
    op.execute('DROP POLICY IF EXISTS "Users can view their own budgets" ON budgets;')
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on budgets" ON budgets;'
    )

    # Disable RLS
    op.execute("ALTER TABLE budgets DISABLE ROW LEVEL SECURITY;")

    # Drop indexes
    op.drop_index("idx_budgets_user_active")
    op.drop_index("idx_budgets_user_category")
    op.drop_index("idx_budgets_user_id")

    # Drop table
    op.drop_table("budgets")
