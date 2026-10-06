"""Add achievement system

Revision ID: 006_add_achievement_system
Revises: 005_add_calculator_history
Create Date: 2025-04-05
"""

# revision identifiers, used by Alembic.
revision = "006_add_achievement_system"
down_revision = "005_add_calculator_history"
branch_labels = None
depends_on = None

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


def upgrade():
    # Create achievements table
    op.create_table(
        "achievements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, default=1),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("icon", sa.String(), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("trigger_event", sa.String(), nullable=False),
        sa.Column("is_repeatable", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, default=False),
        sa.Column("requires_achievement_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["requires_achievement_id"], ["achievements.id"]),
    )

    # Create user_achievements table
    op.create_table(
        "user_achievements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("achievement_id", UUID(as_uuid=True), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, default=0),
        sa.Column("current_value", sa.Float(), nullable=False, default=0),
        sa.Column("is_completed", sa.Boolean(), nullable=False, default=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"]),
        sa.ForeignKeyConstraint(["achievement_id"], ["achievements.id"]),
        sa.UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),
    )

    # Create streaks table
    op.create_table(
        "user_streaks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("streak_type", sa.String(), nullable=False),
        sa.Column("current_count", sa.Integer(), nullable=False, default=0),
        sa.Column("highest_count", sa.Integer(), nullable=False, default=0),
        sa.Column("last_tracked_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"]),
        sa.UniqueConstraint("user_id", "streak_type", name="uq_user_streak_type"),
    )

    # Create daily_challenges table
    op.create_table(
        "daily_challenges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("challenge_type", sa.String(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    # Create user_challenges table
    op.create_table(
        "user_challenges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("challenge_id", UUID(as_uuid=True), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, default=0),
        sa.Column("is_completed", sa.Boolean(), nullable=False, default=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"]),
        sa.ForeignKeyConstraint(["challenge_id"], ["daily_challenges.id"]),
        sa.UniqueConstraint("user_id", "challenge_id", name="uq_user_challenge"),
    )

    # Create user_points table
    op.create_table(
        "user_points",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False, default=0),
        sa.Column("level", sa.Integer(), nullable=False, default=1),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"]),
        sa.UniqueConstraint("user_id", name="uq_user_points"),
    )

    # Add Row Level Security policies
    op.execute(
        """
        ALTER TABLE achievements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_achievements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_streaks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE daily_challenges ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_challenges ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_points ENABLE ROW LEVEL SECURITY;

        -- Achievements policies
        CREATE POLICY achievements_select_policy ON achievements
        FOR SELECT USING (true);

        -- User achievements policies
        CREATE POLICY user_achievements_select_policy ON user_achievements
        FOR SELECT USING (user_id = auth.uid());

        -- User streaks policies
        CREATE POLICY user_streaks_select_policy ON user_streaks
        FOR SELECT USING (user_id = auth.uid());

        -- Daily challenges policies
        CREATE POLICY daily_challenges_select_policy ON daily_challenges
        FOR SELECT USING (is_active = true);

        -- User challenges policies
        CREATE POLICY user_challenges_select_policy ON user_challenges
        FOR SELECT USING (user_id = auth.uid());

        -- User points policies
        CREATE POLICY user_points_select_policy ON user_points
        FOR SELECT USING (user_id = auth.uid());
    """
    )

    # Add Indexes
    op.create_index("idx_achievements_category", "achievements", ["category"])
    op.create_index("idx_achievements_trigger_event", "achievements", ["trigger_event"])
    op.create_index("idx_user_achievements_user_id", "user_achievements", ["user_id"])
    op.create_index(
        "idx_user_achievements_achievement_id", "user_achievements", ["achievement_id"]
    )
    op.create_index("idx_user_streaks_user_id", "user_streaks", ["user_id"])
    op.create_index("idx_user_streaks_type", "user_streaks", ["streak_type"])
    op.create_index("idx_daily_challenges_type", "daily_challenges", ["challenge_type"])
    op.create_index(
        "idx_daily_challenges_expires_at", "daily_challenges", ["expires_at"]
    )
    op.create_index("idx_user_challenges_user_id", "user_challenges", ["user_id"])
    op.create_index(
        "idx_user_challenges_challenge_id", "user_challenges", ["challenge_id"]
    )
    op.create_index("idx_user_points_user_id", "user_points", ["user_id"])

    # Add trigger functions for achievements system
    op.execute(
        """
        -- Function to update user points when achievements are completed
        CREATE OR REPLACE FUNCTION update_user_points_on_achievement()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true AND (OLD.is_completed = false OR OLD.is_completed IS NULL) THEN
                -- Get achievement points
                DECLARE
                    achievement_points INTEGER;
                BEGIN
                    SELECT points INTO achievement_points
                    FROM achievements
                    WHERE id = NEW.achievement_id;

                    -- Update user points
                    INSERT INTO user_points (user_id, points, level)
                    VALUES (NEW.user_id, achievement_points, 1)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        points = user_points.points + achievement_points,
                        level = GREATEST(1, FLOOR(POWER((user_points.points + achievement_points) / 100.0, 0.5))::INTEGER);
                END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        -- Trigger to update user points when achievements are completed
        CREATE TRIGGER update_points_on_achievement_completion
        AFTER INSERT OR UPDATE OF is_completed ON user_achievements
        FOR EACH ROW
        EXECUTE FUNCTION update_user_points_on_achievement();

        -- Function to update user points when challenges are completed
        CREATE OR REPLACE FUNCTION update_user_points_on_challenge()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true AND (OLD.is_completed = false OR OLD.is_completed IS NULL) THEN
                -- Get challenge points
                DECLARE
                    challenge_points INTEGER;
                BEGIN
                    SELECT points INTO challenge_points
                    FROM daily_challenges
                    WHERE id = NEW.challenge_id;

                    -- Update user points
                    INSERT INTO user_points (user_id, points, level)
                    VALUES (NEW.user_id, challenge_points, 1)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        points = user_points.points + challenge_points,
                        level = GREATEST(1, FLOOR(POWER((user_points.points + challenge_points) / 100.0, 0.5))::INTEGER);
                END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        -- Trigger to update user points when challenges are completed
        CREATE TRIGGER update_points_on_challenge_completion
        AFTER INSERT OR UPDATE OF is_completed ON user_challenges
        FOR EACH ROW
        EXECUTE FUNCTION update_user_points_on_challenge();
    """
    )

    # Insert default achievements
    op.execute(
        """
        INSERT INTO achievements (id, title, description, category, level, points, icon, target_value, trigger_event, is_repeatable, is_hidden)
        VALUES
        -- Savings achievements
        (uuid_generate_v4(), 'First Savings Goal', 'Create your first savings goal', 'savings', 1, 10, 'PiggyBank', 1, 'goal_created', false, false),
        (uuid_generate_v4(), 'Goal Achiever', 'Complete your first savings goal', 'savings', 1, 25, 'Trophy', 1, 'goal_completed', false, false),
        (uuid_generate_v4(), 'Consistent Saver', 'Make 5 contributions to savings goals', 'savings', 2, 15, 'TrendingUp', 5, 'goal_contribution', false, false),
        (uuid_generate_v4(), 'Savings Master', 'Complete 5 savings goals', 'savings', 3, 50, 'Award', 5, 'goal_completed', false, false),

        -- Transactions achievements
        (uuid_generate_v4(), 'Financial Tracker', 'Upload your first bank statement', 'tracking', 1, 10, 'FileText', 1, 'statement_uploaded', false, false),
        (uuid_generate_v4(), 'Budget Conscious', 'Categorize 10 transactions', 'tracking', 1, 15, 'Tag', 10, 'transaction_categorized', false, false),
        (uuid_generate_v4(), 'Transaction Master', 'Track 100 transactions', 'tracking', 2, 25, 'BarChart2', 100, 'transaction_tracked', false, false),
        (uuid_generate_v4(), 'Data Analyst', 'Track transactions for 3 consecutive months', 'tracking', 3, 50, 'Calendar', 3, 'month_streak', false, false),

        -- Spending achievements
        (uuid_generate_v4(), 'Spending Tracker', 'Track your first expense', 'spending', 1, 5, 'DollarSign', 1, 'expense_tracked', false, false),
        (uuid_generate_v4(), 'Category Explorer', 'Have transactions in 5 different categories', 'spending', 2, 20, 'Grid', 5, 'category_diversity', false, false),
        (uuid_generate_v4(), 'Budget Keeper', 'Stay under budget for one month', 'spending', 2, 30, 'Briefcase', 1, 'budget_maintained', false, false),
        (uuid_generate_v4(), 'Spending Detective', 'Identify and reduce spending in your top expense category', 'spending', 3, 40, 'TrendingDown', 1, 'reduced_top_spending', false, false),

        -- Engagement achievements
        (uuid_generate_v4(), 'Welcome Aboard', 'Log in for the first time', 'engagement', 1, 5, 'LogIn', 1, 'first_login', false, false),
        (uuid_generate_v4(), 'Regular User', 'Log in for 7 consecutive days', 'engagement', 2, 15, 'UserCheck', 7, 'login_streak', false, false),
        (uuid_generate_v4(), 'Financial Explorer', 'Use all major features of the app', 'engagement', 2, 25, 'Compass', 1, 'feature_explorer', false, false),
        (uuid_generate_v4(), 'Financial Maestro', 'Reach level 10', 'engagement', 3, 100, 'Star', 10, 'level_reached', false, false),

        -- Financial wellness achievements
        (uuid_generate_v4(), 'Emergency Fund Starter', 'Create an emergency fund goal', 'wellness', 1, 15, 'Shield', 1, 'emergency_fund_created', false, false),
        (uuid_generate_v4(), 'Debt Reducer', 'Track debt reduction progress', 'wellness', 2, 25, 'TrendingDown', 1, 'debt_reduction_tracked', false, false),
        (uuid_generate_v4(), 'Financial Planner', 'Use the loan calculators', 'wellness', 2, 15, 'Calculator', 1, 'calculator_used', false, false),
        (uuid_generate_v4(), 'Financial Freedom', 'Complete your emergency fund goal', 'wellness', 3, 50, 'Unlock', 1, 'emergency_fund_completed', false, false)
    """
    )


def downgrade():
    # Remove triggers
    op.execute(
        """
        DROP TRIGGER IF EXISTS update_points_on_achievement_completion ON user_achievements;
        DROP TRIGGER IF EXISTS update_points_on_challenge_completion ON user_challenges;
        DROP FUNCTION IF EXISTS update_user_points_on_achievement();
        DROP FUNCTION IF EXISTS update_user_points_on_challenge();
    """
    )

    # Drop tables
    op.drop_table("user_points")
    op.drop_table("user_challenges")
    op.drop_table("daily_challenges")
    op.drop_table("user_streaks")
    op.drop_table("user_achievements")
    op.drop_table("achievements")
