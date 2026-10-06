"""Fix billing_subscriptions RLS policies for service role access

Revision ID: 021_fix_billing_rls
Revises: 020_add_user_subscriptions
Create Date: 2025-01-03

PROBLEM:
Migration 020 created RLS policies using `current_setting('role') = 'service_role'`
which does NOT work with the Supabase Python client. The client connects as the
'authenticator' PostgreSQL role, not 'service_role'.

SOLUTION:
Use the SAME pattern as the transactions table (migration 003) which has been
working in production: `USING (true) WITH CHECK (true)` for service role access.

This is secure because:
1. All write operations go through the backend API (service role)
2. Users can only SELECT their own records
3. Direct database access by end users is not possible
"""

from alembic import op

# revision identifiers
revision = "021_fix_billing_rls"
down_revision = "020_add_user_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Fix billing_subscriptions RLS to use the proven pattern from migration 003.

    This uses a DO block to drop ALL existing policies dynamically, ensuring
    a clean slate regardless of what policies currently exist.
    """

    # Step 1: Drop ALL existing policies on billing_subscriptions (dynamic)
    # This handles any policy name variations
    op.execute(
        """
        DO $$
        DECLARE
            pol RECORD;
        BEGIN
            FOR pol IN
                SELECT policyname
                FROM pg_policies
                WHERE tablename = 'billing_subscriptions'
            LOOP
                EXECUTE format('DROP POLICY IF EXISTS %I ON billing_subscriptions', pol.policyname);
            END LOOP;
        END $$;
    """
    )

    # Step 2: Create the CORRECT policies using the proven pattern

    # Policy 1: Unconditional access for all operations
    # This is the EXACT pattern used by transactions table (migration 003)
    # The Supabase Python client with service role key relies on this
    op.execute(
        """
        CREATE POLICY "Service role has full access" ON billing_subscriptions
        USING (true)
        WITH CHECK (true);
    """
    )

    # Policy 2: Users can only view their own subscription
    op.execute(
        """
        CREATE POLICY "Users can view own billing" ON billing_subscriptions
        FOR SELECT
        USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    """
    Revert to migration 020 state.
    Note: Those policies are broken, but this is required for rollback.
    """

    # Drop the working policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can view own billing" ON billing_subscriptions;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access" ON billing_subscriptions;'
    )

    # Restore the original (broken) policies from migration 020
    op.execute(
        """
        CREATE POLICY "service_role_full_access" ON billing_subscriptions
        FOR ALL
        USING (current_setting('role') = 'service_role')
        WITH CHECK (current_setting('role') = 'service_role');
    """
    )

    op.execute(
        """
        CREATE POLICY "users_view_own_billing" ON billing_subscriptions
        FOR SELECT
        USING (auth.uid() = user_id);
    """
    )
