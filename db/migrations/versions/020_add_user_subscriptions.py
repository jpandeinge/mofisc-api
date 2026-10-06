"""Add billing_subscriptions table for Dodo Payments integration

Revision ID: 020_add_user_subscriptions
Revises: 019_secure_alembic
Create Date: 2025-01-02

Creates billing_subscriptions table for storing user payment plan information
from Dodo Payments. This is separate from user_subscriptions (migration 007)
which tracks detected recurring transactions from bank statements.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "020_add_user_subscriptions"
down_revision = "019_secure_alembic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create billing_subscriptions table for Dodo Payments.
    Stores user payment plans: Free, Basic, Premium, Enterprise.
    """

    # Drop if exists for clean state
    op.execute("DROP TABLE IF EXISTS billing_subscriptions CASCADE;")

    # Create billing_subscriptions table
    op.execute(
        """
        CREATE TABLE billing_subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
            dodo_customer_id VARCHAR(255),
            dodo_subscription_id VARCHAR(255),
            plan VARCHAR(50) NOT NULL DEFAULT 'free',
            status VARCHAR(50) NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMPTZ,
            current_period_end TIMESTAMPTZ,
            cancel_at_period_end BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_billing_subscriptions_user_id UNIQUE (user_id)
        );
    """
    )

    # Create indexes
    op.execute(
        """
        CREATE INDEX idx_billing_subscriptions_user_id
        ON billing_subscriptions(user_id);
    """
    )
    op.execute(
        """
        CREATE INDEX idx_billing_subscriptions_dodo_customer_id
        ON billing_subscriptions(dodo_customer_id);
    """
    )
    op.execute(
        """
        CREATE INDEX idx_billing_subscriptions_status
        ON billing_subscriptions(status);
    """
    )

    # Create trigger function for updated_at
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_billing_subscriptions_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
    )

    # Create trigger
    op.execute(
        """
        DROP TRIGGER IF EXISTS update_billing_subscriptions_updated_at ON billing_subscriptions;
        CREATE TRIGGER update_billing_subscriptions_updated_at
        BEFORE UPDATE ON billing_subscriptions
        FOR EACH ROW
        EXECUTE FUNCTION update_billing_subscriptions_updated_at();
    """
    )

    # Enable RLS
    op.execute("ALTER TABLE billing_subscriptions ENABLE ROW LEVEL SECURITY;")

    # RLS policy: Service role has full access
    # Uses the SAME pattern as transactions table (migration 003)
    # IMPORTANT: Do NOT use current_setting('role') or TO service_role
    # The Supabase Python client requires USING (true) WITH CHECK (true)
    op.execute(
        """
        CREATE POLICY "Service role has full access" ON billing_subscriptions
        USING (true)
        WITH CHECK (true);
    """
    )

    # RLS policy: Users can view their own billing subscription
    op.execute(
        """
        CREATE POLICY "Users can view own billing" ON billing_subscriptions
        FOR SELECT
        USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    """Remove billing_subscriptions table."""

    op.execute(
        "DROP TRIGGER IF EXISTS update_billing_subscriptions_updated_at ON billing_subscriptions;"
    )
    op.execute("DROP FUNCTION IF EXISTS update_billing_subscriptions_updated_at();")
    op.execute("DROP TABLE IF EXISTS billing_subscriptions CASCADE;")
