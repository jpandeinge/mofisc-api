"""Add RLS policies for daily_challenges and user_challenges tables

Revision ID: 009_challenges_rls
Revises: 008_fix_user_metrics_schema
Create Date: 2025-10-14
"""

from alembic import op

# revision identifiers
revision = "009_challenges_rls"
down_revision = "008_fix_user_metrics_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add INSERT, UPDATE, DELETE policies for daily_challenges and user_challenges tables."""

    # Drop existing policies first to make migration idempotent
    op.execute(
        'DROP POLICY IF EXISTS "Service role can delete daily_challenges" ON daily_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role can update daily_challenges" ON daily_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role can insert daily_challenges" ON daily_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on daily_challenges" ON daily_challenges;'
    )

    # Daily challenges policies - service role has full access for management
    op.execute(
        """
        -- Service role has full access to daily_challenges (for creating/updating challenges)
        CREATE POLICY "Service role has full access on daily_challenges"
        ON daily_challenges USING (true) WITH CHECK (true);

        -- Allow INSERT for daily_challenges (service role)
        CREATE POLICY "Service role can insert daily_challenges"
        ON daily_challenges FOR INSERT WITH CHECK (true);

        -- Allow UPDATE for daily_challenges (service role)
        CREATE POLICY "Service role can update daily_challenges"
        ON daily_challenges FOR UPDATE USING (true) WITH CHECK (true);

        -- Allow DELETE for daily_challenges (service role)
        CREATE POLICY "Service role can delete daily_challenges"
        ON daily_challenges FOR DELETE USING (true);
    """
    )

    # Drop existing user_challenges policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own user_challenges" ON user_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own user_challenges" ON user_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own user_challenges" ON user_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_challenges" ON user_challenges;'
    )

    # User challenges policies - users can view/update their own, service role has full access
    op.execute(
        """
        -- Service role has full access to user_challenges
        CREATE POLICY "Service role has full access on user_challenges"
        ON user_challenges USING (true) WITH CHECK (true);

        -- Users can insert their own user_challenges
        CREATE POLICY "Users can insert their own user_challenges"
        ON user_challenges FOR INSERT WITH CHECK (auth.uid() = user_id);

        -- Users can update their own user_challenges
        CREATE POLICY "Users can update their own user_challenges"
        ON user_challenges FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

        -- Users can delete their own user_challenges
        CREATE POLICY "Users can delete their own user_challenges"
        ON user_challenges FOR DELETE USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    """Remove the RLS policies."""

    # Drop daily_challenges policies
    op.execute(
        'DROP POLICY IF EXISTS "Service role can delete daily_challenges" ON daily_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role can update daily_challenges" ON daily_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role can insert daily_challenges" ON daily_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on daily_challenges" ON daily_challenges;'
    )

    # Drop user_challenges policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete their own user_challenges" ON user_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update their own user_challenges" ON user_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own user_challenges" ON user_challenges;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access on user_challenges" ON user_challenges;'
    )
