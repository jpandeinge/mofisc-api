# db/migrations/versions/007_create_financial_metrics_tables.py

"""Create financial metrics tables for backend calculations

Revision ID: 007_financial_metrics
Revises: 006_add_achievement_system
Create Date: 2025-06-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "007_financial_metrics"
down_revision = "006_add_achievement_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user_financial_metrics table for storing calculated metrics
    op.create_table(
        "user_financial_metrics",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column(
            "statement_id", sa.String(), nullable=True
        ),  # Can be null for overall metrics
        sa.Column(
            "calculation_date",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        # Financial Health Score components
        sa.Column("financial_health_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("dti_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("savings_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("cash_flow_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("behavior_score", sa.Numeric(5, 2), nullable=False),
        # Raw metrics for calculations
        sa.Column(
            "total_income", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_expenses", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_debt_payments", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "net_cash_flow", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "latest_balance", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        # Transaction counts and patterns
        sa.Column(
            "transaction_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("overdraft_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "expense_volatility", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        # Recommendations and insights
        sa.Column("recommendations", postgresql.JSONB(), server_default="[]"),
        sa.Column("insights", postgresql.JSONB(), server_default="{}"),
        sa.Column("score_interpretation", sa.String(), nullable=True),
        # Metadata
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), onupdate=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create cash_flow_data table for storing daily cash flow calculations
    op.create_table(
        "user_cash_flow_data",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("income", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("expenses", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("net_flow", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column(
            "running_balance", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "transaction_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), onupdate=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="unique_user_date_cash_flow"),
    )

    # Create subscriptions table for storing detected subscriptions
    op.create_table(
        "user_subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("frequency", sa.String(), nullable=False),  # monthly, yearly, weekly
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("description_pattern", sa.String(), nullable=True),
        sa.Column("first_detected", sa.Date(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
        sa.Column(
            "transaction_count", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "confidence_score", sa.Numeric(3, 2), nullable=False, server_default="1.0"
        ),  # 0-1 confidence
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), onupdate=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create category spending analysis table
    op.create_table(
        "user_category_spending",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "total_amount", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "transaction_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "average_amount", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "percentage_of_expenses",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "trend_direction", sa.String(), nullable=True
        ),  # increasing, decreasing, stable
        sa.Column("trend_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), onupdate=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "category",
            "period_start",
            "period_end",
            name="unique_user_category_period",
        ),
    )

    # Create monthly trends table
    op.create_table(
        "user_monthly_trends",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),  # First day of the month
        sa.Column(
            "income_current", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "income_previous", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "income_change_pct", sa.Numeric(5, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "expenses_current", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "expenses_previous", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "expenses_change_pct", sa.Numeric(5, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "balance_current", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "balance_previous", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "balance_change_pct", sa.Numeric(5, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "transaction_count_current",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "transaction_count_previous",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "transaction_count_change_pct",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), onupdate=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "month", name="unique_user_month_trends"),
    )

    # Create indexes for performance
    op.create_index(
        "idx_financial_metrics_user_id", "user_financial_metrics", ["user_id"]
    )
    op.create_index(
        "idx_financial_metrics_statement_id", "user_financial_metrics", ["statement_id"]
    )
    op.create_index(
        "idx_financial_metrics_calculation_date",
        "user_financial_metrics",
        ["calculation_date"],
    )
    op.create_index(
        "idx_financial_metrics_is_current", "user_financial_metrics", ["is_current"]
    )
    op.create_index(
        "idx_financial_metrics_user_current",
        "user_financial_metrics",
        ["user_id", "is_current"],
    )

    op.create_index("idx_cash_flow_user_id", "user_cash_flow_data", ["user_id"])
    op.create_index("idx_cash_flow_date", "user_cash_flow_data", ["date"])
    op.create_index(
        "idx_cash_flow_user_date", "user_cash_flow_data", ["user_id", "date"]
    )

    op.create_index("idx_subscriptions_user_id", "user_subscriptions", ["user_id"])
    op.create_index("idx_subscriptions_is_active", "user_subscriptions", ["is_active"])
    op.create_index(
        "idx_subscriptions_user_active", "user_subscriptions", ["user_id", "is_active"]
    )

    op.create_index(
        "idx_category_spending_user_id", "user_category_spending", ["user_id"]
    )
    op.create_index(
        "idx_category_spending_category", "user_category_spending", ["category"]
    )
    op.create_index(
        "idx_category_spending_period",
        "user_category_spending",
        ["period_start", "period_end"],
    )

    op.create_index("idx_monthly_trends_user_id", "user_monthly_trends", ["user_id"])
    op.create_index("idx_monthly_trends_month", "user_monthly_trends", ["month"])
    op.create_index(
        "idx_monthly_trends_user_month", "user_monthly_trends", ["user_id", "month"]
    )

    # Enable RLS on all tables
    op.execute("ALTER TABLE user_financial_metrics ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_cash_flow_data ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_category_spending ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_monthly_trends ENABLE ROW LEVEL SECURITY;")

    # Create RLS policies for each table
    tables = [
        "user_financial_metrics",
        "user_cash_flow_data",
        "user_subscriptions",
        "user_category_spending",
        "user_monthly_trends",
    ]

    for table in tables:
        # Service role has full access
        op.execute(
            f"""
            CREATE POLICY "Service role has full access on {table}"
            ON {table} USING (true) WITH CHECK (true);
        """
        )

        # Users can view their own data
        op.execute(
            f"""
            CREATE POLICY "Users can view their own {table}"
            ON {table} FOR SELECT USING (auth.uid() = user_id);
        """
        )

        # Users can insert their own data
        op.execute(
            f"""
            CREATE POLICY "Users can insert their own {table}"
            ON {table} FOR INSERT WITH CHECK (auth.uid() = user_id);
        """
        )

        # Users can update their own data
        op.execute(
            f"""
            CREATE POLICY "Users can update their own {table}"
            ON {table} FOR UPDATE USING (auth.uid() = user_id);
        """
        )


def downgrade() -> None:
    # Drop RLS policies for each table
    tables = [
        "user_financial_metrics",
        "user_cash_flow_data",
        "user_subscriptions",
        "user_category_spending",
        "user_monthly_trends",
    ]

    for table in tables:
        op.execute(
            f'DROP POLICY IF EXISTS "Users can update their own {table}" ON {table};'
        )
        op.execute(
            f'DROP POLICY IF EXISTS "Users can insert their own {table}" ON {table};'
        )
        op.execute(
            f'DROP POLICY IF EXISTS "Users can view their own {table}" ON {table};'
        )
        op.execute(
            f'DROP POLICY IF EXISTS "Service role has full access on {table}" ON {table};'
        )
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    # Drop indexes
    op.drop_index("idx_monthly_trends_user_month")
    op.drop_index("idx_monthly_trends_month")
    op.drop_index("idx_monthly_trends_user_id")

    op.drop_index("idx_category_spending_period")
    op.drop_index("idx_category_spending_category")
    op.drop_index("idx_category_spending_user_id")

    op.drop_index("idx_subscriptions_user_active")
    op.drop_index("idx_subscriptions_is_active")
    op.drop_index("idx_subscriptions_user_id")

    op.drop_index("idx_cash_flow_user_date")
    op.drop_index("idx_cash_flow_date")
    op.drop_index("idx_cash_flow_user_id")

    op.drop_index("idx_financial_metrics_user_current")
    op.drop_index("idx_financial_metrics_is_current")
    op.drop_index("idx_financial_metrics_calculation_date")
    op.drop_index("idx_financial_metrics_statement_id")
    op.drop_index("idx_financial_metrics_user_id")

    # Drop tables
    op.drop_table("user_monthly_trends")
    op.drop_table("user_category_spending")
    op.drop_table("user_subscriptions")
    op.drop_table("user_cash_flow_data")
    op.drop_table("user_financial_metrics")
