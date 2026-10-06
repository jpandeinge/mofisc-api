"""Fix user metrics schema for UserMetricsSupabaseService compatibility

Revision ID: 008_fix_user_metrics_schema
Revises: 007_financial_metrics
Create Date: 2025-09-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "008_fix_user_metrics_schema"
down_revision = "007_financial_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create user_metrics_cache table that UserMetricsSupabaseService uses for caching
    op.create_table(
        "user_metrics_cache",
        sa.Column(
            "id",
            postgresql.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column(
            "calculated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="unique_user_metrics_cache"),
    )

    # 4. Create index for user_metrics_cache
    op.create_index("idx_user_metrics_cache_user_id", "user_metrics_cache", ["user_id"])

    # 5. Enable RLS for user_metrics_cache
    op.execute("ALTER TABLE user_metrics_cache ENABLE ROW LEVEL SECURITY;")

    # 6. Create RLS policies for user_metrics_cache
    op.execute(
        """
        -- Service role has full access
        CREATE POLICY "Service role has full access on user_metrics_cache"
        ON user_metrics_cache USING (true) WITH CHECK (true);

        -- Users can view their own cache
        CREATE POLICY "Users can view their own user_metrics_cache"
        ON user_metrics_cache FOR SELECT USING (auth.uid() = user_id);

        -- Users can insert their own cache
        CREATE POLICY "Users can insert their own user_metrics_cache"
        ON user_metrics_cache FOR INSERT WITH CHECK (auth.uid() = user_id);

        -- Users can update their own cache
        CREATE POLICY "Users can update their own user_metrics_cache"
        ON user_metrics_cache FOR UPDATE USING (auth.uid() = user_id);

        -- Users can delete their own cache
        CREATE POLICY "Users can delete their own user_metrics_cache"
        ON user_metrics_cache FOR DELETE USING (auth.uid() = user_id);
    """
    )

    # 7. Update existing RLS policies for user_achievements, user_points, user_streaks
    # to include INSERT, UPDATE, DELETE policies (they only had SELECT before)

    # User achievements policies
    op.execute(
        """
        CREATE POLICY "Users can insert their own user_achievements"
        ON user_achievements FOR INSERT WITH CHECK (auth.uid() = user_id);

        CREATE POLICY "Users can update their own user_achievements"
        ON user_achievements FOR UPDATE USING (auth.uid() = user_id);

        CREATE POLICY "Users can delete their own user_achievements"
        ON user_achievements FOR DELETE USING (auth.uid() = user_id);

        CREATE POLICY "Service role has full access on user_achievements"
        ON user_achievements USING (true) WITH CHECK (true);
    """
    )

    # User points policies
    op.execute(
        """
        CREATE POLICY "Users can insert their own user_points"
        ON user_points FOR INSERT WITH CHECK (auth.uid() = user_id);

        CREATE POLICY "Users can update their own user_points"
        ON user_points FOR UPDATE USING (auth.uid() = user_id);

        CREATE POLICY "Users can delete their own user_points"
        ON user_points FOR DELETE USING (auth.uid() = user_id);

        CREATE POLICY "Service role has full access on user_points"
        ON user_points USING (true) WITH CHECK (true);
    """
    )

    # User streaks policies
    op.execute(
        """
        CREATE POLICY "Users can insert their own user_streaks"
        ON user_streaks FOR INSERT WITH CHECK (auth.uid() = user_id);

        CREATE POLICY "Users can update their own user_streaks"
        ON user_streaks FOR UPDATE USING (auth.uid() = user_id);

        CREATE POLICY "Users can delete their own user_streaks"
        ON user_streaks FOR DELETE USING (auth.uid() = user_id);

        CREATE POLICY "Service role has full access on user_streaks"
        ON user_streaks USING (true) WITH CHECK (true);
    """
    )

    # 6. Create predefined achievements that UserMetricsSupabaseService uses
    op.execute(
        """
        INSERT INTO achievements (id, title, description, category, level, points, icon, target_value, trigger_event, is_repeatable, is_hidden)
        VALUES
        -- Achievement types that UserMetricsSupabaseService creates
        (uuid_generate_v4(), 'Transaction Milestone', 'Achievement for transaction milestones', 'tracking', 1, 100, 'FileText', 1, 'transaction_milestone', false, false),
        (uuid_generate_v4(), 'Savings Goal', 'Achievement for savings goals', 'savings', 1, 100, 'PiggyBank', 1, 'savings_goal', false, false),
        (uuid_generate_v4(), 'Streak Keeper', 'Achievement for streak keeping', 'engagement', 1, 100, 'TrendingUp', 1, 'streak_keeper', false, false),
        (uuid_generate_v4(), 'Balance Master', 'Achievement for balance mastery', 'financial', 1, 100, 'DollarSign', 1, 'balance_master', false, false)
        ON CONFLICT DO NOTHING;
    """
    )


def downgrade() -> None:
    # Drop RLS policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own user_metrics_cache" ON user_metrics_cache;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own user_metrics_cache" ON user_metrics_cache;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own user_metrics_cache" ON user_metrics_cache;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own user_metrics_cache" ON user_metrics_cache;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_metrics_cache" ON user_metrics_cache;'
    )

    # Drop policies for other tables
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_achievements" ON user_achievements;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own user_achievements" ON user_achievements;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own user_achievements" ON user_achievements;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own user_achievements" ON user_achievements;'
    )

    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_points" ON user_points;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own user_points" ON user_points;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own user_points" ON user_points;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own user_points" ON user_points;'
    )

    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_streaks" ON user_streaks;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own user_streaks" ON user_streaks;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own user_streaks" ON user_streaks;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own user_streaks" ON user_streaks;'
    )

    # Drop user_metrics_cache table
    op.drop_index("idx_user_metrics_cache_user_id")
    op.execute("ALTER TABLE user_metrics_cache DISABLE ROW LEVEL SECURITY;")
    op.drop_table("user_metrics_cache")

    # Remove predefined achievements we added
    op.execute(
        """
        DELETE FROM achievements WHERE trigger_event IN (
            'transaction_milestone', 'savings_goal', 'streak_keeper', 'balance_master'
        );
    """
    )
